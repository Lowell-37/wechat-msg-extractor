# Single-Page Wizard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy three-page UI with the approved persistent single-page wizard, preserve valid workflow state across navigation and refresh, and finish the remaining architecture, accessibility, privacy, and performance requirements from the comprehensive redesign specification.

**Architecture:** Keep `app.py` as the Uvicorn composition root while moving session/wizard state, catalog/preview orchestration, export jobs, and HTTP handlers into focused `schemas/`, `services/`, and `routers/` packages. Render every business fragment with Jinja, use htmx for workspace replacement, and keep a small local JavaScript controller for browser history, focus, and export progress. Existing database, parsing, matching, voice, AI, and Excel modules remain the business implementations behind the new services.

**Tech Stack:** Python 3.11–3.13, FastAPI, Jinja2, htmx 2, vanilla JavaScript, CSS, pytest, Ruff, openpyxl.

## Global Constraints

- Preserve the existing task-message format, group-to-Sheet meaning, and primary Excel column structure.
- The wizard has exactly three user-visible steps: connect, select data, preview/export; step two is one vertical main content region with no right sidebar.
- The page shell is persistent; htmx replaces `#wizard-workspace` without a full reload.
- Completed steps are returnable, the active step is highlighted, and future steps are server-enforced as locked.
- A successful operation enables the next action but never advances without the user's click.
- Returning or refreshing preserves still-valid connection, group, dates, Sheet, preview, output path, and export options.
- Changing group, date, or Sheet invalidates preview/export state; revisiting without a change preserves it.
- All dynamic HTML is Jinja-rendered and autoescaped; route code must not concatenate business HTML.
- AI and voice remain off by default and require an explicit privacy acknowledgement describing data sent externally.
- Default binding stays `127.0.0.1`; output paths remain confined by `resolve_output_path`.
- Desktop content width is 840–960px; narrow screens use a single column and keep the action bar visible.
- Every behavior change follows RED → GREEN → refactor, with focused tests before the full suite.

---

### Task 1: Typed Wizard State and Transition Rules

**Files:**
- Create: `schemas/__init__.py`
- Create: `schemas/wizard.py`
- Create: `services/__init__.py`
- Create: `services/wizard.py`
- Create: `tests/test_wizard_state.py`
- Modify: `app.py`

**Interfaces:**
- Produces: `WizardStep`, `StepStatus`, `WizardSelection`, `WizardState`.
- Produces: `get_wizard(state)`, `request_step(wizard, requested)`, `mark_connected(wizard)`, `update_selection(wizard, ...)`, `store_preview(wizard, tasks)`, and `step_statuses(wizard)`.
- `app._new_session_state()` stores the typed object under `state["wizard"]` while retaining database resources in the session dictionary.

- [ ] **Step 1: Write failing state-transition tests**

```python
def test_future_step_is_redirected_to_latest_available_step():
    wizard = WizardState()
    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.CONNECT

def test_unchanged_selection_preserves_preview_but_changed_date_invalidates_it():
    wizard = WizardState()
    mark_connected(wizard)
    update_selection(wizard, "room-1", "项目群", date(2026, 8, 1), date(2026, 8, 2), "项目群")
    store_preview(wizard, [object()])
    update_selection(wizard, "room-1", "项目群", date(2026, 8, 1), date(2026, 8, 2), "项目群")
    assert wizard.preview_ready is True
    update_selection(wizard, "room-1", "项目群", date(2026, 8, 1), date(2026, 8, 3), "项目群")
    assert wizard.preview_ready is False
```

- [ ] **Step 2: Run RED**

Run: `py -m pytest tests/test_wizard_state.py -q`

Expected: collection fails because `schemas.wizard` and `services.wizard` do not exist.

- [ ] **Step 3: Implement the typed state and transition functions**

```python
class WizardStep(IntEnum):
    CONNECT = 1
    SELECT = 2
    PREVIEW = 3

@dataclass
class WizardState:
    active_step: WizardStep = WizardStep.CONNECT
    connected: bool = False
    selection: WizardSelection = field(default_factory=WizardSelection)
    preview_tasks: list[Any] = field(default_factory=list)
    preview_ready: bool = False
    output_path: str = ""
    enable_ai: bool = False
    enable_voice: bool = False
    privacy_acknowledged: bool = False
```

`update_selection` compares a tuple of group ID/name, dates, and Sheet before assignment; only a changed tuple clears preview tasks and export-derived state.

- [ ] **Step 4: Run GREEN and regression tests**

Run: `py -m pytest tests/test_wizard_state.py tests/test_lifecycle.py tests/test_app_routes.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add schemas services tests/test_wizard_state.py app.py
git commit -m "feat: add persistent wizard state model"
```

---

### Task 2: Session Catalog Cache and Workflow Orchestration

**Files:**
- Create: `schemas/catalog.py`
- Create: `services/catalog.py`
- Create: `services/preview.py`
- Create: `tests/test_catalog_service.py`
- Create: `tests/test_preview_service.py`
- Modify: `app.py`

**Interfaces:**
- Produces: `ChatroomOption(chat_id, display_name, last_message_at, message_count, suggested_sheet)`.
- Produces: `load_catalog(state, excel_path)`, `filter_catalog(state, query)`, and `build_preview(state, group_id, group_name, start_date, end_date, sheet_name)`.
- Consumes: Task 1 `WizardState` and transition functions.
- Catalog data is cached under `state["chatroom_catalog"]`; search performs no database or workbook reads after the initial load.

- [ ] **Step 1: Write failing cache and preview tests**

```python
def test_filter_catalog_reuses_loaded_catalog_without_requerying_database(catalog_state):
    first = load_catalog(catalog_state, catalog_state["template_path"])
    second = filter_catalog(catalog_state, "项目")
    assert catalog_state["database"].query_count == 1
    assert second == [first[0]]

def test_build_preview_persists_valid_selection_and_grouped_tasks(preview_state):
    result = build_preview(preview_state, "room-1", "项目群", "2026-08-01", "2026-08-02", "项目群")
    assert result.summary.group_name == "项目群"
    assert preview_state["wizard"].preview_ready is True
    assert preview_state["wizard"].preview_tasks == result.tasks
```

- [ ] **Step 2: Run RED**

Run: `py -m pytest tests/test_catalog_service.py tests/test_preview_service.py -q`

Expected: imports fail because the services do not exist.

- [ ] **Step 3: Implement cached catalog and preview orchestration**

`load_catalog` opens `ExcelWriter` once, copies Sheet names, closes it in `finally`, calls the existing chatroom query once, and stores immutable catalog records. `filter_catalog` applies case-insensitive matching to cached `chat_id` and `display_name`. `build_preview` calls existing date/Sheet validation, fetches and parses messages through `asyncio.to_thread` at the route boundary, updates Task 1 state only after validation succeeds, and returns a Jinja-ready result object.

- [ ] **Step 4: Run GREEN and focused regressions**

Run: `py -m pytest tests/test_catalog_service.py tests/test_preview_service.py tests/test_chatrooms.py tests/test_validation.py -q`

Expected: all selected tests pass and the cache test observes one underlying query.

- [ ] **Step 5: Commit**

```powershell
git add schemas/catalog.py services/catalog.py services/preview.py tests/test_catalog_service.py tests/test_preview_service.py app.py
git commit -m "feat: cache wizard catalog and preview state"
```

---

### Task 3: FastAPI Composition, Routers, and Jinja Fragment Boundary

**Files:**
- Create: `routers/__init__.py`
- Create: `routers/wizard.py`
- Create: `routers/actions.py`
- Create: `services/session.py`
- Create: `services/export.py`
- Create: `tests/test_app_composition.py`
- Modify: `app.py`
- Modify: `tests/test_lifecycle.py`
- Modify: `tests/test_app_routes.py`
- Delete: `core/db_decryptor.py`
- Delete: `core/key_extractor.py`
- Delete: `core/message_fetcher.py`
- Delete: `tests/test_message_fetcher.py`

**Interfaces:**
- `app.py` exports `app`, `config`, and compatibility aliases used by existing tests, but contains no business route implementation.
- `routers.wizard.router` owns `/`, `/wizard/{step}`, and `/wizard/{step}/partial`.
- `routers.actions.router` owns connection, catalog, preview, export, and progress endpoints.
- `services.session` owns session creation, TTL/capacity eviction, disposal, and shutdown.
- `services.export` owns immutable job creation, scheduling, worker execution, progress ownership, and shutdown.

- [ ] **Step 1: Write failing composition and boundary tests**

```python
def test_future_wizard_route_redirects_to_latest_accessible_step(client):
    response = client.get("/wizard/3", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/wizard/1"

def test_partial_route_returns_fragment_not_document(client):
    response = client.get("/wizard/1/partial", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "<!DOCTYPE html>" not in response.text
    assert 'id="wizard-workspace"' in response.text
```

- [ ] **Step 2: Run RED**

Run: `py -m pytest tests/test_app_composition.py -q`

Expected: `/wizard/1` routes return 404.

- [ ] **Step 3: Extract services and register routers**

Create `create_app()` in `app.py`, mount `/static`, initialize templates, include both routers, and register `shutdown_resources`. Move existing functions without behavior changes first; route handlers must call services and return `TemplateResponse` or redirects. Preserve `/step/2` and `/step/3` as 303 redirects to `/wizard/2` and `/wizard/3` for saved links.

The production flow already uses `core.connection`/`core.dbutils`; remove the unreferenced legacy `DBDecryptor`, `KeyExtractor`, and `MessageFetcher` path and its tests so production and tests no longer exercise competing database stacks.

- [ ] **Step 4: Run GREEN and all route/lifecycle regressions**

Run: `py -m pytest tests/test_app_composition.py tests/test_app_routes.py tests/test_lifecycle.py tests/test_progress.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app.py routers services/session.py services/export.py tests/test_app_composition.py tests/test_app_routes.py tests/test_lifecycle.py
git commit -m "refactor: split wizard routes and services"
```

---

### Task 4: Persistent Shell and Connection Step

**Files:**
- Create: `templates/wizard.html`
- Create: `templates/components/stepper.html`
- Create: `templates/components/status_message.html`
- Create: `templates/components/wizard_actions.html`
- Create: `templates/steps/connect.html`
- Create: `static/wizard.js`
- Modify: `templates/base.html`
- Modify: `static/style.css`
- Modify: `routers/wizard.py`
- Modify: `routers/actions.py`
- Create: `tests/test_wizard_templates.py`

**Interfaces:**
- Full routes render `wizard.html`; partial routes render the matching `templates/steps/*.html` inside a root `#wizard-workspace` element.
- Every htmx navigation response sets `HX-Push-Url` to `/wizard/{step}`.
- Connection actions render `steps/connect.html` with an updated view model; they never concatenate HTML.

- [ ] **Step 1: Write failing shell, escaping, and connection tests**

```python
def test_wizard_shell_has_persistent_stepper_workspace_and_action_bar(client):
    response = client.get("/")
    assert 'id="wizard-stepper"' in response.text
    assert 'id="wizard-workspace"' in response.text
    assert 'class="wizard-actions"' in response.text

def test_connection_error_is_template_escaped(client, monkeypatch):
    monkeypatch.setattr(actions, "connect_wechat", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("<script>alert(1)</script>")))
    response = client.post("/api/key/validate", data={"key": "ab" * 32})
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
```

- [ ] **Step 2: Run RED**

Run: `py -m pytest tests/test_wizard_templates.py -q`

Expected: shell selectors are absent.

- [ ] **Step 3: Implement the shell and connection view**

Use a compact brand bar, local-processing status, labeled environment checklist, primary “连接并继续” operation, and a closed `<details>` block for manual key input. Replace the unconditional “数据不上传” footer with “数据库与 Excel 默认仅在本机处理；启用 AI 或语音时会先征得确认。” Add visible focus rings, reduced-motion support, non-color icons/text, and an `aria-live="polite"` status region.

- [ ] **Step 4: Run GREEN and accessibility template checks**

Run: `py -m pytest tests/test_wizard_templates.py tests/test_app_routes.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add templates static routers tests/test_wizard_templates.py
git commit -m "feat: build persistent wizard shell"
```

---

### Task 5: Unified Select-Data Step and Returnable Navigation

**Files:**
- Create: `templates/steps/select.html`
- Create: `templates/components/chatroom_rows.html`
- Modify: `routers/wizard.py`
- Modify: `routers/actions.py`
- Modify: `services/catalog.py`
- Modify: `static/wizard.js`
- Modify: `static/style.css`
- Create: `tests/test_select_step.py`
- Modify: `tests/test_wizard_state.py`

**Interfaces:**
- `/api/catalog` returns `components/chatroom_rows.html` from cached records.
- `/api/selection` validates and stores group/date/Sheet, then re-renders the select step with an enabled or disabled next action.
- `/api/preview` creates preview state and returns the preview partial with `HX-Push-Url: /wizard/3`.

- [ ] **Step 1: Write failing selection and navigation tests**

```python
def test_select_step_keeps_filters_list_summary_and_actions_in_one_region(connected_client):
    response = connected_client.get("/wizard/2")
    assert 'id="wizard-workspace"' in response.text
    assert 'name="start_date"' in response.text
    assert 'id="chatroom-list"' in response.text
    assert 'id="selection-summary"' in response.text
    assert "<aside" not in response.text

def test_invalid_date_keeps_submitted_values_and_disables_preview(connected_client):
    response = connected_client.post("/api/selection", data={"group_id": "room-1", "group_name": "项目群", "start_date": "2026-08-03", "end_date": "2026-08-01", "sheet_name": "项目群"})
    assert response.status_code == 422
    assert 'value="2026-08-03"' in response.text
    assert "开始日期不能晚于结束日期" in response.text
    assert 'data-next-enabled="false"' in response.text
```

- [ ] **Step 2: Run RED**

Run: `py -m pytest tests/test_select_step.py -q`

Expected: `/api/selection` is missing and the legacy page violates the layout assertions.

- [ ] **Step 3: Implement the single-region selection view**

Render search, labeled date inputs, responsive group rows, per-row Sheet select, message metadata, a one-line summary, and sticky back/next actions. Rows are keyboard-selectable native radios. Search uses `hx-get="/api/catalog"`, includes the current selection, and filters only Task 2 cache. Stepper and back actions request partials; `wizard.js` restores focus to the step heading and handles `popstate` by fetching the corresponding partial.

- [ ] **Step 4: Run GREEN and navigation regressions**

Run: `py -m pytest tests/test_select_step.py tests/test_wizard_state.py tests/test_catalog_service.py tests/test_app_composition.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add templates/steps/select.html templates/components/chatroom_rows.html routers services/catalog.py static tests/test_select_step.py tests/test_wizard_state.py
git commit -m "feat: add unified selection workflow"
```

---

### Task 6: Preview, Privacy Consent, Export Progress, and Retry

**Files:**
- Create: `templates/steps/preview.html`
- Create: `templates/components/task_rows.html`
- Create: `templates/components/export_progress.html`
- Modify: `routers/actions.py`
- Modify: `services/export.py`
- Modify: `services/wizard.py`
- Modify: `static/wizard.js`
- Modify: `static/style.css`
- Create: `tests/test_preview_export_step.py`
- Modify: `tests/test_app_routes.py`
- Modify: `tests/test_progress.py`

**Interfaces:**
- Preview tasks have stable task IDs and selected IDs are submitted as repeated `task_id` values.
- `/api/export` returns `export_progress.html` with the job ID and HTTP 202; duplicate jobs return the same fragment with HTTP 409.
- `/api/progress/stream?job_id=...` remains JSON SSE; `wizard.js` updates `role="progressbar"` and preserves the form on failure.

- [ ] **Step 1: Write failing preview/privacy/retry tests**

```python
def test_external_options_require_acknowledgement(preview_client):
    response = preview_client.post("/api/export", data={"sheet_name": "项目群", "enable_ai": "true", "output_path": "result.xlsx"})
    assert response.status_code == 422
    assert "确认可能发送到外部服务" in response.text

def test_export_failure_fragment_preserves_selection_and_offers_retry(preview_client, failing_export):
    response = failing_export(preview_client)
    assert 'aria-valuenow="0"' in response.text
    assert 'data-export-state="failed"' in response.text
    assert "重试导出" in response.text
    assert 'name="task_id"' in response.text
```

- [ ] **Step 2: Run RED**

Run: `py -m pytest tests/test_preview_export_step.py -q`

Expected: privacy validation and retry fragment assertions fail.

- [ ] **Step 3: Implement preview and export experience**

Render summary chips for group/date/Sheet/count, date-grouped checkable tasks, output path, and a collapsed external-processing section. The acknowledgement checkbox is required only when AI or voice is enabled. Start export once, disable the submit button while active, update phase/percent/message from JSON SSE using `textContent`, and on failure restore controls without clearing wizard state. A completed event shows the resolved output path and retains a “返回修改” action.

- [ ] **Step 4: Run GREEN and export regressions**

Run: `py -m pytest tests/test_preview_export_step.py tests/test_app_routes.py tests/test_progress.py tests/test_excel_writer.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add templates/steps/preview.html templates/components/task_rows.html templates/components/export_progress.html routers/actions.py services/export.py services/wizard.py static tests
git commit -m "feat: redesign preview and export feedback"
```

---

### Task 7: Non-Blocking Boundaries, Responsive Browser Flow, and Final Cleanup

**Files:**
- Modify: `services/catalog.py`
- Modify: `services/preview.py`
- Modify: `services/export.py`
- Modify: `routers/actions.py`
- Modify: `core/dbutils.py`
- Modify: `core/voice.py`
- Modify: `core/ai_analyzer.py`
- Modify: `static/style.css`
- Modify: `static/wizard.js`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_dbutils.py`
- Create: `tests/test_async_boundaries.py`
- Create: `tests/test_browser_contract.py`
- Create: `tests/test_external_processing.py`

**Interfaces:**
- Scanner, database discovery/decryption, catalog query, message query/parsing, voice/SILK work, and Excel writes cross `asyncio.to_thread` or an existing background task boundary.
- Browser contract covers forward, back, refresh restore, changed-selection invalidation, progress failure retry, narrow layout, and focus/ARIA behavior.

- [ ] **Step 1: Write failing async and browser-contract tests**

```python
@pytest.mark.anyio
async def test_catalog_route_moves_blocking_load_off_event_loop(monkeypatch, connected_request):
    calls = []
    async def capture(func, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", capture)
    await catalog_route(connected_request)
    assert "load_catalog" in calls

def test_responsive_css_keeps_actions_visible_and_collapses_rows():
    css = Path("static/style.css").read_text(encoding="utf-8")
    assert "position: sticky" in css
    assert "@media (max-width: 720px)" in css

def test_decrypt_reads_one_database_page_at_a_time(encrypted_database, tracked_open):
    assert decrypt_db_raw(encrypted_database.key, encrypted_database.path, encrypted_database.output)
    assert tracked_open.maximum_read_size == DEFAULT_PAGESIZE
```

- [ ] **Step 2: Run RED**

Run: `py -m pytest tests/test_async_boundaries.py tests/test_browser_contract.py -q`

Expected: blocking boundary and final responsive contract assertions fail.

- [ ] **Step 3: Complete non-blocking and documentation changes**

Move every identified blocking entry point behind `asyncio.to_thread` while keeping export work in the existing task registry. Rewrite `decrypt_db_raw` to read, validate, decrypt, and write one `DEFAULT_PAGESIZE` page at a time instead of calling an unbounded `read()`. Add finite AI/voice timeouts, two retries for transient HTTP failures, and bounded concurrency using one shared semaphore in `services/export.py`; preserve visible warning events on degradation. Add `mypy>=1.17,<2` to the dev extra, type-check `app.py`, `routers`, `services`, and `schemas`, and add the command to CI. Document `py app.py`, the three-step workflow, privacy acknowledgement, `py -m pytest -q`, `py -m ruff check .`, and `py -m mypy app.py routers services schemas`.

- [ ] **Step 4: Run full automated verification**

Run: `py -m pytest -q`

Run: `py -m ruff check .`

Run: `py -m mypy app.py routers services schemas`

Expected: the full suite passes, Ruff reports `All checks passed!`, and mypy reports `Success: no issues found`.

- [ ] **Step 5: Run browser acceptance**

Start the server from this branch and verify at desktop width and 390px width: shell remains fixed; step one enables but does not auto-advance; step two is one column; back/forward and refresh restore state; changed selection invalidates preview; an export failure preserves choices and offers retry; labels, focus rings, step status text, and `aria-live` are present. Save acceptance results in the SDD task report; do not add screenshots to Git.

- [ ] **Step 6: Commit**

```powershell
git add core services routers static README.md AGENTS.md pyproject.toml .github/workflows/ci.yml tests
git commit -m "perf: finish responsive nonblocking wizard"
```

---

## Final Verification

- Run `py -m pytest -q` from a fresh process.
- Run `py -m ruff check .` from a fresh process.
- Run `py -m mypy app.py routers services schemas` from a fresh process.
- Run `git diff --check <merge-base>..HEAD`.
- Perform the desktop and 390px browser acceptance flow against the final branch.
- Request a whole-branch code review against this plan and the comprehensive redesign specification; fix every Critical or Important finding and re-review the fix range.
- Use `superpowers:finishing-a-development-branch` to merge the approved branch into `main`, rerun the full gate on merged `main`, restart the local server from the merged checkout, and verify `/` returns HTTP 200.

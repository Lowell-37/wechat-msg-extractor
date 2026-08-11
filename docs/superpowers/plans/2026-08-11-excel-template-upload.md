# Excel Template Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user upload an `.xlsx` template from the connection step and persist the validated server-side copy as the local default.

**Architecture:** A focused template-storage service validates and atomically activates uploads. The action router owns multipart HTTP handling, while the existing connection view model exposes the active template. Session-derived catalog and preview state are invalidated after activation.

**Tech Stack:** Python 3.11–3.13, FastAPI multipart uploads, openpyxl, PyYAML, Jinja2/htmx, pytest, Ruff.

## Global Constraints

- Accept `.xlsx` only, with a 20 MiB maximum upload size.
- Save templates under Git-ignored `local/templates/` using server-generated names.
- Persist only the server-side path in local `config.yaml`; preserve unrelated YAML settings.
- Never replace the active template or in-memory configuration until validation and configuration persistence succeed.
- Invalidate workbook-derived state for all sessions after activation.

---

### Task 1: Validated Template Storage

**Files:**
- Create: `services/template_store.py`
- Create: `tests/test_template_store.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `activate_template(upload: BinaryIO, filename: str, *, base_dir: Path, config_path: Path, config: AppConfig, max_bytes: int = 20 * 1024 * 1024) -> TemplateActivation`
- Produces: `TemplateActivation(path: str, filename: str, sheet_names: tuple[str, ...])`
- Raises: `TemplateUploadError` with safe user-facing messages.

- [ ] **Step 1: Write failing storage tests**

```python
def test_activate_template_persists_valid_upload(tmp_path):
    config = AppConfig()
    result = activate_template(
        io.BytesIO(valid_workbook_bytes("项目群")),
        "../../任务.xlsx",
        base_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        config=config,
    )
    assert result.sheet_names == ("项目群",)
    assert Path(result.path).parent == tmp_path / "local" / "templates"
    assert AppConfig.from_yaml(str(tmp_path / "config.yaml")).excel.template_path == result.path
```

Add literal behavior tests for `.xlsm`, 20 MiB overflow, corrupt bytes, no visible worksheet, and a forced config-write failure preserving `config.excel.template_path`.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_template_store.py -q`
Expected: collection fails because `services.template_store` does not exist.

- [ ] **Step 3: Implement validation and atomic activation**

```python
@dataclass(frozen=True)
class TemplateActivation:
    path: str
    filename: str
    sheet_names: tuple[str, ...]

def activate_template(upload, filename, *, base_dir, config_path, config, max_bytes=20 * 1024 * 1024):
    if Path(filename).suffix.lower() != ".xlsx":
        raise TemplateUploadError("请选择 .xlsx 文件")
    template_dir = base_dir / "local" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = _stream_limited(upload, template_dir, max_bytes)
    sheet_names = _validate_workbook(temporary_path)
    destination = template_dir / f"{uuid.uuid4().hex}.xlsx"
    old_path = config.excel.template_path
    try:
        os.replace(temporary_path, destination)
        _persist_template_path(config_path, str(destination.resolve()))
    except Exception:
        destination.unlink(missing_ok=True)
        config.excel.template_path = old_path
        raise
    config.excel.template_path = str(destination.resolve())
    return TemplateActivation(str(destination.resolve()), Path(filename).name, sheet_names)
```

- [ ] **Step 4: Run GREEN and focused regression**

Run: `python -m pytest tests/test_template_store.py tests/test_catalog_service.py tests/test_excel_writer.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add .gitignore services/template_store.py tests/test_template_store.py
git commit -m "feat: validate and persist Excel templates"
```

### Task 2: Wizard Upload Flow

**Files:**
- Modify: `routers/actions.py`
- Modify: `routers/wizard.py`
- Modify: `services/wizard.py`
- Modify: `templates/steps/connect.html`
- Modify: `static/css/app.css`
- Modify: `tests/test_wizard_templates.py`
- Modify: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: the Task 1 `activate_template` interface and its `TemplateActivation` result.
- Produces: `POST /api/template` multipart endpoint returning the updated connection-step fragment.
- Produces: `invalidate_workbook_state(state: MutableMapping[str, Any]) -> None`.

- [ ] **Step 1: Write failing route and template tests**

```python
def test_template_upload_activates_workbook_and_invalidates_sessions(client):
    response = client.post(
        "/api/template",
        files={"template": ("任务.xlsx", valid_workbook_bytes("项目群"), XLSX_MIME)},
    )
    assert response.status_code == 200
    assert "任务.xlsx" in response.text
    assert "1 个可用 Sheet" in response.text
    assert all(state["chatroom_catalog"] is None for state in session_state.values())
```

Also assert the full connection page contains `type="file"`, `accept=".xlsx"`, `enctype="multipart/form-data"`, and an invalid upload returns 400 while showing the prior template.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_app_routes.py tests/test_wizard_templates.py -q`
Expected: upload route is 404 and file-picker assertions fail.

- [ ] **Step 3: Implement the route, view model, and file-picker card**

```python
@router.post("/api/template")
async def upload_template(request: Request, template: UploadFile = File(...)):
    session_id, state = get_session(request)
    try:
        activation = activate_template(
            template.file,
            template.filename or "",
            base_dir=request.app.state.base_dir,
            config_path=request.app.state.config_path,
            config=request.app.state.config,
        )
    except TemplateUploadError as exc:
        response = _connection_template(
            request,
            state,
            template_status={"tone": "error", "message": str(exc)},
            status_code=400,
        )
        return _with_session_cookie(request, response, session_id)
    for current_state in session_state.values():
        invalidate_workbook_state(current_state)
    response = _connection_template(
        request,
        state,
        template_status={
            "tone": "success",
            "message": f"已启用 {activation.filename}，{len(activation.sheet_names)} 个可用 Sheet。",
        },
    )
    return _with_session_cookie(request, response, session_id)
```

Expose the active basename and existence status from `build_connection_view_model`; render the upload as an htmx multipart form targeting `#wizard-workspace`. Add only the CSS needed for the template card and native file input.

- [ ] **Step 4: Run GREEN and full verification**

Run: `python -m pytest tests/test_app_routes.py tests/test_wizard_templates.py tests/test_select_step.py tests/test_preview_export_step.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: all pass.

Run: `python -m ruff check .`
Expected: `All checks passed!`

Compile every Jinja template using the configured environment and perform a browser upload check on `http://127.0.0.1:8888`.

- [ ] **Step 5: Commit and push**

```powershell
git add routers/actions.py routers/wizard.py templates/steps/connect.html static/css/app.css tests/test_app_routes.py tests/test_wizard_templates.py
git commit -m "feat: select Excel templates from the wizard"
git push origin main
```

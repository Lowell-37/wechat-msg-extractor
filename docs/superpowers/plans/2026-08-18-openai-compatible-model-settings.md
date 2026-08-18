# OpenAI-Compatible Model Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure model-settings page that configures, verifies, and reliably uses any OpenAI-compatible chat-completions service during Excel export.

**Architecture:** A focused settings service persists non-secret profile data under `local/settings/` and the API Key as a machine-scope DPAPI blob under `local/secrets/`. A generic OpenAI-compatible analyzer consumes immutable verified settings snapshots. A dedicated router/template exposes configuration while the export boundary rejects unavailable AI instead of silently skipping it.

**Tech Stack:** Python 3.11–3.13, FastAPI, Jinja2, HTMX, httpx, Windows DPAPI through the existing credential store, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-18-openai-compatible-model-settings-design.md`

## Global Constraints

- Never return, render, log, or commit an API Key or decrypted credential.
- Store model API Keys only as machine-scope Windows DPAPI ciphertext.
- Persist local model settings and secrets only under Git-ignored `local/` paths.
- Permit HTTPS endpoints and loopback HTTP endpoints; reject other plaintext HTTP endpoints, URL credentials, query strings, and fragments.
- Preserve DeepSeek defaults: display name `DeepSeek`, API base `https://api.deepseek.com`, model `deepseek-chat`.
- A requested AI export must fail clearly when the model is disabled, incomplete, unverified, or unavailable; never silently copy context as AI output.
- Capture immutable settings at job creation so later settings edits cannot alter a running export.
- Use the repository `.venv` Python for every test and lint command.

---

### Task 1: Persistent Model Profile and Secret Boundary

**Files:**
- Create: `services/model_settings.py`
- Create: `tests/test_model_settings.py`
- Modify: `services/credential_store.py`
- Modify: `tests/test_credential_store.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `CredentialStore(path: Path, protector: Protector | None = None)`.
- Produces: immutable `ModelProfile(provider_name: str, api_base: str, model: str, enabled: bool, verified: bool = False)`.
- Produces: immutable `ResolvedModelProfile(profile: ModelProfile, api_key: str)`.
- Produces: `ModelSettingsStore(path: Path).load() -> ModelProfile` and `.save(profile: ModelProfile) -> None`.
- Produces: `ModelSettingsService(store, credential_store, tester)` with `.status()`, async `.save_and_test(...)`, and `.resolved_profile()`.
- Produces: `CredentialStore.model_api_key() -> CredentialStore`.
- Produces: `CredentialStore.delete() -> None` for compensating rollback of a newly staged credential.

- [ ] **Step 1: Write failing persistence and validation tests**

Add tests demonstrating the public contract:

```python
def test_defaults_are_deepseek_and_not_ready(tmp_path):
    service = model_service(tmp_path)
    status = service.status()
    assert status.profile == ModelProfile(
        provider_name="DeepSeek",
        api_base="https://api.deepseek.com",
        model="deepseek-chat",
        enabled=True,
        verified=False,
    )
    assert status.has_api_key is False
    assert status.ready is False


def test_save_and_test_commits_profile_and_encrypted_key(tmp_path):
    tested = []
    async def tester(settings):
        tested.append(settings)
    service, protector = model_service(tmp_path, tester=tester)
    asyncio.run(
        service.save_and_test(
            provider_name="Local Model",
            api_base="http://127.0.0.1:11434",
            model="qwen2.5",
            enabled=True,
            api_key="private-key",
        )
    )
    assert tested[0].api_key == "private-key"
    assert b"private-key" not in service.credential_store.path.read_bytes()
    assert service.status().ready is True


@pytest.mark.parametrize(
    "url",
    ["http://models.example.com", "ftp://localhost/model", "https://u:p@example.com", "https://example.com?a=1"],
)
def test_rejects_unsafe_api_base(url, tmp_path):
    service = model_service(tmp_path)
    with pytest.raises(ModelSettingsError):
        asyncio.run(service.save_and_test("Provider", url, "model", True, "key"))
```

Also cover blank provider/model, existing-Key reuse, disabling without a network test, failed tests preserving the prior profile/Key, settings-write rollback, corrupt JSON, and secret-free errors.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_settings.py tests/test_credential_store.py -q -p no:cacheprovider --basetemp=.test-tmp-model-settings-red
```

Expected: collection fails because `services.model_settings` and `CredentialStore.model_api_key` do not exist.

- [ ] **Step 3: Implement the minimal settings service**

Use frozen dataclasses and dependency injection:

```python
@dataclass(frozen=True)
class ModelProfile:
    provider_name: str = "DeepSeek"
    api_base: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    enabled: bool = True
    verified: bool = False


@dataclass(frozen=True)
class ResolvedModelProfile:
    profile: ModelProfile
    api_key: str


class ModelSettingsService:
    async def save_and_test(self, provider_name, api_base, model, enabled, api_key=""):
        proposed = validate_profile(provider_name, api_base, model, enabled)
        if not enabled:
            self.store.save(replace(proposed, verified=False))
            return self.status()
        resolved_key = api_key.strip() or self.credential_store.load()
        if not resolved_key:
            raise ModelSettingsError("请填写 API Key")
        await self.tester(ResolvedModelProfile(proposed, resolved_key))
        self._commit_verified(proposed, api_key.strip())
        return self.status()
```

Implement JSON writes with `tempfile.mkstemp`, flush, `os.fsync`, and `os.replace`. `_commit_verified` must snapshot the prior profile and credential, restore them on persistence failure (using `CredentialStore.delete()` when there was no prior Key), and never place either secret value in an exception. Add `local/settings/` to `.gitignore` and return only `has_api_key: bool` through status.

- [ ] **Step 4: Run GREEN and lint**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_settings.py tests/test_credential_store.py -q -p no:cacheprovider --basetemp=.test-tmp-model-settings-green
.\.venv\Scripts\python.exe -m ruff check services/model_settings.py services/credential_store.py tests/test_model_settings.py tests/test_credential_store.py
```

Expected: all tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit**

```powershell
git add .gitignore services/model_settings.py services/credential_store.py tests/test_model_settings.py tests/test_credential_store.py
git commit -m "feat: persist verified model settings"
```

---

### Task 2: Generic OpenAI-Compatible Analyzer

**Files:**
- Modify: `core/ai_analyzer.py`
- Create: `tests/test_ai_analyzer.py`

**Interfaces:**
- Consumes: `ResolvedModelProfile` from Task 1.
- Produces: `OpenAICompatibleAnalyzer(settings: ResolvedModelProfile, transport: httpx.AsyncBaseTransport | None = None)`.
- Produces: `AIAnalysisError` with secret-free messages.
- Produces: async `.analyze(task_items, context, date_str) -> str` and `.test_connection() -> None`.
- Preserves: `create_analyzer(settings) -> OpenAICompatibleAnalyzer`.

- [ ] **Step 1: Write failing analyzer tests**

```python
def test_analyzer_uses_configured_endpoint_model_and_key():
    requests = []
    analyzer = OpenAICompatibleAnalyzer(
        resolved_profile("http://127.0.0.1:9000", "custom-model", "private-key"),
        transport=httpx.MockTransport(record_chat_completion(requests)),
    )
    result = asyncio.run(
        analyzer.analyze(["完成作业"], ["已订正"], "2026-08-18")
    )
    assert result == "学习情况正常"
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer private-key"
    assert json.loads(requests[0].content)["model"] == "custom-model"


def test_upstream_failure_raises_safe_error_without_key_or_body():
    analyzer = analyzer_returning(401, "private-key echoed by upstream")
    with pytest.raises(AIAnalysisError) as error:
        asyncio.run(analyzer.analyze(["任务"], [], "2026-08-18"))
    assert "private-key" not in str(error.value)
    assert "认证失败" in str(error.value)
```

Cover timeout, 429, 5xx, malformed JSON, empty completion, and a successful minimal `test_connection` request.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_analyzer.py -q -p no:cacheprovider --basetemp=.test-tmp-ai-analyzer-red
```

Expected: imports fail because `OpenAICompatibleAnalyzer` and `AIAnalysisError` do not exist.

- [ ] **Step 3: Implement the generic analyzer**

Replace provider dispatch with a single OpenAI-compatible implementation:

```python
class AIAnalysisError(RuntimeError):
    pass


class OpenAICompatibleAnalyzer(BaseAnalyzer):
    def __init__(self, settings, transport=None):
        self._settings = settings
        self._transport = transport

    async def analyze(self, task_items, context, date_str):
        payload = {
            "model": self._settings.profile.model,
            "messages": self._build_messages(task_items, context, date_str),
            "temperature": 0.7,
            "max_tokens": 600,
        }
        data = await self._completion(payload)
        return parse_completion(data)
```

Map 401/403 to `模型服务认证失败`, 429 to `模型服务请求过于频繁`, timeouts to `模型服务请求超时`, and all other HTTP/format failures to a safe generic message. Do not retain the current fallback that makes failed AI output indistinguishable from success. Keep `DeepSeekAnalyzer = OpenAICompatibleAnalyzer` only if an existing import requires it.

- [ ] **Step 4: Run GREEN and lint**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_analyzer.py -q -p no:cacheprovider --basetemp=.test-tmp-ai-analyzer-green
.\.venv\Scripts\python.exe -m ruff check core/ai_analyzer.py tests/test_ai_analyzer.py
```

- [ ] **Step 5: Commit**

```powershell
git add core/ai_analyzer.py tests/test_ai_analyzer.py
git commit -m "feat: support OpenAI-compatible analysis"
```

---

### Task 3: Model Settings Page and Connection Test

**Files:**
- Create: `routers/model_settings.py`
- Create: `templates/model_settings.html`
- Create: `templates/components/model_settings_form.html`
- Create: `tests/test_model_settings_routes.py`
- Modify: `app.py`
- Modify: `templates/base.html`
- Modify: `static/style.css`

**Interfaces:**
- Consumes: `ModelSettingsService.status()` and `.save_and_test(...)` from Task 1.
- Consumes: `OpenAICompatibleAnalyzer.test_connection()` from Task 2 through an injected tester.
- Produces: GET `/settings/model` and POST `/settings/model`.
- Produces template context fields: `provider_name`, `api_base`, `model`, `enabled`, `has_api_key`, `ready`, `message`, and `tone`.

- [ ] **Step 1: Write failing route and secret-rendering tests**

```python
def test_header_links_to_model_settings(client):
    response = client.get("/wizard/1")
    assert response.status_code == 200
    assert 'href="/settings/model"' in response.text


def test_settings_page_never_renders_saved_key(client, configured_service):
    response = client.get("/settings/model")
    assert response.status_code == 200
    assert "private-key" not in response.text
    assert "API Key 已配置" in response.text


def test_save_and_test_returns_success_fragment(client, fake_tester):
    response = client.post(
        "/settings/model",
        data={
            "provider_name": "Local",
            "api_base": "http://127.0.0.1:11434",
            "model": "qwen2.5",
            "api_key": "private-key",
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "连接测试通过" in response.text
    assert "private-key" not in response.text
```

Also cover validation 422, upstream test 400 with sanitized copy, existing-Key reuse, disable flow, full-document vs HTMX fragment boundaries, and app composition ownership.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_settings_routes.py -q -p no:cacheprovider --basetemp=.test-tmp-model-routes-red
```

Expected: 404 responses and missing header link.

- [ ] **Step 3: Implement router, composition, templates, and styling**

Create a dedicated APIRouter. The async POST must `await service.save_and_test(...)`, pass the submitted Key directly to the service, and immediately discard the local reference after the call. Render `<input type="password" name="api_key" value="" autocomplete="new-password">`; never add the Key to context. Include status copy for `已验证`, `未配置 API Key`, `尚未通过连接测试`, and `已停用`.

Compose in `create_app()`:

```python
model_service = ModelSettingsService.default(
    tester=lambda resolved: run_model_test(resolved)
)
app.state.model_settings = model_service
app.include_router(model_settings.router)
```

The header link must remain available on wizard and settings pages. Reuse existing button, card, focus, responsive, and status styles; add only focused settings-grid and credential-status rules.

- [ ] **Step 4: Run GREEN, Jinja compilation, and lint**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_settings_routes.py tests/test_app_composition.py tests/test_wizard_templates.py -q -p no:cacheprovider --basetemp=.test-tmp-model-routes-green
.\.venv\Scripts\python.exe -c "from pathlib import Path; from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); [e.get_template(str(p.relative_to('templates')).replace('\\','/')) for p in Path('templates').rglob('*.html')]; print('Jinja templates: OK')"
.\.venv\Scripts\python.exe -m ruff check routers/model_settings.py app.py tests/test_model_settings_routes.py
```

- [ ] **Step 5: Commit**

```powershell
git add routers/model_settings.py templates/model_settings.html templates/components/model_settings_form.html tests/test_model_settings_routes.py app.py templates/base.html static/style.css
git commit -m "feat: add model settings page"
```

---

### Task 4: Enforce AI Readiness and Snapshot Export Settings

**Files:**
- Modify: `services/export.py`
- Modify: `routers/actions.py`
- Modify: `routers/wizard.py`
- Modify: `templates/steps/preview.html`
- Modify: `templates/components/export_progress.html`
- Modify: `tests/test_preview_export_step.py`
- Modify: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `ModelSettingsService.status()` and `.resolved_profile()`.
- Consumes: `create_analyzer(ResolvedModelProfile)`.
- Extends: `ExportJob` with `model_profile: ResolvedModelProfile | None`.
- Produces preview context: `ai_ready`, `ai_status`, `ai_provider`, `ai_model`, `model_settings_url`.

- [ ] **Step 1: Write failing readiness, snapshot, and failure tests**

```python
def test_preview_disables_ai_when_model_is_not_ready(preview_client):
    response = preview_client.get("/wizard/3")
    assert "AI 模型尚未配置" in response.text
    assert 'name="enable_ai"' in response.text
    assert "disabled" in ai_checkbox_markup(response.text)


def test_export_rejects_ai_when_model_is_not_ready(preview_client):
    response = preview_client.post(
        "/api/export",
        data=valid_export_data(enable_ai="true", privacy_acknowledged="true"),
    )
    assert response.status_code == 422
    assert "请先完成模型设置和连接测试" in response.text


def test_export_job_captures_immutable_model_snapshot(configured_client):
    job = capture_scheduled_job(configured_client)
    asyncio.run(
        configured_client.app.state.model_settings.save_and_test(
            "Changed", "https://changed.example", "changed", True, "new-key"
        )
    )
    assert job.model_profile.profile.model == "original-model"


def test_runtime_ai_failure_emits_warning_and_writes_failure_note(export_job):
    asyncio.run(run_export_job(export_job, analyzer=FailingAnalyzer()))
    assert progress_events_contain("AI 分析失败")
    assert written_analysis.startswith("[AI 分析失败]")
```

Also assert successful analyzer invocation, selected task/context inputs, no analysis when unchecked, retry values, and progress recovery.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_preview_export_step.py tests/test_app_routes.py -q -p no:cacheprovider --basetemp=.test-tmp-ai-export-red
```

Expected: missing readiness copy, export accepts unavailable AI, and `ExportJob` has no model snapshot.

- [ ] **Step 3: Implement readiness checks and immutable job execution**

At preview time, derive status only from `request.app.state.model_settings.status()`. Disable the checkbox when not ready and link to `/settings/model`.

At POST `/api/export`, before job creation:

```python
model_profile = None
if enable_ai:
    try:
        model_profile = request.app.state.model_settings.resolved_profile()
    except ModelSettingsError:
        return _export_error(
            request,
            session_id,
            "请先完成模型设置和连接测试。",
            422,
            retry_values,
        )
```

Store `model_profile` on the frozen `ExportJob`. In `run_export_job`, create the analyzer from `job.model_profile`; do not reread mutable global model configuration. Catch `AIAnalysisError` per task, log only provider/model/date identifiers, emit a warning event, and write `[AI 分析失败] <safe message>` to the analysis column.

- [ ] **Step 4: Run GREEN and affected regression suite**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_preview_export_step.py tests/test_app_routes.py tests/test_lifecycle.py tests/test_progress.py -q -p no:cacheprovider --basetemp=.test-tmp-ai-export-green
.\.venv\Scripts\python.exe -m ruff check services/export.py routers/actions.py routers/wizard.py tests/test_preview_export_step.py tests/test_app_routes.py
```

- [ ] **Step 5: Commit**

```powershell
git add services/export.py routers/actions.py routers/wizard.py templates/steps/preview.html templates/components/export_progress.html tests/test_preview_export_step.py tests/test_app_routes.py
git commit -m "fix: require verified AI settings for export"
```

---

### Task 5: Documentation, Live Verification, and Integration

**Files:**
- Modify: `README.md`
- Modify: `config.example.yaml`
- Test: full repository suite

**Interfaces:**
- Documents the model-settings page, supported OpenAI-compatible fields, DPAPI storage, safe Key rotation, and local-model HTTP restriction.
- Removes the implication that editing plaintext `ai.api_key` is the preferred configuration path.

- [ ] **Step 1: Update documentation and example configuration**

Document this user flow:

```text
模型设置 → 填写提供商/API 地址/模型/API Key → 保存并测试 → 返回预览 → 启用 AI 分析
```

State that the Key is machine-scope DPAPI ciphertext, never returned to the browser, and that loopback HTTP is permitted for local servers while remote services require HTTPS. Keep legacy YAML fields documented as migration defaults, not the normal secret-storage path.

- [ ] **Step 2: Run full automated verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.test-tmp-model-settings-full
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -c "from pathlib import Path; from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); [e.get_template(str(p.relative_to('templates')).replace('\\','/')) for p in Path('templates').rglob('*.html')]; print('Jinja templates: OK')"
git diff --check
```

Expected: all pytest tests pass, Ruff reports clean, every Jinja template compiles, and diff check exits 0.

- [ ] **Step 3: Run browser and live-model smoke checks**

With the in-app browser:

1. Open `/settings/model` and verify no saved Key appears in DOM or page source.
2. Ask the user to enter their API Key in the secure form; do not read it from chat, browser state, DOM, logs, or clipboard. After their confirmation, submit and test the OpenAI-compatible configuration.
3. Confirm the preview shows the verified provider/model and enables AI.
4. Export one sanitized task and verify progress includes `AI分析中` and the output workbook analysis cell contains model output.
5. Restart the app without an API-Key environment variable and confirm the DPAPI-backed configuration remains ready.

- [ ] **Step 4: Request code review and address findings**

Use `superpowers:requesting-code-review` over the complete feature diff. Fix every Critical or Important finding with a RED/GREEN regression test and rerun Step 2.

- [ ] **Step 5: Commit and push**

```powershell
git add README.md config.example.yaml
git commit -m "docs: explain secure model configuration"
git push origin main
```

- [ ] **Step 6: Restart and final smoke test**

Restart Uvicorn with `.venv\Scripts\python.exe`, explicitly report the restart to the user, then verify `/`, `/settings/model`, and the configured AI export boundary return successful results without exposing a credential.

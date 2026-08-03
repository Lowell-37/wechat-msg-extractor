# P0 Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the current WeChat extraction flow reproducibly installable and correct before replacing its UI.

**Architecture:** Keep the existing FastAPI application operational while introducing focused validation and connection modules that the later wizard can reuse. Resolve correctness defects through tests first, centralize dependency metadata, and keep each task independently committable.

**Tech Stack:** Python 3.11–3.13, FastAPI, Jinja2, htmx, openpyxl, SQLite, pytest, Ruff.

## Global Constraints

- Preserve the existing three-step user-visible workflow during this phase.
- Keep the server bound to `127.0.0.1` by default.
- Do not use a live WeChat installation, real keys, real databases, or personal message data in tests.
- Run every code change through a red-green test cycle.
- Do not stage or commit the user's untracked `AGENTS.md`.

## File Map

- Create `pyproject.toml`: canonical runtime, development, pytest, and Ruff configuration.
- Modify `requirements.txt`: compatibility entry point that installs the project metadata.
- Create `core/validation.py`: date range, Sheet, key, and output-path validation.
- Modify `core/excel_writer.py`: deterministic date-row updates, explicit Sheet errors, safe text, and reliable saves.
- Modify `core/dbutils.py`: accept and validate a manually supplied database key.
- Create `core/connection.py`: shared automatic/manual connection orchestration.
- Modify `app.py`: add the missing manual-key route and use validated session dates during export.
- Create `tests/test_project_metadata.py`, `tests/test_validation.py`, `tests/test_connection.py`, and `tests/test_app_routes.py`.
- Modify `tests/test_excel_writer.py`: strengthen existing-date, invalid-Sheet, formula, and filename-only coverage.
- Create `.github/workflows/ci.yml`: Windows test and static-check workflow.
- Modify `README.md`: canonical installation, test, and run commands.

---

### Task 1: Reproducible Python Project Metadata

**Files:**
- Create: `pyproject.toml`
- Modify: `requirements.txt`
- Create: `tests/test_project_metadata.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `py -m pip install -e ".[dev]"` as the canonical development install.
- Produces: pytest configuration with `testpaths = ["tests"]`.
- Consumes: existing imports in `app.py`, `config.py`, and `core/`.

- [ ] **Step 1: Write the failing metadata test**

```python
# tests/test_project_metadata.py
from pathlib import Path
import tomllib


def test_runtime_and_test_dependencies_are_declared():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    runtime = "\n".join(data["project"]["dependencies"]).lower()
    dev = "\n".join(data["project"]["optional-dependencies"]["dev"]).lower()

    assert "pycryptodomex" in runtime
    assert "pywxdump" in runtime
    assert "pytest" in dev
    assert "ruff" in dev
```

- [ ] **Step 2: Run the test and confirm the missing metadata failure**

Run: `py -m pytest tests/test_project_metadata.py -q`

Expected: FAIL because `pyproject.toml` does not exist.

- [ ] **Step 3: Add canonical project metadata**

Create `pyproject.toml` with this structure:

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "wechat-msg-extractor"
version = "0.1.0"
requires-python = ">=3.11,<3.14"
dependencies = [
  "fastapi==0.115.6",
  "uvicorn[standard]==0.34.0",
  "jinja2==3.1.4",
  "openpyxl==3.1.5",
  "psutil==6.1.1",
  "pymem==1.14.0",
  "pyyaml==6.0.2",
  "python-multipart==0.0.19",
  "httpx>=0.27,<1",
  "pysilk>=0.1,<1",
  "pycryptodomex>=3.20,<4",
  "pywxdump",
]

[project.optional-dependencies]
dev = ["pytest>=8,<9", "ruff>=0.12,<1"]

[tool.setuptools]
py-modules = ["app", "config"]

[tool.setuptools.packages.find]
include = ["core*"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100
```

Replace `requirements.txt` with `-e .` and update README commands to:

```powershell
py -m pip install -e ".[dev]"
py -m pytest -q
py app.py
```

- [ ] **Step 4: Install dependencies and run the metadata test**

Run: `py -m pip install -e ".[dev]"`

Expected: installation completes without an undeclared-import error.

Run: `py -m pytest tests/test_project_metadata.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the dependency baseline**

```powershell
git add pyproject.toml requirements.txt README.md tests/test_project_metadata.py
git commit -m "build: define reproducible Python environment"
```

---

### Task 2: Shared Input Validation

**Files:**
- Create: `core/validation.py`
- Create: `tests/test_validation.py`

**Interfaces:**
- Produces: `ValidationError(ValueError)`.
- Produces: `parse_date_range(start: str, end: str) -> tuple[date, date]`.
- Produces: `validate_hex_key(value: str) -> str`.
- Produces: `validate_sheet_name(value: str, available: Collection[str]) -> str`.
- Produces: `resolve_output_path(value: str, output_dir: str, default_name: str) -> Path`.

- [ ] **Step 1: Write failing validation tests**

```python
# tests/test_validation.py
from pathlib import Path
import pytest

from core.validation import (
    ValidationError,
    parse_date_range,
    resolve_output_path,
    validate_hex_key,
    validate_sheet_name,
)


def test_date_range_rejects_reverse_order():
    with pytest.raises(ValidationError, match="开始日期"):
        parse_date_range("2026-08-03", "2026-08-01")


def test_hex_key_is_normalized_and_validated():
    assert validate_hex_key(" A1" * 32) == "a1" * 32
    with pytest.raises(ValidationError, match="64"):
        validate_hex_key("abcd")


def test_sheet_must_exist():
    assert validate_sheet_name("张三", ["张三", "李四"]) == "张三"
    with pytest.raises(ValidationError, match="工作表"):
        validate_sheet_name("王五", ["张三", "李四"])


def test_output_path_is_confined_to_default_directory(tmp_path):
    result = resolve_output_path("report.xlsx", str(tmp_path), "fallback.xlsx")
    assert result == (tmp_path / "report.xlsx").resolve()
    with pytest.raises(ValidationError, match="导出目录"):
        resolve_output_path("../outside.xlsx", str(tmp_path), "fallback.xlsx")
```

- [ ] **Step 2: Run tests and confirm import failure**

Run: `py -m pytest tests/test_validation.py -q`

Expected: FAIL with `ModuleNotFoundError: core.validation`.

- [ ] **Step 3: Implement minimal validation functions**

```python
# core/validation.py
import re
from datetime import date
from pathlib import Path
from typing import Collection


class ValidationError(ValueError):
    pass


def parse_date_range(start: str, end: str) -> tuple[date, date]:
    try:
        start_value = date.fromisoformat(start)
        end_value = date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise ValidationError("日期格式无效") from exc
    if start_value > end_value:
        raise ValidationError("开始日期不能晚于结束日期")
    return start_value, end_value


def validate_hex_key(value: str) -> str:
    normalized = "".join(value.split()).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValidationError("数据库密钥必须是 64 位十六进制字符串")
    return normalized


def validate_sheet_name(value: str, available: Collection[str]) -> str:
    if value not in available:
        raise ValidationError("目标工作表不存在")
    return value


def resolve_output_path(value: str, output_dir: str, default_name: str) -> Path:
    root = Path(output_dir).resolve()
    candidate = Path(value) if value else Path(default_name)
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".xlsx":
        raise ValidationError("导出文件必须使用 .xlsx 扩展名")
    if root != resolved.parent and root not in resolved.parents:
        raise ValidationError("导出文件必须位于配置的导出目录内")
    return resolved
```

- [ ] **Step 4: Run validation tests**

Run: `py -m pytest tests/test_validation.py -q`

Expected: PASS.

- [ ] **Step 5: Commit shared validation**

```powershell
git add core/validation.py tests/test_validation.py
git commit -m "feat: centralize workflow validation"
```

---

### Task 3: Correct and Safe Excel Writes

**Files:**
- Modify: `core/excel_writer.py`
- Modify: `tests/test_excel_writer.py`

**Interfaces:**
- Consumes: `ParsedTask.date`, `ParsedTask.tasks`, and validated Sheet names.
- Produces: `ExcelWriter.add_task(...) -> int`, returning the updated row number.
- Produces: `safe_excel_text(value: str) -> str`.

- [ ] **Step 1: Add failing regression tests**

```python
def test_write_to_existing_date_updates_that_row(temp_template, parsed_task):
    writer = ExcelWriter(temp_template)
    row = writer.add_task("张三", parsed_task, "=HYPERLINK(\"bad\")")
    assert row == 2
    assert writer._wb["张三"]["B2"].value.startswith("1、订正作文")
    assert writer._wb["张三"]["C2"].value.startswith("'")
    writer.close()


def test_missing_sheet_is_an_error(temp_template, parsed_task):
    writer = ExcelWriter(temp_template)
    with pytest.raises(KeyError, match="不存在的Sheet"):
        writer.add_task("不存在的Sheet", parsed_task)
    writer.close()


def test_save_accepts_filename_without_parent(temp_template, parsed_task, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    writer = ExcelWriter(temp_template)
    writer.add_task("张三", parsed_task)
    writer.save("output.xlsx")
    writer.close()
    assert (tmp_path / "output.xlsx").exists()
```

- [ ] **Step 2: Run the focused tests and confirm failures**

Run: `py -m pytest tests/test_excel_writer.py -q`

Expected: FAIL because existing dates append, missing Sheets are ignored, and empty parent paths fail.

- [ ] **Step 3: Implement deterministic row selection and safe text**

Implement these helpers and behavior in `core/excel_writer.py`:

```python
from datetime import date, datetime
from pathlib import Path
from openpyxl.utils.datetime import from_excel


def safe_excel_text(value: str) -> str:
    if value and value[0] in "=+-@":
        return "'" + value
    return value


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        converted = from_excel(value)
        return converted.date() if isinstance(converted, datetime) else converted
    return None
```

`add_task` must scan rows `2..max_row`, reuse the row whose column A date equals `task.date`, append only when absent, raise `KeyError(sheet_name)` for a missing Sheet, sanitize columns B/C, and return the selected row number. `save` must use `Path(output_path).parent.mkdir(parents=True, exist_ok=True)` before saving.

- [ ] **Step 4: Run Excel tests**

Run: `py -m pytest tests/test_excel_writer.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Excel correctness fixes**

```powershell
git add core/excel_writer.py tests/test_excel_writer.py
git commit -m "fix: update existing Excel dates safely"
```

---

### Task 4: Shared Automatic and Manual Connection Flow

**Files:**
- Modify: `core/dbutils.py`
- Create: `core/connection.py`
- Create: `tests/test_connection.py`
- Modify: `app.py`
- Create: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `validate_hex_key(value: str) -> str` from Task 2.
- Produces: `WeChatDB.scan_and_use_key(key_hex: str) -> tuple[bool, str]`.
- Produces: `ConnectedWechat(manager: WeChatDB, database: MergedMsgDB, shard_count: int, table_count: int)`.
- Produces: `connect_wechat(key: str | None = None) -> ConnectedWechat`.

- [ ] **Step 1: Write failing connection-service tests**

```python
# tests/test_connection.py
import pytest

from core.connection import ConnectionError, connect_wechat


def test_manual_connection_uses_supplied_key(monkeypatch):
    calls = []

    class FakeDB:
        def scan_and_use_key(self, key):
            calls.append(key)
            return True, "密钥已接受"

        def open_all_msg_dbs(self):
            return [FakeShard()]

    class FakeShard:
        def execute(self, sql, params=()):
            return [{"n": 2}]

        def close(self):
            pass

    monkeypatch.setattr("core.connection.WeChatDB", FakeDB)
    result = connect_wechat("ab" * 32)
    assert calls == ["ab" * 32]
    assert result.shard_count == 1


def test_connection_closes_shards_when_validation_fails(monkeypatch):
    closed = []

    class BadShard:
        def execute(self, sql, params=()):
            raise RuntimeError("bad database")

        def close(self):
            closed.append(True)

    class FakeDB:
        def scan_and_extract(self):
            return True, "ok"

        def open_all_msg_dbs(self):
            return [BadShard()]

    monkeypatch.setattr("core.connection.WeChatDB", FakeDB)
    with pytest.raises(ConnectionError, match="数据库验证失败"):
        connect_wechat()
    assert closed == [True]
```

- [ ] **Step 2: Run connection tests and confirm import failure**

Run: `py -m pytest tests/test_connection.py -q`

Expected: FAIL because `core.connection` does not exist.

- [ ] **Step 3: Implement manual-key preparation and connection orchestration**

Add to `WeChatDB`:

```python
def scan_and_use_key(self, key_hex: str) -> tuple[bool, str]:
    from core.validation import validate_hex_key

    self._info = self._scanner.scan()
    if not self._info.data_dir:
        return False, "未找到数据目录"
    self._key = validate_hex_key(key_hex)
    return True, "密钥已接受"
```

In `core/connection.py`, implement `ConnectionError`, the `ConnectedWechat` dataclass, and `connect_wechat`. It must choose auto extraction when `key is None`, otherwise call `scan_and_use_key`; open every shard; verify `sqlite_master` and `MSG`; close all opened shards on any failure; and return a `MergedMsgDB` on success.

- [ ] **Step 4: Add the missing route test and route**

```python
# tests/test_app_routes.py
from fastapi.testclient import TestClient
import app as app_module


def test_manual_key_route_stores_connection(monkeypatch):
    connected = object()

    class Result:
        manager = object()
        database = connected
        shard_count = 1
        table_count = 2

    monkeypatch.setattr(app_module, "connect_wechat", lambda key=None: Result())
    client = TestClient(app_module.app)
    client.get("/")
    response = client.post("/api/key/validate", data={"key": "ab" * 32})
    assert response.status_code == 200
    assert "验证通过" in response.text
```

Add `@app.post("/api/key/validate")`, call `connect_wechat(key)`, store `manager` and `database` in session, and return a safe HTML fragment. Refactor automatic extraction to call `connect_wechat()` so both routes share verification and cleanup.

- [ ] **Step 5: Run connection and route tests**

Run: `py -m pytest tests/test_connection.py tests/test_app_routes.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the unified connection flow**

```powershell
git add core/dbutils.py core/connection.py app.py tests/test_connection.py tests/test_app_routes.py
git commit -m "fix: unify automatic and manual database connection"
```

---

### Task 5: Validated Preview and Voice Export Dates

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `parse_date_range(start: str, end: str) -> tuple[date, date]` from Task 2.
- Consumes: `resolve_output_path(...) -> Path` and `validate_sheet_name(...) -> str` from Task 2.
- Produces: export closures that capture validated `start_value` and `end_value` instead of undefined names.

- [ ] **Step 1: Add failing route validation tests**

```python
def test_preview_rejects_reverse_date_range(client_with_database):
    response = client_with_database.post(
        "/api/preview",
        data={
            "group_name": "group@chatroom",
            "sheet_name": "张三",
            "start_date": "2026-08-03",
            "end_date": "2026-08-01",
        },
    )
    assert response.status_code == 400
    assert "开始日期不能晚于结束日期" in response.text


def test_export_builds_voice_range_from_session_dates(monkeypatch, client_with_preview):
    captured = {}

    class FakeVoice:
        def __init__(self, database, config):
            pass

        async def transcribe_all(self, group, start_ts, end_ts):
            captured.update(group=group, start_ts=start_ts, end_ts=end_ts)
            return {}

    monkeypatch.setattr(app_module, "VoiceTranscriber", FakeVoice)
    monkeypatch.setattr(app_module.config.voice, "enabled", True)
    monkeypatch.setattr(app_module.config.voice, "api_key", "test-key")
    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "张三", "output_path": "result.xlsx"}
    )
    assert response.status_code == 200
    assert captured["end_ts"] > captured["start_ts"]
```

The fixtures must use a fake merged database, a temporary workbook, and session dates; they must not access WeChat or external APIs.

- [ ] **Step 2: Run focused route tests and confirm failures**

Run: `py -m pytest tests/test_app_routes.py -q`

Expected: FAIL because reverse dates are unhandled and voice export references undefined variables.

- [ ] **Step 3: Apply validation before storing or exporting state**

In `preview`, call `parse_date_range` before mutating session state and return status 400 with a safe error partial on `ValidationError`. In `export`, validate the stored dates, available Sheet names, and output path before starting the background task. Capture `start_value` and `end_value` in the closure and calculate timestamps from those names.

Replace the voice block's silent exception with:

```python
logger.warning("Voice transcription failed for session %s", session_id, exc_info=True)
await progress_hub.emit(
    session_id,
    ProgressEvent(stage="warning", message="部分语音转写失败，继续导出文本任务", progress=8),
)
```

- [ ] **Step 4: Run route and existing regression tests**

Run: `py -m pytest tests/test_app_routes.py tests/test_task_parser.py tests/test_excel_writer.py -q`

Expected: PASS.

- [ ] **Step 5: Commit validated preview and export**

```powershell
git add app.py tests/test_app_routes.py
git commit -m "fix: validate preview and export state"
```

---

### Task 6: P0 Quality Gate and Windows CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: all tests and commands created in Tasks 1–5.
- Produces: a repeatable Windows quality gate for the next UI phase.

- [ ] **Step 1: Run the complete local quality gate**

Run:

```powershell
py -m pytest -q
py -m ruff check .
```

Expected: all tests pass and Ruff reports no errors. Fix only violations in files touched by this phase; configure explicit ignores for unrelated legacy findings rather than silently rewriting the repository.

- [ ] **Step 2: Add Windows CI**

```yaml
# .github/workflows/ci.yml
name: ci
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
      - run: py -m pip install -e ".[dev]"
      - run: py -m pytest -q
      - run: py -m ruff check .
```

- [ ] **Step 3: Document the verified workflow**

README must state the supported Python range, virtual-environment commands, development install, test command, lint command, and local-only default address. It must not claim tests pass unless Step 1 actually completed.

- [ ] **Step 4: Re-run the complete quality gate**

Run:

```powershell
py -m pytest -q
py -m ruff check .
```

Expected: zero test failures and zero Ruff errors.

- [ ] **Step 5: Commit the P0 quality gate**

```powershell
git add .github/workflows/ci.yml README.md
git commit -m "ci: add Windows quality gate"
```

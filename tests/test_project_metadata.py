import tomllib
from pathlib import Path


def test_runtime_and_test_dependencies_are_declared():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    runtime = "\n".join(data["project"]["dependencies"]).lower()
    dev = "\n".join(data["project"]["optional-dependencies"]["dev"]).lower()

    assert "pycryptodomex" in runtime
    assert "pywxdump" in runtime
    assert "sqlcipher3" in runtime
    assert "pytest" in dev
    assert "ruff" in dev


def test_ruff_has_explicit_selection_without_touched_file_exemptions():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    lint = data["tool"]["ruff"]["lint"]

    assert set(lint["select"]) == {
        "B",
        "BLE",
        "DTZ",
        "E4",
        "E7",
        "E9",
        "F",
        "I",
        "RUF",
        "S",
        "TRY",
        "UP",
    }
    touched = {
        "app.py",
        "core/connection.py",
        "core/dbutils.py",
        "core/excel_writer.py",
        "core/progress.py",
    }
    assert touched.isdisjoint(lint.get("per-file-ignores", {}))

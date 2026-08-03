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

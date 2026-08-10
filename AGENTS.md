# Repository Guidelines

## Project Structure & Module Organization

`app.py` defines the FastAPI application, routes, and in-memory session flow. Keep reusable domain logic in `core/`: database discovery/decryption, message fetching, task parsing, matching, AI/voice processing, and Excel output each have focused modules. Jinja templates live in `templates/`, browser styling in `static/`, and automated tests in `tests/`. Configuration defaults and loading are defined by `config.py` and `config.example.yaml`; `excel_structure.json` documents the expected workbook layout. Design notes and implementation plans are under `docs/superpowers/`.

## Build, Test, and Development Commands

Create and activate a virtual environment before installing dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
```

- `py app.py` starts the local server at `http://127.0.0.1:8888` using the configured host and port.
- `py -m pytest` runs the full test suite.
- `py -m pytest tests/test_task_parser.py -q` runs one focused test module.
- `py -m ruff check .` runs the configured lint and import checks.

There is no separate build step; templates and CSS are served directly by FastAPI.

## Coding Style & Naming Conventions

Follow standard Python conventions: four-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_CASE` for constants. Add type hints to public or non-obvious interfaces and use dataclasses for structured records where appropriate. Keep modules focused and preserve the existing separation between web routes and `core/` logic. Name templates by workflow step, such as `step2_select.html`. Run Ruff before committing and keep imports grouped.

## Testing Guidelines

Tests use `pytest`, including fixtures and class-based groupings. Name files `test_<module>.py`, classes `Test<Component>`, and functions `test_<behavior>`. Prefer deterministic unit tests with fake decryptors or temporary data; never depend on a developer's live WeChat installation, credentials, or databases. Add regression coverage for parsing formats, database schema variations, and Excel cell mappings affected by a change.

## Commit & Pull Request Guidelines

History follows Conventional Commit-style subjects such as `feat:`, `fix:`, `docs:`, and `chore:`. Keep subjects imperative and scoped to one logical change. Pull requests should explain user-visible behavior, list verification commands, link relevant issues, and include screenshots for template or CSS changes. Call out configuration or database compatibility impacts explicitly.

## Security & Configuration Tips

Copy `config.example.yaml` to local `config.yaml` and keep secrets, exported media, Excel outputs, decrypted databases, and API keys out of Git. Use sanitized fixtures when reproducing message or workbook issues.

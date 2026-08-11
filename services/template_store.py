"""Validated persistence for user-selected Excel templates."""

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import BadZipFile

import yaml
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from config import AppConfig

DEFAULT_MAX_TEMPLATE_BYTES = 20 * 1024 * 1024


class TemplateUploadError(ValueError):
    """Raised when an uploaded workbook cannot be safely activated."""


@dataclass(frozen=True)
class TemplateActivation:
    path: str
    filename: str
    sheet_names: tuple[str, ...]


def activate_template(
    upload: BinaryIO,
    filename: str,
    *,
    base_dir: Path,
    config_path: Path,
    config: AppConfig,
    max_bytes: int = DEFAULT_MAX_TEMPLATE_BYTES,
) -> TemplateActivation:
    """Validate an upload and persist its server-side copy as the default."""
    safe_filename = Path(filename.replace("\\", "/")).name
    if not safe_filename or Path(safe_filename).suffix.lower() != ".xlsx":
        raise TemplateUploadError("请选择 .xlsx 格式的 Excel 模板")

    template_dir = Path(base_dir) / "local" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = _stream_limited(upload, template_dir, max_bytes)
    destination: Path | None = None
    try:
        sheet_names = _validate_workbook(temporary_path)
        destination = template_dir / f"{uuid.uuid4().hex}.xlsx"
        os.replace(temporary_path, destination)
        temporary_path = None
        resolved_path = str(destination.resolve())
        try:
            _persist_template_path(Path(config_path), resolved_path)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise TemplateUploadError(f"无法保存模板配置：{exc}") from exc
        config.excel.template_path = resolved_path
        return TemplateActivation(resolved_path, safe_filename, sheet_names)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _stream_limited(
    upload: BinaryIO, template_dir: Path, max_bytes: int
) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix="template-upload-", suffix=".xlsx", dir=template_dir
    )
    path = Path(name)
    total = 0
    completed = False
    try:
        with os.fdopen(descriptor, "wb") as output:
            while chunk := upload.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise TemplateUploadError(
                        "Excel 模板过大，最大允许 20 MiB"
                    )
                output.write(chunk)
        completed = True
    finally:
        if not completed:
            path.unlink(missing_ok=True)
    return path


def _validate_workbook(path: Path) -> tuple[str, ...]:
    workbook = None
    try:
        workbook = load_workbook(path, read_only=True, data_only=False)
        sheet_names = tuple(
            worksheet.title
            for worksheet in workbook.worksheets
            if worksheet.sheet_state == "visible"
        )
    except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise TemplateUploadError("Excel 模板无法读取或文件已损坏") from exc
    finally:
        if workbook is not None:
            workbook.close()
    if not sheet_names:
        raise TemplateUploadError("Excel 模板至少需要一个可见 Sheet")
    return sheet_names


def _persist_template_path(config_path: Path, template_path: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file) or {}
        if not isinstance(loaded, dict):
            raise OSError("config.yaml 顶层必须是映射")
        data = loaded
    excel = data.setdefault("excel", {})
    if not isinstance(excel, dict):
        raise OSError("config.yaml 的 excel 配置必须是映射")
    excel["template_path"] = template_path

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="config-", suffix=".yaml", dir=config_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            yaml.safe_dump(
                data,
                output,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(temporary_path, config_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

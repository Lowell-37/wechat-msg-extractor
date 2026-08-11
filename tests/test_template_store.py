import io
import zipfile
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from config import AppConfig
from services.template_store import (
    TemplateUploadError,
    activate_template,
)


def _workbook_bytes(sheet_name="项目群"):
    stream = io.BytesIO()
    workbook = Workbook()
    workbook.active.title = sheet_name
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _hidden_only_workbook_bytes():
    source = io.BytesIO(_workbook_bytes())
    output = io.BytesIO()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(
        output, "w"
    ) as rewritten:
        for item in archive.infolist():
            content = archive.read(item.filename)
            if item.filename == "xl/workbook.xml":
                content = content.replace(
                    b'state="visible"', b'state="hidden"'
                )
            rewritten.writestr(item, content)
    return output.getvalue()


def test_activate_template_persists_valid_upload_and_sanitizes_name(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "server:\n  port: 9000\ncustom:\n  preserved: true\n",
        encoding="utf-8",
    )
    config = AppConfig()

    result = activate_template(
        io.BytesIO(_workbook_bytes()),
        "../../任务安排.xlsx",
        base_dir=tmp_path,
        config_path=config_path,
        config=config,
    )

    saved_path = Path(result.path)
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result.filename == "任务安排.xlsx"
    assert result.sheet_names == ("项目群",)
    assert saved_path.parent == tmp_path / "local" / "templates"
    assert saved_path.name != "任务安排.xlsx"
    assert saved_path.exists()
    assert config.excel.template_path == str(saved_path)
    assert persisted["excel"]["template_path"] == str(saved_path)
    assert persisted["server"]["port"] == 9000
    assert persisted["custom"]["preserved"] is True


@pytest.mark.parametrize("filename", ["template.xls", "template.xlsm", "template.exe"])
def test_activate_template_rejects_non_xlsx_files(tmp_path, filename):
    config = AppConfig()

    with pytest.raises(TemplateUploadError, match=r"\.xlsx"):
        activate_template(
            io.BytesIO(_workbook_bytes()),
            filename,
            base_dir=tmp_path,
            config_path=tmp_path / "config.yaml",
            config=config,
        )


def test_activate_template_rejects_oversized_upload(tmp_path):
    config = AppConfig()

    with pytest.raises(TemplateUploadError, match="过大"):
        activate_template(
            io.BytesIO(b"01234567890"),
            "template.xlsx",
            base_dir=tmp_path,
            config_path=tmp_path / "config.yaml",
            config=config,
            max_bytes=10,
        )


def test_activate_template_rejects_corrupt_workbook(tmp_path):
    config = AppConfig()

    with pytest.raises(TemplateUploadError, match="无法读取"):
        activate_template(
            io.BytesIO(b"not an xlsx workbook"),
            "template.xlsx",
            base_dir=tmp_path,
            config_path=tmp_path / "config.yaml",
            config=config,
        )


def test_activate_template_requires_a_visible_sheet(tmp_path):
    config = AppConfig()

    with pytest.raises(TemplateUploadError, match="可见 Sheet"):
        activate_template(
            io.BytesIO(_hidden_only_workbook_bytes()),
            "template.xlsx",
            base_dir=tmp_path,
            config_path=tmp_path / "config.yaml",
            config=config,
        )


def test_config_write_failure_preserves_active_template(tmp_path, monkeypatch):
    old_template = tmp_path / "old.xlsx"
    old_template.write_bytes(_workbook_bytes("旧模板"))
    config = AppConfig()
    config.excel.template_path = str(old_template)

    def fail_persist(config_path, template_path):
        raise RuntimeError("serialization unavailable")

    monkeypatch.setattr(
        "services.template_store._persist_template_path", fail_persist
    )

    with pytest.raises(TemplateUploadError, match="无法保存"):
        activate_template(
            io.BytesIO(_workbook_bytes("新模板")),
            "new.xlsx",
            base_dir=tmp_path,
            config_path=tmp_path / "config.yaml",
            config=config,
        )

    assert config.excel.template_path == str(old_template)
    assert old_template.exists()
    assert list((tmp_path / "local" / "templates").glob("*.xlsx")) == []

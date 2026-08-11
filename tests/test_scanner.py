from types import SimpleNamespace

import pytest

from core.scanner import WeChatInfo, WeChatScanner


@pytest.mark.parametrize(
    "process_name",
    ["WeChat.exe", "WeChatAppEx.exe", "Weixin.exe"],
)
def test_detect_process_accepts_supported_wechat_executable_names(
    monkeypatch, process_name
):
    process = SimpleNamespace(
        info={
            "pid": 2468,
            "name": process_name,
            "exe": rf"D:\Apps\Weixin\{process_name}",
        }
    )
    monkeypatch.setattr(
        "core.scanner.psutil.process_iter",
        lambda attributes: [process],
    )
    info = WeChatInfo()

    WeChatScanner()._detect_process(info)

    assert info.pid == 2468
    assert info.exe_path == rf"D:\Apps\Weixin\{process_name}"
    assert info.install_path == r"D:\Apps\Weixin"


def test_process_runtime_path_removes_only_stale_install_warning(monkeypatch):
    process = SimpleNamespace(
        info={
            "pid": 2468,
            "name": "WeChatAppEx.exe",
            "exe": r"D:\Apps\Weixin\runtime\WeChatAppEx.exe",
        }
    )
    monkeypatch.setattr(
        "core.scanner.psutil.process_iter",
        lambda attributes: [process],
    )
    compatibility_warning = (
        "检测到新版微信 message_*.db 数据；当前版本暂不支持解密与导出"
    )
    info = WeChatInfo(
        errors=[
            r"微信安装目录不存在: C:\Program Files\Tencent\WeChat",
            compatibility_warning,
        ]
    )

    WeChatScanner()._detect_process(info)

    assert not any(error.startswith("微信安装目录不存在:") for error in info.errors)
    assert compatibility_warning in info.errors


def test_xwechat_configured_storage_discovers_new_message_databases(
    monkeypatch, tmp_path
):
    configured_root = tmp_path / "wechat-store"
    message_dir = (
        configured_root
        / "xwechat_files"
        / "wxid_demo_ab12"
        / "db_storage"
        / "message"
    )
    message_dir.mkdir(parents=True)
    (message_dir / "message_1.db").touch()
    (message_dir / "message_0.db").touch()

    config_dir = tmp_path / "xwechat" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "account.ini").write_text(
        str(configured_root), encoding="utf-8"
    )

    monkeypatch.setattr(WeChatScanner, "_DATA_BASES", ())
    monkeypatch.setattr(
        WeChatScanner,
        "_XWECHAT_CONFIG_DIR",
        str(config_dir),
    )
    info = WeChatInfo()

    WeChatScanner()._detect_data_dir(info)

    assert info.data_dir == str(message_dir)
    assert info.db_files == ["message_0.db", "message_1.db"]
    assert any("新版微信" in error and "需要手动提供" in error for error in info.errors)

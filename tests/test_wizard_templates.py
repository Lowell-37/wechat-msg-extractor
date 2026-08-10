from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
from schemas.wizard import WizardStep


@pytest.fixture(autouse=True)
def reset_in_memory_state():
    app_module.session_state.clear()
    yield
    app_module.session_state.clear()


@pytest.fixture
def client(monkeypatch):
    class Scanner:
        def __init__(self, **kwargs):
            pass

        def scan(self):
            return SimpleNamespace(
                version="4.0.3",
                install_path="C:/Program Files/WeChat",
                pid=2468,
                data_dir="D:/WeChat Files/demo/Msg",
                errors=[],
            )

    monkeypatch.setattr(app_module, "WeChatScanner", Scanner)
    return TestClient(app_module.app)


def test_wizard_shell_has_persistent_stepper_workspace_and_action_bar(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="wizard-stepper"' in response.text
    assert 'id="wizard-workspace"' in response.text
    assert 'class="wizard-actions"' in response.text
    assert "微信消息提取器" in response.text
    assert "本机处理" in response.text


def test_connect_step_has_labeled_status_controls_and_collapsed_manual_key(client):
    response = client.get("/")

    assert 'aria-live="polite"' in response.text
    assert "微信客户端" in response.text
    assert "微信进程" in response.text
    assert "数据与消息库" in response.text
    assert "连接并继续" in response.text
    assert "高级选项" in response.text
    assert "手动输入密钥" in response.text
    assert 'label for="manual-key"' in response.text
    assert '<details class="advanced-options">' in response.text
    assert '<details class="advanced-options" open' not in response.text


def test_privacy_copy_does_not_promise_that_data_is_never_uploaded(client):
    response = client.get("/")

    assert "数据库与 Excel 默认仅在本机处理；启用 AI 或语音时会先征得确认。" in response.text
    assert "数据仅存储在本地，不上传任何信息" not in response.text


def test_partial_navigation_returns_workspace_and_pushes_canonical_url(client):
    response = client.get("/wizard/1/partial")

    assert response.status_code == 200
    assert response.headers["HX-Push-Url"] == "/wizard/1"
    assert response.text.lstrip().startswith('<div id="wizard-workspace"')
    assert "<!DOCTYPE html>" not in response.text


def test_successful_connection_enables_next_without_advancing(client, monkeypatch):
    connected = object()

    class Result:
        manager = object()
        database = connected
        shard_count = 3
        table_count = 12

    monkeypatch.setattr(app_module, "connect_wechat", lambda key=None: Result())
    client.get("/")

    response = client.post("/api/key/extract")

    assert response.status_code == 200
    assert "连接成功" in response.text
    assert "3 个消息分片" in response.text
    assert 'hx-get="/wizard/2/partial"' in response.text
    assert "disabled" not in response.text.split("wizard-actions", 1)[1]
    assert "HX-Push-Url" not in response.headers
    session_id = client.cookies["session_id"]
    wizard = app_module.session_state[session_id]["wizard"]
    assert wizard.connected is True
    assert wizard.active_step is WizardStep.CONNECT


def test_connection_error_is_escaped_recoverable_and_keeps_environment(
    client, monkeypatch
):
    def fail(key=None):
        raise RuntimeError("<script>alert(1)</script>")

    monkeypatch.setattr(app_module, "connect_wechat", fail)
    client.get("/")

    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    assert response.status_code == 400
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "失败原因" in response.text
    assert "恢复建议" in response.text
    assert "重试连接" in response.text
    assert "4.0.3" in response.text
    assert "D:/WeChat Files/demo/Msg" in response.text

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from schemas.wizard import WizardStep
from services.session import get_session
from services.wizard import get_wizard, request_step, step_statuses

router = APIRouter()

_STEP_TEMPLATES = {
    WizardStep.CONNECT: "steps/connect.html",
    WizardStep.SELECT: "step2_select.html",
    WizardStep.PREVIEW: "step3_preview.html",
}

_STEP_LABELS = {
    WizardStep.CONNECT: "连接微信",
    WizardStep.SELECT: "选择数据",
    WizardStep.PREVIEW: "预览导出",
}


@router.get("/")
async def index(request: Request):
    return _render_wizard_step(request, WizardStep.CONNECT, partial=False)


@router.get("/wizard/{step}")
async def wizard_step(request: Request, step: int):
    requested = _parse_step(step)
    return _render_wizard_step(request, requested, partial=False)


@router.get("/wizard/{step}/partial")
async def wizard_step_partial(request: Request, step: int):
    requested = _parse_step(step)
    return _render_wizard_step(request, requested, partial=True)


@router.get("/step/2")
async def legacy_step_two():
    return RedirectResponse("/wizard/2", status_code=303)


@router.get("/step/3")
async def legacy_step_three():
    return RedirectResponse("/wizard/3", status_code=303)


def _parse_step(step: int) -> WizardStep:
    try:
        return WizardStep(step)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Wizard step not found") from exc


def _render_wizard_step(
    request: Request,
    requested: WizardStep,
    *,
    partial: bool,
):
    session_id, state = get_session(request)
    wizard = get_wizard(state)
    accessible = request_step(wizard, requested)
    if accessible is not requested:
        suffix = "/partial" if partial else ""
        response = RedirectResponse(
            f"/wizard/{int(accessible)}{suffix}", status_code=303
        )
        return _with_session_cookie(request, response, session_id)

    today = date.today()  # noqa: DTZ011
    context = build_wizard_context(accessible, wizard)
    context.update(
        {
            "step_template": _STEP_TEMPLATES[accessible],
            "start_date": state.get("start_date")
            or (today - timedelta(days=30)).isoformat(),
            "end_date": state.get("end_date") or today.isoformat(),
        }
    )
    if accessible is WizardStep.CONNECT:
        context.update(build_connection_view_model(request, state))

    response = request.app.state.templates.TemplateResponse(
        request,
        "wizard_fragment.html" if partial else "wizard.html",
        context,
    )
    if partial:
        response.headers["HX-Push-Url"] = f"/wizard/{int(accessible)}"
    return _with_session_cookie(request, response, session_id)


def build_wizard_context(step: WizardStep, wizard) -> dict[str, Any]:
    statuses = step_statuses(wizard)
    return {
        "step": int(step),
        "wizard": wizard,
        "wizard_steps": [
            {
                "number": int(item),
                "label": _STEP_LABELS[item],
                "status": statuses[item].value,
                "can_visit": statuses[item].value != "locked",
            }
            for item in WizardStep
        ],
    }


def build_connection_view_model(
    request: Request,
    state: dict[str, Any],
    *,
    refresh: bool = False,
    connection_status: dict[str, str] | None = None,
) -> dict[str, Any]:
    environment = state.get("wechat_environment")
    if refresh or not isinstance(environment, dict):
        environment = _scan_environment(request)
        state["wechat_environment"] = environment

    wizard = get_wizard(state)
    return {
        "environment_items": environment["items"],
        "environment_warnings": environment["warnings"],
        "connection_status": connection_status,
        "next_enabled": wizard.connected,
    }


def _scan_environment(request: Request) -> dict[str, Any]:
    try:
        config = request.app.state.config
        scanner = request.app.state.scanner_factory(
            install_path=config.wechat.version_dir,
            data_dir=config.wechat.data_dir,
        )
        info = scanner.scan()
    except Exception as exc:  # noqa: BLE001
        return {
            "items": [
                _environment_item(
                    "微信客户端",
                    "检测失败",
                    "请确认微信已安装后重新检测",
                    ok=False,
                ),
                _environment_item("微信进程", "状态未知", "无法读取进程", ok=False),
                _environment_item(
                    "数据与消息库", "状态未知", "尚未找到数据库", ok=False
                ),
            ],
            "warnings": [str(exc)],
        }

    return {
        "items": [
            _environment_item(
                "微信客户端",
                f"版本 {info.version}" if info.version else "未检测到微信安装",
                str(info.install_path or "未发现安装路径"),
                ok=bool(info.version),
            ),
            _environment_item(
                "微信进程",
                f"正在运行（PID {info.pid}）" if info.pid else "未运行",
                "可以读取当前进程" if info.pid else "请启动并登录微信",
                ok=bool(info.pid),
            ),
            _environment_item(
                "数据与消息库",
                str(info.data_dir or "未找到数据目录"),
                "消息分片将在连接时验证" if info.data_dir else "请检查微信数据目录",
                ok=bool(info.data_dir),
            ),
        ],
        "warnings": [str(error) for error in info.errors],
    }


def _environment_item(
    label: str, value: str, detail: str, *, ok: bool
) -> dict[str, str]:
    return {
        "label": label,
        "value": value,
        "detail": detail,
        "tone": "ok" if ok else "error",
        "icon": "✓" if ok else "!",
        "state_text": "已就绪" if ok else "需处理",
    }


def _with_session_cookie(request: Request, response, session_id: str):
    if request.cookies.get("session_id") != session_id:
        response.set_cookie(key="session_id", value=session_id)
    return response

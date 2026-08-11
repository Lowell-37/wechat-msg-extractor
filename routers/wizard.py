from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from schemas.wizard import WizardStep
from services.catalog import find_catalog_option, load_catalog
from services.session import get_session
from services.wizard import (
    get_wizard,
    has_complete_selection,
    request_step,
    step_statuses,
)

router = APIRouter()

_STEP_TEMPLATES = {
    WizardStep.CONNECT: "steps/connect.html",
    WizardStep.SELECT: "steps/select.html",
    WizardStep.PREVIEW: "steps/preview.html",
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

    context = build_wizard_context(accessible, wizard)
    context["step_template"] = _STEP_TEMPLATES[accessible]
    if accessible is WizardStep.CONNECT:
        context.update(build_connection_view_model(request, state))
    elif accessible is WizardStep.SELECT:
        context.update(build_selection_view_model(request, state))
    else:
        context.update(build_preview_view_model(request, state))

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
    template_path = request.app.state.config.excel.template_path
    return {
        "environment_items": environment["items"],
        "environment_warnings": environment["warnings"],
        "connection_status": connection_status,
        "next_enabled": wizard.connected,
        "template_name": Path(template_path).name if template_path else "未选择",
        "template_path": template_path,
        "template_available": bool(
            template_path and Path(template_path).is_file()
        ),
    }


def build_selection_view_model(
    request: Request,
    state: dict[str, Any],
    *,
    values: dict[str, str] | None = None,
    validation_error: str = "",
    next_enabled: bool | None = None,
) -> dict[str, Any]:
    """Build the single-region selection view from cache and session state."""
    wizard = get_wizard(state)
    catalog_error = ""
    if not isinstance(state.get("chatroom_catalog"), tuple):
        try:
            load_catalog(state, request.app.state.config.excel.template_path)
        except Exception as exc:  # noqa: BLE001
            catalog_error = f"无法加载群聊列表：{exc}"

    selection = wizard.selection
    today = date.today()  # noqa: DTZ011
    defaults = {
        "group_id": selection.group_id,
        "group_name": selection.group_name,
        "start_date": (
            selection.start_date.isoformat()
            if selection.start_date
            else state.get("start_date") or (today - timedelta(days=30)).isoformat()
        ),
        "end_date": (
            selection.end_date.isoformat()
            if selection.end_date
            else state.get("end_date") or today.isoformat()
        ),
        "sheet_name": selection.sheet_name or state.get("selected_sheet") or "",
        "query": "",
    }
    if values:
        defaults.update(values)
    if next_enabled is None:
        next_enabled = has_complete_selection(selection)
        if next_enabled:
            try:
                find_catalog_option(state, selection.group_id, selection.group_name)
            except ValueError:
                next_enabled = False
            else:
                next_enabled = selection.sheet_name in state.get(
                    "catalog_sheet_names", ()
                )
    return {
        **defaults,
        "options": state.get("chatroom_catalog") or (),
        "sheet_names": state.get("catalog_sheet_names") or (),
        "validation_error": validation_error or catalog_error,
        "next_enabled": next_enabled,
    }


def build_preview_view_model(
    request: Request,
    state: dict[str, Any],
    *,
    parsed_tasks: list[Any] | None = None,
) -> dict[str, Any]:
    """Build grouped preview rows and persisted export controls."""
    wizard = get_wizard(state)
    selection = wizard.selection
    tasks = parsed_tasks if parsed_tasks is not None else state.get("parsed_tasks", [])
    selected_ids = set(wizard.selected_task_ids)
    if not selected_ids:
        selected_ids = {str(task.msg_id) for task in tasks}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        date_key = task.date.isoformat()
        grouped.setdefault(date_key, []).append(
            {
                "id": str(task.msg_id),
                "task": task,
                "selected": str(task.msg_id) in selected_ids,
            }
        )
    config = request.app.state.config
    return {
        "group_name": selection.group_name,
        "sheet_name": selection.sheet_name,
        "start_date": selection.start_date.isoformat() if selection.start_date else "",
        "end_date": selection.end_date.isoformat() if selection.end_date else "",
        "parsed_tasks": tasks,
        "task_groups": [
            {"date": date_key, "rows": rows}
            for date_key, rows in sorted(grouped.items())
        ],
        "selected_task_ids": selected_ids,
        "task_count": len(tasks),
        "sheet_names": state.get("catalog_sheet_names") or (),
        "default_output_path": wizard.output_path
        or f"任务记录_{date.today().isoformat()}.xlsx",  # noqa: DTZ011
        "enable_ai": wizard.enable_ai,
        "enable_voice": wizard.enable_voice,
        "privacy_acknowledged": wizard.privacy_acknowledged,
        "ai_provider": config.ai.provider,
        "voice_provider": config.voice.transcriber,
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

    client_found = bool(info.version or info.install_path or info.exe_path)
    client_label = (
        f"版本 {info.version}"
        if info.version
        else "已发现微信客户端" if client_found else "未检测到微信安装"
    )
    return {
        "items": [
            _environment_item(
                "微信客户端",
                client_label,
                str(info.install_path or info.exe_path or "未发现安装路径"),
                ok=client_found,
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

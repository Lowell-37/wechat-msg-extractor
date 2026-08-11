import os
import sqlite3
import tempfile
import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from core import dbutils
from core.dbutils import DecryptedDB
from core.validation import (
    ValidationError,
    parse_date_range,
    resolve_output_path,
    validate_sheet_name,
)
from routers.wizard import (
    build_connection_view_model,
    build_preview_view_model,
    build_selection_view_model,
    build_wizard_context,
)
from schemas.wizard import WizardStep
from services import export as export_service
from services.catalog import filter_catalog, find_catalog_option, load_catalog
from services.preview import build_legacy_preview, build_preview
from services.session import dispose_session, get_session, new_session_state, session_state
from services.template_store import TemplateUploadError, activate_template
from services.wechat_explorer import DEFAULT_EXPLORER_BASE_URL
from services.wizard import (
    get_wizard,
    has_complete_selection,
    invalidate_workbook_state,
    mark_connected,
    request_step,
    store_export_preferences,
    update_session_selection,
)

router = APIRouter()


@router.get("/api/wechat/status")
async def wechat_status(request: Request):
    session_id, state = get_session(request)
    context = build_connection_view_model(request, state, refresh=True)
    response = _template(request, "fragments/wechat_status.html", context)
    return _with_session_cookie(request, response, session_id)


@router.post("/api/key/extract")
async def extract_key(request: Request):
    return _store_connection(
        request,
        None,
        "密钥提取并验证通过",
    )


@router.post("/api/key/validate")
async def validate_key(request: Request, key: str = Form(...)):
    return _store_connection(request, key, "验证通过")


@router.post("/api/template")
async def upload_template(
    request: Request, template: Annotated[UploadFile, File()]
):
    session_id, state = get_session(request)
    try:
        activation = activate_template(
            template.file,
            template.filename or "",
            base_dir=request.app.state.base_dir,
            config_path=request.app.state.config_path,
            config=request.app.state.config,
        )
    except TemplateUploadError as exc:
        response = _connection_template(
            request,
            state,
            template_status={"tone": "error", "message": str(exc)},
            status_code=400,
        )
        return _with_session_cookie(request, response, session_id)

    for current_state in session_state.values():
        invalidate_workbook_state(current_state)
    response = _connection_template(
        request,
        state,
        template_status={
            "tone": "success",
            "message": (
                f"已启用 {activation.filename}，"
                f"{len(activation.sheet_names)} 个可用 Sheet。"
            ),
        },
    )
    return _with_session_cookie(request, response, session_id)


@router.get("/api/chatrooms/list")
async def list_chatrooms(request: Request):
    session_id, state = get_session(request)
    if not state.get("ddb"):
        return _session_template_error(
            request,
            session_id,
            "请先完成鉴权步骤",
            401,
        )
    try:
        options = load_catalog(state, request.app.state.config.excel.template_path)
    except Exception as exc:  # noqa: BLE001
        return _session_template_error(
            request,
            session_id,
            f"查询群聊失败：{exc}",
            500,
        )
    response = _template(
        request,
        "fragments/chatroom_list.html",
        {"options": options},
    )
    return _with_session_cookie(request, response, session_id)


@router.get("/api/chatrooms/search")
async def search_chatrooms(request: Request, query: str = ""):
    session_id, state = get_session(request)
    if not state.get("ddb"):
        return _session_template_error(
            request,
            session_id,
            "请先完成鉴权",
            401,
        )
    try:
        if not isinstance(state.get("chatroom_catalog"), tuple):
            load_catalog(state, request.app.state.config.excel.template_path)
        options = filter_catalog(state, query)
    except Exception as exc:  # noqa: BLE001
        return _session_template_error(
            request,
            session_id,
            f"查询群聊失败：{exc}",
            500,
        )
    response = _template(
        request,
        "fragments/chatroom_rows.html",
        {"options": options},
    )
    return _with_session_cookie(request, response, session_id)


@router.get("/api/catalog")
async def catalog(
    request: Request,
    query: str = "",
    group_id: str = "",
    sheet_name: str = "",
):
    session_id, state = get_session(request)
    if not state.get("ddb"):
        return _session_template_error(request, session_id, "请先完成鉴权", 401)
    if not isinstance(state.get("chatroom_catalog"), tuple):
        return _session_template_error(
            request, session_id, "群聊缓存尚未就绪，请重新进入选择步骤", 409
        )
    options = filter_catalog(state, query)
    response = _template(
        request,
        "components/chatroom_rows.html",
        {
            "options": options,
            "sheet_names": state.get("catalog_sheet_names", ()),
            "group_id": group_id,
            "sheet_name": sheet_name,
        },
    )
    return _with_session_cookie(request, response, session_id)


@router.post("/api/selection")
async def selection(
    request: Request,
    group_id: str = Form(""),
    group_name: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    sheet_name: str = Form(""),
    query: str = Form(""),
):
    session_id, state = get_session(request)
    wizard = get_wizard(state)
    values = {
        "group_id": group_id,
        "group_name": group_name,
        "start_date": start_date,
        "end_date": end_date,
        "sheet_name": sheet_name,
        "query": query,
    }
    error = "" if wizard.connected and state.get("ddb") else "请先完成鉴权"
    if not error:
        try:
            option = find_catalog_option(state, group_id, group_name)
            start_value, end_value = parse_date_range(start_date, end_date)
            sheet_value = validate_sheet_name(
                sheet_name, state.get("catalog_sheet_names", ())
            )
            update_session_selection(
                state,
                option.chat_id,
                option.display_name,
                start_value,
                end_value,
                sheet_value,
            )
        except ValidationError as exc:
            error = str(exc)
    status_code = 422 if error else 200

    context = build_wizard_context(WizardStep.SELECT, wizard)
    context["include_oob_updates"] = True
    context.update(
        build_selection_view_model(
            request,
            state,
            values=values,
            validation_error=error,
            next_enabled=not error,
        )
    )
    response = _template(
        request, "steps/select.html", context, status_code=status_code
    )
    return _with_session_cookie(request, response, session_id)


@router.post("/api/preview")
async def preview(
    request: Request,
    selection_source: str = Form(""),
    group_id: str = Form(""),
    group_name: str = Form(""),
    sheet_name: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
):
    session_id, state = get_session(request)
    if not state.get("ddb"):
        return _session_template_error(
            request, session_id, "请先完成鉴权", 401
        )
    wizard_selection = get_wizard(state).selection
    if selection_source == "wizard" and not has_complete_selection(
        wizard_selection
    ):
        return _session_template_error(
            request, session_id, "选择尚未完成或已过期", 400
        )
    try:
        if selection_source == "wizard":
            find_catalog_option(
                state, wizard_selection.group_id, wizard_selection.group_name
            )
            validate_sheet_name(
                wizard_selection.sheet_name,
                state.get("catalog_sheet_names", ()),
            )
            result = build_preview(
                state,
                wizard_selection.group_id,
                wizard_selection.group_name,
                wizard_selection.start_date.isoformat(),
                wizard_selection.end_date.isoformat(),
                wizard_selection.sheet_name,
            )
        elif group_id:
            result = build_preview(
                state,
                group_id,
                group_name,
                start_date,
                end_date,
                sheet_name,
            )
        else:
            result = build_legacy_preview(
                state,
                group_name,
                start_date,
                end_date,
                sheet_name,
            )
    except ValidationError as exc:
        return _session_template_error(request, session_id, str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _session_template_error(
            request,
            session_id,
            f"查询消息失败：{exc}",
            500,
        )

    request_step(get_wizard(state), WizardStep.PREVIEW)
    context = build_wizard_context(WizardStep.PREVIEW, get_wizard(state))
    context.update(build_preview_view_model(request, state, parsed_tasks=result.tasks))
    context["step_template"] = "steps/preview.html"
    response = _template(request, "wizard_fragment.html", context)
    response.headers["HX-Push-Url"] = "/wizard/3"
    return _with_session_cookie(request, response, session_id)


@router.post("/api/export")
async def export(
    request: Request,
    output_path: str = Form(""),
    sheet_name: str = Form(""),
    task_id: Annotated[list[str] | None, Form()] = None,
    task_selection_present: bool = Form(False),
    enable_ai: bool = Form(False),
    enable_voice: bool = Form(False),
    privacy_acknowledged: bool = Form(False),
):
    session_id, state = get_session(request)
    active_job_id = export_service.active_job_id(session_id)
    if active_job_id:
        response = _template(
            request,
            "components/export_progress.html",
            _export_progress_context(
                job_id=active_job_id,
                state="active",
                message="导出任务正在进行，已恢复当前进度。",
                retry_values=_stored_export_values(state),
            ),
            status_code=409,
        )
        return _with_session_cookie(request, response, session_id)

    parsed_tasks = state.get("parsed_tasks", [])
    sheet_name = sheet_name or state.get("selected_sheet", "")
    wizard = get_wizard(state)
    selected_ids = list(dict.fromkeys(task_id or []))
    if not selected_ids and not task_selection_present:
        selected_ids = wizard.selected_task_ids or [
            str(task.msg_id) for task in parsed_tasks
        ]
    retry_values = {
        "task_ids": selected_ids,
        "output_path": output_path,
        "sheet_name": sheet_name,
        "enable_ai": enable_ai,
        "enable_voice": enable_voice,
        "privacy_acknowledged": privacy_acknowledged,
    }
    store_export_preferences(
        wizard,
        selected_task_ids=selected_ids,
        output_path=output_path,
        enable_ai=enable_ai,
        enable_voice=enable_voice,
        privacy_acknowledged=privacy_acknowledged,
    )
    if (enable_ai or enable_voice) and not privacy_acknowledged:
        return _export_error(
            request,
            session_id,
            "请先确认可能发送到外部服务的数据、提供商与处理影响。",
            422,
            retry_values,
        )
    if not parsed_tasks:
        return _export_error(
            request, session_id, "没有可导出的任务", 400, retry_values
        )
    if not selected_ids:
        return _export_error(
            request, session_id, "请至少选择一条任务", 422, retry_values
        )
    tasks_by_id = {str(task.msg_id): task for task in parsed_tasks}
    if any(identifier not in tasks_by_id for identifier in selected_ids):
        return _export_error(
            request,
            session_id,
            "任务选择已过期，请返回预览后重试",
            409,
            retry_values,
        )
    selected_tasks = [tasks_by_id[identifier] for identifier in selected_ids]
    if not sheet_name:
        return _export_error(
            request, session_id, "未指定目标 Sheet", 400, retry_values
        )
    try:
        start_value, end_value = parse_date_range(
            state.get("start_date"), state.get("end_date")
        )
    except ValidationError as exc:
        return _export_error(
            request, session_id, str(exc), 400, retry_values
        )

    config = request.app.state.config
    excel_path = config.excel.template_path
    if not os.path.exists(excel_path):
        return _export_error(
            request,
            session_id,
            f"Excel 模板不存在：{excel_path}",
            400,
            retry_values,
        )

    writer = None
    try:
        writer = request.app.state.writer_factory(excel_path)
        sheet_value = validate_sheet_name(
            sheet_name, writer.get_sheet_names()
        )
    except ValidationError as exc:
        return _export_error(
            request, session_id, str(exc), 400, retry_values
        )
    except Exception as exc:  # noqa: BLE001
        return _export_error(
            request,
            session_id,
            f"无法读取 Excel 模板：{exc}",
            400,
            retry_values,
        )
    finally:
        if writer is not None:
            writer.close()

    try:
        output_value = str(
            resolve_output_path(
                output_path,
                config.excel.output_dir,
                f"任务记录_{date.today().isoformat()}.xlsx",  # noqa: DTZ011
            )
        )
    except ValidationError as exc:
        return _export_error(
            request, session_id, str(exc), 400, retry_values
        )

    database = state.get("ddb")
    if not database:
        return _export_error(
            request, session_id, "请先完成鉴权", 401, retry_values
        )
    group_value = state.get("selected_group")
    if not group_value:
        return _export_error(
            request, session_id, "未指定群聊", 400, retry_values
        )

    job_id = str(uuid.uuid4())
    job = export_service.create_export_job(
        job_id=job_id,
        session_id=session_id,
        database=database,
        group=group_value,
        sheet_name=sheet_value,
        template_path=excel_path,
        output_path=output_value,
        start_date=start_value,
        end_date=end_value,
        parsed_tasks=selected_tasks,
        analysis_by_date=state.get("analysis_by_date", {}),
        enable_ai=enable_ai,
        enable_voice=enable_voice,
    )
    try:
        request.app.state.start_export_job(job)
    except Exception as exc:
        request.app.state.logger.exception("Unable to start export job")
        return _export_error(
            request,
            session_id,
            f"导出启动失败：{exc}",
            500,
            retry_values,
        )
    response = _template(
        request,
        "components/export_progress.html",
        _export_progress_context(
            job_id=job_id,
            state="active",
            message="正在准备导出...",
            retry_values=retry_values,
        ),
        status_code=202,
    )
    return _with_session_cookie(request, response, session_id)


@router.get("/api/progress/stream")
async def progress_stream(request: Request, job_id: str):
    session_id, _ = get_session(request)
    if not export_service.owns_job(job_id, session_id):
        return _session_template_error(
            request,
            session_id,
            "导出任务不存在",
            404,
        )

    async def generate():
        try:
            async for event_data in request.app.state.progress_hub.event_stream(
                job_id
            ):
                yield event_data
        finally:
            export_service.release_progress_owner(job_id, session_id)

    return StreamingResponse(generate(), media_type="text/event-stream")


def _store_connection(
    request: Request,
    key: str | None,
    success_message: str,
):
    session_id, state = get_session(request)
    if export_service.has_active_job(session_id):
        response = _connection_template(
            request,
            state,
            {
                "tone": "error",
                "icon": "!",
                "heading": "暂时无法重新连接",
                "label": "失败原因：",
                "message": "导出任务正在进行。",
                "recovery": "等待当前导出结束后再重试。",
                "retry_url": "/api/key/extract",
            },
            status_code=409,
        )
        return _with_session_cookie(request, response, session_id)

    connection_context = build_connection_view_model(request, state)
    try:
        config = request.app.state.config
        connection_kwargs = {}
        if (
            config.wechat.version_dir is not None
            or config.wechat.data_dir is not None
        ):
            connection_kwargs = {
                "install_path": config.wechat.version_dir,
                "data_dir": config.wechat.data_dir,
            }
        if config.wechat.explorer_base_url != DEFAULT_EXPLORER_BASE_URL:
            connection_kwargs["explorer_base_url"] = (
                config.wechat.explorer_base_url
            )
        connected = request.app.state.connect_wechat(key, **connection_kwargs)

        dispose_session(session_id)
        state = new_session_state()
        session_state[session_id] = state
        state["wechat_environment"] = {
            "items": connection_context["environment_items"],
            "warnings": connection_context["environment_warnings"],
        }
        state["wdb"] = connected.manager
        state["ddb"] = connected.database
        mark_connected(get_wizard(state))
        response = _connection_template(
            request,
            state,
            {
                "tone": "success",
                "icon": "✓",
                "heading": "连接成功",
                "label": "验证结果：",
                "message": (
                    f"{success_message}，已验证 {connected.shard_count} 个消息分片、"
                    f"{connected.table_count} 张表。"
                ),
                "recovery": "",
                "retry_url": "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        recovery = (
            "请运行本机安全凭据设置，或临时设置环境变量后重试。"
            if "WECHATEXPLORER_API_TOKEN" in message or "WechatExplorer" in message
            else "确认微信已启动并登录；如自动连接仍失败，可在高级选项中输入密钥。"
        )
        response = _connection_template(
            request,
            state,
            {
                "tone": "error",
                "icon": "!",
                "heading": "连接失败",
                "label": "失败原因：",
                "message": message,
                "recovery": recovery,
                "retry_url": "/api/key/extract",
            },
            status_code=400,
        )
    return _with_session_cookie(request, response, session_id)


def _connection_template(
    request: Request,
    state: dict[str, Any],
    connection_status: dict[str, str] | None = None,
    *,
    template_status: dict[str, str] | None = None,
    status_code: int = 200,
):
    wizard = get_wizard(state)
    context = build_wizard_context(WizardStep.CONNECT, wizard)
    context["include_oob_updates"] = True
    context.update(
        build_connection_view_model(
            request,
            state,
            connection_status=connection_status,
        )
    )
    context["template_status"] = template_status
    return _template(
        request,
        "steps/connect.html",
        context,
        status_code=status_code,
    )


def _template(
    request: Request,
    name: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
):
    return request.app.state.templates.TemplateResponse(
        request,
        name,
        context,
        status_code=status_code,
    )


def _stored_export_values(state: dict[str, Any]) -> dict[str, Any]:
    wizard = get_wizard(state)
    return {
        "task_ids": wizard.selected_task_ids,
        "output_path": wizard.output_path,
        "sheet_name": wizard.selection.sheet_name or state.get("selected_sheet", ""),
        "enable_ai": wizard.enable_ai,
        "enable_voice": wizard.enable_voice,
        "privacy_acknowledged": wizard.privacy_acknowledged,
    }


def _export_progress_context(
    *,
    job_id: str,
    state: str,
    message: str,
    retry_values: dict[str, Any],
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "export_state": state,
        "progress": 0,
        "progress_message": message,
        "retry_values": retry_values,
    }


def _export_error(
    request: Request,
    session_id: str,
    message: str,
    status_code: int,
    retry_values: dict[str, Any],
):
    response = _template(
        request,
        "components/export_progress.html",
        _export_progress_context(
            job_id="",
            state="failed",
            message=message,
            retry_values=retry_values,
        ),
        status_code=status_code,
    )
    return _with_session_cookie(request, response, session_id)


def _session_template_error(
    request: Request,
    session_id: str,
    message: str,
    status_code: int,
):
    response = _template(
        request,
        "fragments/status_message.html",
        {"status_class": "status-err", "message": message},
        status_code=status_code,
    )
    return _with_session_cookie(request, response, session_id)


def _with_session_cookie(request: Request, response: Any, session_id: str):
    if request.cookies.get("session_id") != session_id:
        response.set_cookie(key="session_id", value=session_id)
    return response


def _get_chatrooms(
    ddb: DecryptedDB,
) -> list[tuple[str, str, int | None, int]]:
    """Return chatroom identities and one-time message metadata."""
    query_chatrooms = getattr(ddb, "query_chatrooms", None)
    if callable(query_chatrooms):
        return query_chatrooms()
    chatroom_ids: set[str] = set()
    metadata: dict[str, tuple[int | None, int]] = {}
    try:
        metadata_sql = (
            "SELECT StrTalker, MAX(CreateTime) AS LastMessageAt, "
            "COUNT(*) AS MessageCount FROM MSG "
            "WHERE StrTalker LIKE '%@chatroom' GROUP BY StrTalker"
        )
        execute_per_shard = getattr(ddb, "execute_per_shard", None)
        shard_results = (
            execute_per_shard(metadata_sql)
            if callable(execute_per_shard)
            else [ddb.execute(metadata_sql)]
        )
        rows = (row for shard_rows in shard_results for row in shard_rows)
        for row in rows:
            chatroom_id = row["StrTalker"]
            if chatroom_id:
                chatroom_ids.add(chatroom_id)
                previous_last, previous_count = metadata.get(
                    chatroom_id, (None, 0)
                )
                current_last = row["LastMessageAt"]
                latest = max(
                    timestamp
                    for timestamp in (previous_last, current_last)
                    if timestamp is not None
                )
                metadata[chatroom_id] = (
                    latest,
                    previous_count + row["MessageCount"],
                )
    except Exception:  # noqa: BLE001 - optional MSG discovery may fail by schema
        request_logger().debug("MSG chatroom discovery failed", exc_info=True)

    name_map: dict[str, str] = {}
    parent = os.path.dirname(ddb.original_path)
    if os.path.basename(parent) == "Multi":
        parent = os.path.dirname(parent)

    micro_candidates = [
        os.path.join(parent, filename)
        for filename in os.listdir(parent)
        if "MicroMsg" in filename
        and filename.endswith(".db")
        and os.path.isfile(os.path.join(parent, filename))
    ]
    grandparent = os.path.dirname(parent)
    if grandparent and os.path.isdir(grandparent):
        micro_candidates.extend(
            os.path.join(grandparent, filename)
            for filename in os.listdir(grandparent)
            if "MicroMsg" in filename
            and filename.endswith(".db")
            and os.path.isfile(os.path.join(grandparent, filename))
        )

    for micro_path in micro_candidates:
        descriptor, plaintext_path = tempfile.mkstemp(suffix=".db")
        os.close(descriptor)
        connection = None
        try:
            if not dbutils.decrypt_db_raw(
                ddb.key_hex, micro_path, plaintext_path
            ):
                os.unlink(plaintext_path)
                continue
            connection = sqlite3.connect(plaintext_path)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT UserName, NickName, Remark FROM Contact "
                "WHERE UserName LIKE '%@chatroom'"
            ).fetchall()
            for row in rows:
                chatroom_id = row["UserName"]
                chatroom_ids.add(chatroom_id)
                name = row["Remark"] or row["NickName"] or ""
                if name.strip():
                    name_map[chatroom_id] = name.strip()

            rows = connection.execute(
                "SELECT ChatRoomName, DisplayNameList FROM ChatRoom"
            ).fetchall()
            for row in rows:
                chatroom_id = row["ChatRoomName"]
                if chatroom_id not in name_map:
                    chatroom_ids.add(chatroom_id)
                    display_name = row["DisplayNameList"] or ""
                    if display_name.strip() and display_name != "\u0007\u0007":
                        name_map[chatroom_id] = display_name.strip()
            break
        except Exception:  # noqa: BLE001 - optional MicroMsg schemas vary
            request_logger().debug(
                "MicroMsg chatroom discovery failed", exc_info=True
            )
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # noqa: BLE001 - cleanup is best effort
                    request_logger().warning(
                        "Failed to close MicroMsg database", exc_info=True
                    )
            try:
                os.unlink(plaintext_path)
            except Exception:  # noqa: BLE001 - cleanup is best effort
                request_logger().debug(
                    "Failed to remove MicroMsg plaintext", exc_info=True
                )

    return [
        (
            chatroom_id,
            name_map.get(chatroom_id, chatroom_id),
            metadata.get(chatroom_id, (None, 0))[0],
            metadata.get(chatroom_id, (None, 0))[1],
        )
        for chatroom_id in sorted(chatroom_ids)
    ]


def _get_messages(
    ddb: DecryptedDB,
    chat_id: str,
    start_date: date,
    end_date: date,
    task_only: bool = False,
) -> list[dict[str, Any]]:
    start_ts = int(
        datetime(  # noqa: DTZ001 - WeChat stores local timestamps
            start_date.year, start_date.month, start_date.day
        ).timestamp()
    )
    end_ts = int(
        datetime(  # noqa: DTZ001 - WeChat stores local timestamps
            end_date.year,
            end_date.month,
            end_date.day,
            23,
            59,
            59,
        ).timestamp()
    )
    query_messages = getattr(ddb, "query_messages", None)
    if callable(query_messages):
        rows = query_messages(chat_id, start_ts, end_ts)
    else:
        rows = ddb.execute(
            "SELECT localId, Type, SubType, IsSender, CreateTime, "
            "StrContent, StrTalker FROM MSG WHERE StrTalker=? "
            "AND CreateTime BETWEEN ? AND ? AND Type=1 "
            "ORDER BY CreateTime ASC",
            (chat_id, start_ts, end_ts),
        )
    parser = None
    messages = []
    for row in rows:
        content = row["StrContent"] or ""
        if task_only:
            if parser is None:
                from core.task_parser import TaskParser

                parser = TaskParser()
            if not parser.is_task_message(content):
                continue
        messages.append(
            {
                "msg_id": row["localId"],
                "content": content,
                "timestamp": row["CreateTime"],
            }
        )
    messages.sort(key=lambda message: message["timestamp"])
    return messages


def request_logger():
    import logging

    return logging.getLogger("app")

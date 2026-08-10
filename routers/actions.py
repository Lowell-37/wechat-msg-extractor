import os
import sqlite3
import tempfile
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import StreamingResponse

from core import dbutils
from core.dbutils import DecryptedDB
from core.validation import (
    ValidationError,
    parse_date_range,
    resolve_output_path,
    validate_sheet_name,
)
from routers.wizard import build_connection_view_model, build_wizard_context
from schemas.wizard import WizardStep
from services import export as export_service
from services.catalog import filter_catalog, load_catalog
from services.preview import build_legacy_preview
from services.session import dispose_session, get_session, new_session_state, session_state
from services.wizard import get_wizard, mark_connected

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


@router.post("/api/preview")
async def preview(
    request: Request,
    group_name: str = Form(...),
    sheet_name: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(...),
):
    session_id, state = get_session(request)
    try:
        parse_date_range(start_date, end_date)
    except ValidationError as exc:
        return _session_template_error(
            request, session_id, str(exc), 400
        )
    if not state.get("ddb"):
        return _session_template_error(
            request, session_id, "请先完成鉴权", 401
        )
    try:
        result = build_legacy_preview(
            state,
            group_name,
            start_date,
            end_date,
            sheet_name,
        )
    except Exception as exc:  # noqa: BLE001
        return _session_template_error(
            request,
            session_id,
            f"查询消息失败：{exc}",
            500,
        )

    sheet_names = []
    writer = None
    try:
        writer = request.app.state.writer_factory(
            request.app.state.config.excel.template_path
        )
        sheet_names = writer.get_sheet_names()
    except Exception:
        request.app.state.logger.debug(
            "Excel sheet discovery failed", exc_info=True
        )
    finally:
        if writer is not None:
            writer.close()

    response = _template(
        request,
        "fragments/preview_result.html",
        {
            "group_name": group_name,
            "sheet_name": sheet_name,
            "start_date": start_date,
            "end_date": end_date,
            "parsed_tasks": result.tasks,
            "sheet_names": sheet_names,
            "default_output_path": f"任务记录_{date.today().isoformat()}.xlsx",  # noqa: DTZ011
        },
    )
    return _with_session_cookie(request, response, session_id)


@router.post("/api/export")
async def export(
    request: Request,
    output_path: str = Form(""),
    sheet_name: str = Form(""),
):
    session_id, state = get_session(request)
    if export_service.has_active_job(session_id):
        return _session_template_error(
            request,
            session_id,
            "导出任务正在进行，请稍后重试",
            409,
        )

    parsed_tasks = state.get("parsed_tasks", [])
    sheet_name = sheet_name or state.get("selected_sheet", "")
    if not parsed_tasks:
        return _session_template_error(
            request, session_id, "没有可导出的任务", 400
        )
    if not sheet_name:
        return _session_template_error(
            request, session_id, "未指定目标 Sheet", 400
        )
    try:
        start_value, end_value = parse_date_range(
            state.get("start_date"), state.get("end_date")
        )
    except ValidationError as exc:
        return _session_template_error(
            request, session_id, str(exc), 400
        )

    config = request.app.state.config
    excel_path = config.excel.template_path
    if not os.path.exists(excel_path):
        return _session_template_error(
            request,
            session_id,
            f"Excel 模板不存在：{excel_path}",
            400,
        )

    writer = None
    try:
        writer = request.app.state.writer_factory(excel_path)
        sheet_value = validate_sheet_name(
            sheet_name, writer.get_sheet_names()
        )
    except ValidationError as exc:
        return _session_template_error(
            request, session_id, str(exc), 400
        )
    except Exception as exc:  # noqa: BLE001
        return _session_template_error(
            request,
            session_id,
            f"无法读取 Excel 模板：{exc}",
            400,
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
        return _session_template_error(
            request, session_id, str(exc), 400
        )

    database = state.get("ddb")
    if not database:
        return _session_template_error(
            request, session_id, "请先完成鉴权", 401
        )
    group_value = state.get("selected_group")
    if not group_value:
        return _session_template_error(
            request, session_id, "未指定群聊", 400
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
        parsed_tasks=parsed_tasks,
        analysis_by_date=state.get("analysis_by_date", {}),
    )
    request.app.state.start_export_job(job)
    response = _template(
        request,
        "fragments/export_progress.html",
        {"job_id": job_id},
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
        response = _connection_template(
            request,
            state,
            {
                "tone": "error",
                "icon": "!",
                "heading": "连接失败",
                "label": "失败原因：",
                "message": str(exc),
                "recovery": "确认微信已启动并登录；如自动连接仍失败，可在高级选项中输入密钥。",
                "retry_url": "/api/key/extract",
            },
            status_code=400,
        )
    return _with_session_cookie(request, response, session_id)


def _connection_template(
    request: Request,
    state: dict[str, Any],
    connection_status: dict[str, str],
    *,
    status_code: int = 200,
):
    wizard = get_wizard(state)
    context = build_wizard_context(WizardStep.CONNECT, wizard)
    context.update(
        build_connection_view_model(
            request,
            state,
            connection_status=connection_status,
        )
    )
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


def _get_chatrooms(ddb: DecryptedDB) -> list[tuple[str, str]]:
    """Return chatroom IDs and display names from MSG and MicroMsg data."""
    chatroom_ids: set[str] = set()
    try:
        rows = ddb.execute(
            "SELECT DISTINCT StrTalker FROM MSG "
            "WHERE StrTalker LIKE '%@chatroom'"
        )
        chatroom_ids.update(
            row["StrTalker"] for row in rows if row["StrTalker"]
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
        (chatroom_id, name_map.get(chatroom_id, chatroom_id))
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

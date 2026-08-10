# app.py
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import AppConfig
from core.ai_analyzer import create_analyzer
from core.connection import connect_wechat
from core.dbutils import DecryptedDB
from core.excel_writer import ExcelWriter
from core.matcher import SheetMatcher
from core.progress import ProgressEvent, progress_hub
from core.scanner import WeChatScanner
from core.task_parser import ParsedTask, TaskParser
from core.validation import (
    ValidationError,
    parse_date_range,
    resolve_output_path,
    validate_sheet_name,
)
from core.voice import VoiceTranscriber
from schemas.catalog import CatalogDependencies, PreviewDependencies
from schemas.wizard import WizardState

# --- App Setup ---
BASE_DIR = Path(__file__).parent
config = AppConfig.from_yaml()
app = FastAPI(title="微信聊天提取工具")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- In-memory state (per-session) ---
logger = logging.getLogger(__name__)
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 128
session_state: dict[str, dict[str, Any]] = {}
export_tasks: dict[str, Any] = {}
session_jobs: dict[str, str] = {}
job_owners: dict[str, str] = {}


@dataclass(frozen=True)
class ExportTaskSnapshot:
    msg_id: int
    date: date
    date_excel_serial: int
    raw_text: str
    tasks: tuple[str, ...]


@dataclass(frozen=True)
class ExportJob:
    job_id: str
    session_id: str
    database: Any
    group: str
    sheet_name: str
    template_path: str
    output_path: str
    start_date: date
    end_date: date
    parsed_tasks: tuple[ExportTaskSnapshot, ...]
    analysis_by_date: tuple[tuple[str, tuple[str, ...]], ...]


def _snapshot_parsed_tasks(tasks: list[ParsedTask]) -> tuple[ExportTaskSnapshot, ...]:
    return tuple(
        ExportTaskSnapshot(
            msg_id=task.msg_id,
            date=task.date,
            date_excel_serial=task.date_excel_serial,
            raw_text=task.raw_text,
            tasks=tuple(task.tasks),
        )
        for task in tasks
    )


def _snapshot_analysis(analysis: dict[str, list[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple((date_key, tuple(messages)) for date_key, messages in analysis.items())


def _release_export_job(job: ExportJob) -> None:
    export_tasks.pop(job.job_id, None)
    if session_jobs.get(job.session_id) == job.job_id:
        session_jobs.pop(job.session_id, None)


def _start_export_job(job: ExportJob) -> None:
    progress_hub.register(job.job_id)
    session_jobs[job.session_id] = job.job_id
    job_owners[job.job_id] = job.session_id
    try:
        task = asyncio.create_task(_run_export_job(job))
    except BaseException:
        session_jobs.pop(job.session_id, None)
        job_owners.pop(job.job_id, None)
        progress_hub.unregister(job.job_id)
        raise
    export_tasks[job.job_id] = task


def _session_clock() -> float:
    return time.monotonic()


def _new_session_state(now: float | None = None) -> dict[str, Any]:
    return {
        "wdb": None,
        "ddb": None,
        "selected_group": None,
        "selected_sheet": None,
        "start_date": None,
        "end_date": None,
        "parsed_tasks": [],
        "analysis_by_date": {},
        "chatroom_catalog": None,
        "catalog_sheet_names": (),
        "catalog_dependencies": CatalogDependencies(query_chatrooms=_get_chatrooms),
        "preview_dependencies": PreviewDependencies(fetch_messages=_get_messages),
        "wizard": WizardState(),
        "last_access": _session_clock() if now is None else now,
    }


def _session_has_active_job(session_id: str) -> bool:
    job_id = session_jobs.get(session_id)
    if not job_id:
        return False
    task = export_tasks.get(job_id)
    if task is None:
        session_jobs.pop(session_id, None)
        export_tasks.pop(job_id, None)
        return False
    done = getattr(task, "done", None)
    if done is None or not done():
        return True
    session_jobs.pop(session_id, None)
    export_tasks.pop(job_id, None)
    return False


def dispose_session(session_id: str) -> None:
    state = session_state.pop(session_id, None)
    if state is not None:
        database = state.get("ddb")
        if database is not None:
            try:
                database.close_all()
            except Exception:
                logger.warning(
                    "Failed to close session database %s", session_id, exc_info=True
                )
    session_jobs.pop(session_id, None)
    for job_id, owner in list(job_owners.items()):
        if owner == session_id:
            job_owners.pop(job_id, None)
            progress_hub.unregister(job_id)


def _evict_sessions(now: float, reserve: int = 0) -> None:
    expired = [
        session_id
        for session_id, state in session_state.items()
        if now - state.get("last_access", 0.0) > SESSION_TTL_SECONDS
        and not _session_has_active_job(session_id)
    ]
    for session_id in expired:
        dispose_session(session_id)

    target = max(0, MAX_SESSIONS - reserve)
    while len(session_state) > target:
        idle = [
            (state.get("last_access", 0.0), session_id)
            for session_id, state in session_state.items()
            if not _session_has_active_job(session_id)
        ]
        if not idle:
            break
        _, oldest_id = min(idle)
        dispose_session(oldest_id)


async def shutdown_resources() -> None:
    tasks = [task for task in export_tasks.values() if isinstance(task, asyncio.Task)]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    export_tasks.clear()
    session_jobs.clear()
    job_owners.clear()
    progress_hub.clear()
    for session_id in list(session_state):
        dispose_session(session_id)


app.add_event_handler("shutdown", shutdown_resources)


def _error_fragment(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        f'<p class="status-err">{escape(message)}</p>',
        status_code=status_code,
    )


def _validation_error(message: str) -> HTMLResponse:
    return _error_fragment(message, 400)


def get_session(request: Request):
    now = _session_clock()
    _evict_sessions(now)
    session_id = request.cookies.get("session_id")
    if session_id and session_id in session_state:
        state = session_state[session_id]
        state["last_access"] = now
        return session_id, state

    _evict_sessions(now, reserve=1)
    session_id = str(uuid.uuid4())
    state = _new_session_state(now)
    session_state[session_id] = state
    return session_id, state


# --- Helper: query chatrooms ---
def _get_chatrooms(ddb: DecryptedDB) -> list:
    """从 MSG 表和 MicroMsg.db 获取群聊列表。返回 [(chatroom_id, display_name), ...]"""
    import os
    import sqlite3
    import tempfile

    from core.dbutils import decrypt_db_raw

    # Step 1: 从 MSG 表获取所有有消息记录的群聊 ID
    chatroom_ids = set()
    try:
        rows = ddb.execute(
            "SELECT DISTINCT StrTalker FROM MSG WHERE StrTalker LIKE '%@chatroom'"
        )
        chatroom_ids.update(r["StrTalker"] for r in rows if r["StrTalker"])
    except Exception:
        logger.debug("MSG chatroom discovery failed", exc_info=True)

    # Step 2: 从 MicroMsg.db 获取更多群聊 ID 和显示名称
    name_map = {}  # chatroom_id -> display_name

    # 推导 MicroMsg.db 路径（MSG.db 在 Msg/Multi/ 或 Msg/ 下）
    parent = os.path.dirname(ddb.original_path)
    if os.path.basename(parent) == "Multi":
        parent = os.path.dirname(parent)

    micro_candidates = []
    for f in os.listdir(parent):
        if "MicroMsg" in f and f.endswith(".db") and os.path.isfile(os.path.join(parent, f)):
            micro_candidates.append(os.path.join(parent, f))
    grandparent = os.path.dirname(parent)
    if grandparent and os.path.isdir(grandparent):
        for f in os.listdir(grandparent):
            if "MicroMsg" in f and f.endswith(".db") and os.path.isfile(os.path.join(grandparent, f)):
                micro_candidates.append(os.path.join(grandparent, f))

    for micro_path in micro_candidates:
        tf, tmp = tempfile.mkstemp(suffix=".db")
        os.close(tf)
        conn = None
        try:
            if not decrypt_db_raw(ddb.key_hex, micro_path, tmp):
                os.unlink(tmp)
                continue
            conn = sqlite3.connect(tmp)
            conn.row_factory = sqlite3.Row

            # 从 Contact 表获取群聊 ID（含无消息记录的群）和昵称
            rows = conn.execute(
                "SELECT UserName, NickName, Remark FROM Contact WHERE UserName LIKE '%@chatroom'"
            ).fetchall()
            for r in rows:
                uid = r["UserName"]
                chatroom_ids.add(uid)  # 补充 MSG 表中没有的群聊
                name = r["Remark"] or r["NickName"] or ""
                if name and name.strip():
                    name_map[uid] = name.strip()

            # ChatRoom 表 DisplayNameList 作为备用名称来源
            rows = conn.execute(
                "SELECT ChatRoomName, DisplayNameList FROM ChatRoom"
            ).fetchall()
            for r in rows:
                uid = r["ChatRoomName"]
                if uid not in name_map:
                    chatroom_ids.add(uid)
                    display = r["DisplayNameList"] or ""
                    if display.strip() and display != "\u0007\u0007":
                        name_map[uid] = display.strip()

            break
        except Exception:
            logger.debug("MicroMsg chatroom discovery failed", exc_info=True)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.warning("Failed to close MicroMsg database", exc_info=True)
            try:
                os.unlink(tmp)
            except Exception:
                logger.debug("Failed to remove MicroMsg plaintext", exc_info=True)

    # Step 3: 返回带显示名称的列表
    return [(cid, name_map.get(cid, cid)) for cid in sorted(chatroom_ids)]


def _get_messages(ddb: DecryptedDB, chat_id: str, start_date: date, end_date: date, task_only: bool = False) -> list:
    """查询聊天记录，返回 dict 列表。"""
    start_ts = int(datetime(start_date.year, start_date.month, start_date.day).timestamp())  # noqa: DTZ001 - WeChat stores local timestamps
    end_ts = int(datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59).timestamp())  # noqa: DTZ001 - WeChat stores local timestamps

    rows = ddb.execute(
        "SELECT localId, Type, SubType, IsSender, CreateTime, StrContent, StrTalker "
        "FROM MSG WHERE StrTalker=? AND CreateTime BETWEEN ? AND ? AND Type=1 "
        "ORDER BY CreateTime ASC",
        (chat_id, start_ts, end_ts),
    )

    messages = []
    for r in rows:
        content = r["StrContent"] or ""
        if task_only:
            parser = TaskParser()
            if not parser.is_task_message(content):
                continue
        messages.append({
            "msg_id": r["localId"],
            "content": content,
            "timestamp": r["CreateTime"],
        })
    # 多分片数据库结果合并后按时间排序
    messages.sort(key=lambda m: m["timestamp"])
    return messages


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    session_id, _ = get_session(request)
    response = templates.TemplateResponse(request, "step1_connect.html", {"step": 1})
    response.set_cookie(key="session_id", value=session_id)
    return response


@app.get("/step/2", response_class=HTMLResponse)
async def step2(request: Request):
    today = date.today()  # noqa: DTZ011 - UI defaults use the host's local date
    return templates.TemplateResponse(request, "step2_select.html", {
        "step": 2,
        "start_date": (today - timedelta(days=30)).isoformat(),
        "end_date": today.isoformat(),
    })


@app.get("/step/3", response_class=HTMLResponse)
async def step3(request: Request):
    return templates.TemplateResponse(request, "step3_preview.html", {"step": 3})


# --- API ---
@app.get("/api/wechat/status")
async def wechat_status():
    scanner = WeChatScanner(
        install_path=config.wechat.version_dir,
        data_dir=config.wechat.data_dir,
    )
    info = scanner.scan()
    parts = []
    if info.version:
        parts.append(
            f'<p class="status-ok">✔ 微信版本：{escape(str(info.version))}</p>'
        )
        parts.append(
            f'<p class="status-ok">✔ 安装路径：{escape(str(info.install_path))}</p>'
        )
    else:
        parts.append('<p class="status-err">✘ 未检测到微信安装</p>')
    if info.pid:
        parts.append(f'<p class="status-ok">✔ 微信进程 PID：{escape(str(info.pid))}</p>')
    else:
        parts.append('<p class="status-err">✘ 微信未运行</p>')
    if info.data_dir:
        parts.append(f'<p class="status-ok">✔ 数据目录：{escape(str(info.data_dir))}</p>')
    else:
        parts.append('<p class="status-err">✘ 未找到数据目录</p>')
    for e in info.errors:
        parts.append(f'<p class="status-warn">⚠ {escape(str(e))}</p>')
    return HTMLResponse("".join(parts))


def _store_connection(request: Request, key: str | None, success_message: str) -> HTMLResponse:
    session_id, _ = get_session(request)
    if _session_has_active_job(session_id):
        return HTMLResponse(
            '<p class="status-err">导出任务正在进行，请稍后重试</p>', status_code=409
        )

    try:
        connection_kwargs = {}
        if config.wechat.version_dir is not None or config.wechat.data_dir is not None:
            connection_kwargs = {
                "install_path": config.wechat.version_dir,
                "data_dir": config.wechat.data_dir,
            }
        connected = connect_wechat(key, **connection_kwargs)

        dispose_session(session_id)
        state = _new_session_state()
        session_state[session_id] = state
        state["wdb"] = connected.manager
        state["ddb"] = connected.database

        response = HTMLResponse(
            f'<p class="status-ok">✔ {success_message}（{connected.table_count} 张表，'
            f'{connected.shard_count} 个分片库）</p>'
            f'<div id="next-step-area" style="display:block;">'
            f'<a href="/step/2" class="btn btn-primary">下一步：选择群聊 →</a></div>'
        )
        if request.cookies.get("session_id") != session_id:
            response.set_cookie(key="session_id", value=session_id)
        return response  # noqa: TRY300 - response cookie is finalized inside this branch
    except Exception as e:  # noqa: BLE001 - convert connection failures to an HTML fragment
        response = HTMLResponse(
            f'<p class="status-err">✘ 连接失败：{escape(str(e))}</p>', status_code=400
        )
        if request.cookies.get("session_id") != session_id:
            response.set_cookie(key="session_id", value=session_id)
        return response


@app.post("/api/key/extract")
async def extract_key(request: Request):
    return _store_connection(request, None, "密钥提取并验证通过")


@app.post("/api/key/validate")
async def validate_key(request: Request, key: str = Form(...)):
    return _store_connection(request, key, "验证通过")


@app.get("/api/chatrooms/list")
async def list_chatrooms(request: Request):
    _, state = get_session(request)
    ddb = state.get("ddb")
    if not ddb:
        return _error_fragment("请先完成鉴权步骤", 401)

    try:
        chatrooms = _get_chatrooms(ddb)
    except Exception as e:  # noqa: BLE001 - API boundary returns an explicit error fragment
        return _error_fragment(f"查询群聊失败：{e}", 500)

    if not chatrooms:
        return HTMLResponse('<p class="status-warn">未找到群聊记录</p>')

    excel_path = config.excel.template_path
    sheet_names = []
    try:
        writer = ExcelWriter(excel_path)
        sheet_names = writer.get_sheet_names()
        writer.close()
    except Exception:
        logger.debug("Excel sheet discovery failed", exc_info=True)

    matcher = SheetMatcher(sheet_names, manual_map=config.matching.group_sheet_map)
    display_names = [c[1] for c in chatrooms]
    matches = matcher.match(display_names)

    items = []
    for cid, display_name in chatrooms:
        matched = matches.get(display_name)
        group_attr = escape(str(cid), quote=True)
        display_text = escape(str(display_name))
        sheet_attr = escape(str(matched or ""), quote=True)
        cls = "matched" if matched else "unmatched"
        sheet_text = escape(str(matched or "未匹配"))
        items.append(
            f'<div class="chatroom-item" onclick="select(this)"'
            f' data-group="{group_attr}" data-sheet="{sheet_attr}">'
            f'<span class="chatroom-name">{display_text}</span>'
            f'<span class="chatroom-sheet {cls}">{sheet_text}</span>'
            f'</div>'
        )

    html = "".join(items)
    html += '<form hx-post="/api/preview" hx-target="#main-content" hx-swap="outerHTML" id="select-form">'
    html += '<input type="hidden" name="group_name" id="selected-group">'
    html += '<input type="hidden" name="sheet_name" id="selected-sheet">'
    html += '<input type="hidden" name="start_date" id="selected-start">'
    html += '<input type="hidden" name="end_date" id="selected-end">'
    html += '</form>'
    html += '<script>'
    html += 'function select(el) {'
    html += '  document.querySelectorAll(".chatroom-item").forEach(e => e.classList.remove("selected"));'
    html += '  el.classList.add("selected");'
    html += '  document.getElementById("selected-group").value = el.dataset.group;'
    html += '  document.getElementById("selected-sheet").value = el.dataset.sheet;'
    html += '  document.getElementById("selected-start").value = document.querySelector("[name=start_date]").value;'
    html += '  document.getElementById("selected-end").value = document.querySelector("[name=end_date]").value;'
    html += '  document.getElementById("select-form").requestSubmit();'
    html += '}'
    html += '</script>'
    return HTMLResponse(html)


@app.get("/api/chatrooms/search")
async def search_chatrooms(request: Request, query: str = ""):
    _, state = get_session(request)
    ddb = state.get("ddb")
    if not ddb:
        return _error_fragment("请先完成鉴权", 401)

    try:
        chatrooms = _get_chatrooms(ddb)
    except Exception as exc:  # noqa: BLE001 - API boundary returns an explicit error fragment
        return _error_fragment(f"查询群聊失败：{exc}", 500)
    if not query:
        return await list_chatrooms(request)

    matched = [c for c in chatrooms if query.lower() in c[0].lower() or query.lower() in c[1].lower()]

    excel_path = config.excel.template_path
    sheet_names = []
    try:
        writer = ExcelWriter(excel_path)
        sheet_names = writer.get_sheet_names()
        writer.close()
    except Exception:
        logger.debug("Excel sheet discovery failed", exc_info=True)

    matcher = SheetMatcher(sheet_names, manual_map=config.matching.group_sheet_map)
    display_names = [c[1] for c in matched]
    mm = matcher.match(display_names)

    items = []
    for cid, display_name in matched:
        s = mm.get(display_name)
        group_attr = escape(str(cid), quote=True)
        display_text = escape(str(display_name))
        sheet_attr = escape(str(s or ""), quote=True)
        sheet_text = escape(str(("→ " + s) if s else "未匹配"))
        items.append(
            f'<div class="chatroom-item" onclick="select(this)"'
            f' data-group="{group_attr}" data-sheet="{sheet_attr}">'
            f'<span class="chatroom-name">{display_text}</span>'
            f'<span class="chatroom-sheet {"matched" if s else "unmatched"}">'
            f'{sheet_text}</span></div>'
        )
    return HTMLResponse("".join(items) or '<p>无匹配群聊</p>')


@app.post("/api/preview")
async def preview(
    request: Request,
    group_name: str = Form(...),
    sheet_name: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(...),
):
    _, state = get_session(request)
    try:
        start_value, end_value = parse_date_range(start_date, end_date)
    except ValidationError as exc:
        return _validation_error(str(exc))

    state["selected_group"] = group_name
    state["selected_sheet"] = sheet_name
    state["start_date"] = start_date
    state["end_date"] = end_date

    ddb = state.get("ddb")
    if not ddb:
        return _error_fragment("请先完成鉴权", 401)

    try:
        messages = _get_messages(ddb, group_name, start_value, end_value, task_only=False)
    except Exception as exc:  # noqa: BLE001 - API boundary returns an explicit error fragment
        return _error_fragment(f"查询消息失败：{exc}", 500)

    parser = TaskParser()
    parsed_tasks = []
    analysis_by_date = {}  # date.isoformat() -> [message_content, ...]
    for msg in messages:
        content = msg["content"]
        if parser.is_task_message(content):
            result = parser.parse(content, msg["msg_id"])
            if result:
                parsed_tasks.append(result)
        else:
            if content.strip():
                msg_date = datetime.fromtimestamp(msg["timestamp"]).date()  # noqa: DTZ006 - WeChat stores local timestamps
                analysis_by_date.setdefault(msg_date.isoformat(), []).append(content.strip())

    # 按任务日期（标题中的日期）排序
    parsed_tasks.sort(key=lambda pt: pt.date)
    state["parsed_tasks"] = parsed_tasks
    state["analysis_by_date"] = analysis_by_date

    # Get available sheet names for the dropdown
    all_sheets = []
    try:
        writer = ExcelWriter(config.excel.template_path)
        all_sheets = writer.get_sheet_names()
        writer.close()
    except Exception:
        logger.debug("Excel sheet discovery failed", exc_info=True)

    sheet_options = []
    for option in all_sheets:
        option_attr = escape(str(option), quote=True)
        option_text = escape(str(option))
        selected = "selected" if option == sheet_name else ""
        sheet_options.append(
            f'<option value="{option_attr}" {selected}>{option_text}</option>'
        )

    rows = ""
    for pt in parsed_tasks:
        items = "".join(f"<li>{escape(str(task))}</li>" for task in pt.tasks)
        target_sheet = escape(str(sheet_name or "未匹配"))
        rows += (
            f"<tr><td>{pt.date.isoformat()}</td>"
            f"<td><ul class='task-list'>{items}</ul></td><td>{target_sheet}</td></tr>"
        )

    html = (
        f"<div class='card'><h2>步骤 3：预览与导出</h2>"
        f"<p>群聊：{escape(group_name)}</p>"
        f"<p>时间范围：{escape(start_date)} ~ {escape(end_date)}</p>"
        f"<p>共匹配 {len(parsed_tasks)} 条任务消息</p>"
        f"<div class='form-group'>"
        f"<label>目标Sheet：</label>"
        f"<select id='selected-sheet' name='sheet_name' class='input-text'>"
        f"<option value=''>-- 请选择 --</option>"
        f"{''.join(sheet_options)}"
        f"</select>"
        f"</div>"
        f"<div class='form-group'>"
        f"<label>导出到文件：</label>"
        f"<input type='text' id='output-path' name='output_path' "
        f"  value='任务记录_{date.today().isoformat()}.xlsx' class='input-text' style='width:100%'>"  # noqa: DTZ011 - export names use the local date
        f"</div>"
        f"<table class='preview-table'><tr><th>日期</th><th>任务内容</th><th>目标Sheet</th></tr>{rows}</table>"
        f"<div class='btn-group'>"
        f"<a href='/step/2' class='btn btn-secondary'>← 返回修改</a>"
        f"<button class='btn btn-primary' "
        f"hx-post='/api/export' hx-include='#output-path,#selected-sheet' hx-target='#export-result' hx-swap='innerHTML'>"
        f"确认导出 →</button></div>"
        f"<div id='export-result'></div></div>"
    )
    return HTMLResponse(html)


async def _run_export_job(job: ExportJob) -> None:
    writer = None
    try:
        analysis_by_date = {
            date_key: list(messages) for date_key, messages in job.analysis_by_date
        }
        total = len(job.parsed_tasks)

        if config.voice.enabled and config.voice.api_key:
            await progress_hub.emit(
                job.job_id,
                ProgressEvent(stage="voice", message="正在转写语音消息...", progress=5),
            )
            try:
                transcriber = VoiceTranscriber(job.database, config.voice)
                start_ts = int(
                    datetime(  # noqa: DTZ001 - WeChat stores local timestamps
                        job.start_date.year,
                        job.start_date.month,
                        job.start_date.day,
                    ).timestamp()
                )
                end_ts = int(
                    datetime(  # noqa: DTZ001 - WeChat stores local timestamps
                        job.end_date.year,
                        job.end_date.month,
                        job.end_date.day,
                        23,
                        59,
                        59,
                    ).timestamp()
                )
                voice_texts = await transcriber.transcribe_all(
                    job.group, start_ts, end_ts
                )
                for date_key, texts in voice_texts.items():
                    analysis_by_date.setdefault(date_key, []).extend(texts)
            except Exception:
                logger.warning(
                    "Voice transcription failed for session %s",
                    job.session_id,
                    exc_info=True,
                )
                await progress_hub.emit(
                    job.job_id,
                    ProgressEvent(
                        stage="warning",
                        message="部分语音转写失败，继续导出文本任务",
                        progress=8,
                    ),
                )

        analyzer = None
        if config.ai.enabled and config.ai.api_key:
            analyzer = create_analyzer(config.ai)

        await progress_hub.emit(
            job.job_id,
            ProgressEvent(stage="write", message="正在写入任务数据...", progress=10),
        )
        writer = ExcelWriter(job.template_path)

        for index, task in enumerate(job.parsed_tasks):
            date_key = task.date.isoformat()
            context = analysis_by_date.get(date_key, [])
            if analyzer:
                progress = 10 + int(70 * (index + 1) / total)
                await progress_hub.emit(
                    job.job_id,
                    ProgressEvent(
                        stage="ai",
                        message=f"AI分析中 ({index + 1}/{total}): {date_key}",
                        progress=progress,
                    ),
                )
                analysis = await analyzer.analyze(task.tasks, context, date_key)
            else:
                analysis = "\n".join(context) if context else ""
            writer.add_task(job.sheet_name, task, analysis)

        await progress_hub.emit(
            job.job_id,
            ProgressEvent(stage="save", message="正在保存文件...", progress=85),
        )
        writer.save(job.output_path)
        await progress_hub.emit(
            job.job_id,
            ProgressEvent(
                stage="done",
                message=f"导出完成！文件保存在：{job.output_path}",
                progress=100,
                detail={"path": job.output_path},
            ),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - report job failures through its SSE channel
        await progress_hub.emit(
            job.job_id,
            ProgressEvent(stage="error", message=f"导出失败：{exc}", progress=0),
        )
    finally:
        if writer:
            writer.close()
        _release_export_job(job)



@app.post("/api/export")
async def export(request: Request, output_path: str = Form(""), sheet_name: str = Form("")):
    session_id, state = get_session(request)
    if _session_has_active_job(session_id):
        return _error_fragment("导出任务正在进行，请稍后重试", 409)

    parsed_tasks = state.get("parsed_tasks", [])
    # Use sheet_name from the form if provided; fall back to session state
    sheet_name = sheet_name or state.get("selected_sheet", "")

    if not parsed_tasks:
        return _validation_error("没有可导出的任务")
    if not sheet_name:
        return _validation_error("未指定目标 Sheet")

    try:
        start_value, end_value = parse_date_range(
            state.get("start_date"), state.get("end_date")
        )
    except ValidationError as exc:
        return _validation_error(str(exc))

    excel_path = config.excel.template_path
    if not Path(excel_path).exists():
        return _validation_error(f"Excel 模板不存在：{excel_path}")

    writer = None
    try:
        writer = ExcelWriter(excel_path)
        sheet_value = validate_sheet_name(sheet_name, writer.get_sheet_names())
    except ValidationError as exc:
        return _validation_error(str(exc))
    except Exception as exc:  # noqa: BLE001 - API boundary returns an explicit error fragment
        return _validation_error(f"无法读取 Excel 模板：{exc}")
    finally:
        if writer:
            writer.close()

    try:
        output_value = str(resolve_output_path(
            output_path,
            config.excel.output_dir,
            f"任务记录_{date.today().isoformat()}.xlsx",  # noqa: DTZ011 - export names use the local date
        ))
    except ValidationError as exc:
        return _validation_error(str(exc))

    group_value = state.get("selected_group")
    job_id = str(uuid.uuid4())
    database = state.get("ddb")
    if not database:
        return _error_fragment("请先完成鉴权", 401)
    if not group_value:
        return _validation_error("未指定群聊")

    job = ExportJob(
        job_id=job_id,
        session_id=session_id,
        database=database,
        group=group_value,
        sheet_name=sheet_value,
        template_path=excel_path,
        output_path=output_value,
        start_date=start_value,
        end_date=end_value,
        parsed_tasks=_snapshot_parsed_tasks(parsed_tasks),
        analysis_by_date=_snapshot_analysis(state.get("analysis_by_date", {})),
    )
    _start_export_job(job)

    return HTMLResponse(
        '<h3>导出进度</h3>'
        '<div id="progress-area" style="display:block;">'
        '<div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>'
        '<p id="progress-message">正在准备导出...</p></div>'
        '<div id="export-success"></div>'
        '<script>'
        f'var es = new EventSource("/api/progress/stream?job_id={job_id}");'
        'es.onmessage = function(e) {'
        '  var payload = JSON.parse(e.data);'
        '  var stage = payload.stage, msg = payload.message, pct = payload.progress;'
        '  document.getElementById("progress-fill").style.width = pct + "%";'
        '  document.getElementById("progress-message").textContent = msg;'
        '  if (stage === "done") {'
        '    es.close();'
        '    document.getElementById("progress-fill").style.background = "var(--success)";'
        '    var success = document.getElementById("export-success");'
        '    success.textContent = "";'
        '    var status = document.createElement("p");'
        '    status.className = "status-ok";'
        '    status.textContent = "✔ 导出成功！";'
        '    success.appendChild(status);'
        '    var path = document.createElement("p");'
        '    path.append("文件保存在：");'
        '    var code = document.createElement("code");'
        '    code.textContent = msg.replace("导出完成！文件保存在：", "");'
        '    path.appendChild(code);'
        '    success.appendChild(path);'
        '  } else if (stage === "error") {'
        '    es.close();'
        '    document.getElementById("progress-fill").style.background = "#e74c3c";'
        '  }'
        '};'
        'es.onerror = function() { es.close(); };'
        '</script>'
    )


@app.get("/api/progress/stream")
async def progress_stream(request: Request, job_id: str):
    session_id, _ = get_session(request)
    if job_owners.get(job_id) != session_id:
        return _error_fragment("导出任务不存在", 404)

    async def generate():
        try:
            async for event_data in progress_hub.event_stream(job_id):
                yield event_data
        finally:
            if job_owners.get(job_id) == session_id:
                job_owners.pop(job_id, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )

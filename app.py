# app.py
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from config import AppConfig
from core.dbutils import WeChatDB, DecryptedDB, MergedMsgDB
from core.scanner import WeChatScanner
from core.task_parser import TaskParser, ParsedTask
from core.matcher import SheetMatcher
from core.excel_writer import ExcelWriter
from core.progress import progress_hub, ProgressEvent

# --- App Setup ---
BASE_DIR = Path(__file__).parent
config = AppConfig.from_yaml()
app = FastAPI(title="微信聊天提取工具")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- In-memory state (per-session) ---
session_state: dict = {}


def get_session(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in session_state:
        session_id = str(uuid.uuid4())
    if session_id not in session_state:
        session_state[session_id] = {
            "wdb": None,
            "ddb": None,
            "selected_group": None,
            "selected_sheet": None,
            "start_date": None,
            "end_date": None,
            "parsed_tasks": [],
        }
    return session_id, session_state[session_id]


# --- Helper: query chatrooms ---
def _get_chatrooms(ddb: DecryptedDB) -> list:
    """从 MSG 表和 MicroMsg.db 获取群聊列表。返回 [(chatroom_id, display_name), ...]"""
    import os, tempfile, sqlite3
    from core.dbutils import decrypt_db_raw

    # Step 1: 从 MSG 表获取所有有消息记录的群聊 ID
    chatroom_ids = set()
    try:
        rows = ddb.execute(
            "SELECT DISTINCT StrTalker FROM MSG WHERE StrTalker LIKE '%@chatroom'"
        )
        chatroom_ids.update(r["StrTalker"] for r in rows if r["StrTalker"])
    except Exception:
        pass

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

            conn.close()
            break
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # Step 3: 返回带显示名称的列表
    return [(cid, name_map.get(cid, cid)) for cid in sorted(chatroom_ids)]


def _get_messages(ddb: DecryptedDB, chat_id: str, start_date: date, end_date: date, task_only: bool = False) -> list:
    """查询聊天记录，返回 dict 列表。"""
    start_ts = int(datetime(start_date.year, start_date.month, start_date.day).timestamp())
    end_ts = int(datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59).timestamp())

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
    session_id, state = get_session(request)
    response = templates.TemplateResponse(request, "step1_connect.html", {"step": 1})
    response.set_cookie(key="session_id", value=session_id)
    return response


@app.get("/step/2", response_class=HTMLResponse)
async def step2(request: Request):
    today = date.today()
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
        parts.append(f'<p class="status-ok">✔ 微信版本：{info.version}</p>')
        parts.append(f'<p class="status-ok">✔ 安装路径：{info.install_path}</p>')
    else:
        parts.append('<p class="status-err">✘ 未检测到微信安装</p>')
    if info.pid:
        parts.append(f'<p class="status-ok">✔ 微信进程 PID：{info.pid}</p>')
    else:
        parts.append('<p class="status-err">✘ 微信未运行</p>')
    if info.data_dir:
        parts.append(f'<p class="status-ok">✔ 数据目录：{info.data_dir}</p>')
    else:
        parts.append('<p class="status-err">✘ 未找到数据目录</p>')
    for e in info.errors:
        parts.append(f'<p class="status-warn">⚠ {e}</p>')
    return HTMLResponse("".join(parts))


@app.post("/api/key/extract")
async def extract_key(request: Request):
    try:
        wdb = WeChatDB()
        success, msg = wdb.scan_and_extract()
        if not success:
            return HTMLResponse(f'<p class="status-err">✘ {msg}</p>')

        dbs = wdb.open_all_msg_dbs()
        if not dbs:
            # Try single db for backward compat
            ddb = wdb.open_msg_db()
            if not ddb:
                return HTMLResponse(
                    f'<p class="status-ok">✔ 密钥提取成功</p>'
                    f'<p class="status-err">✘ 但无法打开 MSG 数据库（密钥可能不兼容此版本）</p>'
                )
            dbs = [ddb]

        merged = MergedMsgDB(dbs)

        # Verify we can query
        try:
            tables = dbs[0].execute("SELECT count(*) as n FROM sqlite_master")
            count = tables[0]["n"]
            dbs[0].execute("SELECT count(*) as n FROM MSG LIMIT 1")
        except Exception as e:
            merged.close_all()
            return HTMLResponse(f'<p class="status-err">✘ 数据库验证失败：{e}</p>')

        session_id, state = get_session(request)
        state["wdb"] = wdb
        state["ddb"] = merged

        return HTMLResponse(
            f'<p class="status-ok">✔ 密钥提取并验证通过（{count} 张表，{len(dbs)} 个分片库）</p>'
            f'<div id="next-step-area" style="display:block;">'
            f'<a href="/step/2" class="btn btn-primary">下一步：选择群聊 →</a></div>'
        )
    except Exception as e:
        return HTMLResponse(f'<p class="status-err">✘ 提取失败：{e}</p>')


@app.get("/api/chatrooms/list")
async def list_chatrooms(request: Request):
    _, state = get_session(request)
    ddb = state.get("ddb")
    if not ddb:
        return HTMLResponse('<p class="status-err">请先完成鉴权步骤</p>')

    try:
        chatrooms = _get_chatrooms(ddb)
    except Exception as e:
        return HTMLResponse(f'<p class="status-err">查询群聊失败：{e}</p>')

    if not chatrooms:
        return HTMLResponse('<p class="status-warn">未找到群聊记录</p>')

    excel_path = config.excel.template_path
    sheet_names = []
    try:
        writer = ExcelWriter(excel_path)
        sheet_names = writer.get_sheet_names()
        writer.close()
    except Exception:
        pass

    matcher = SheetMatcher(sheet_names, manual_map=config.matching.group_sheet_map)
    display_names = [c[1] for c in chatrooms]
    matches = matcher.match(display_names)

    items = []
    for cid, display_name in chatrooms:
        matched = matches.get(display_name)
        cls = "matched" if matched else "unmatched"
        txt = matched or "未匹配"
        items.append(
            f'<div class="chatroom-item" onclick="select(this)"'
            f' data-group="{cid}" data-sheet="{matched or ""}">'
            f'<span class="chatroom-name">{display_name}</span>'
            f'<span class="chatroom-sheet {cls}">{txt}</span>'
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
        return HTMLResponse('<p class="status-err">请先完成鉴权</p>')

    chatrooms = _get_chatrooms(ddb)
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
        pass

    matcher = SheetMatcher(sheet_names, manual_map=config.matching.group_sheet_map)
    display_names = [c[1] for c in matched]
    mm = matcher.match(display_names)

    items = []
    for cid, display_name in matched:
        s = mm.get(display_name)
        items.append(
            f'<div class="chatroom-item" onclick="select(this)"'
            f' data-group="{cid}" data-sheet="{s or ""}">'
            f'<span class="chatroom-name">{display_name}</span>'
            f'<span class="chatroom-sheet {"matched" if s else "unmatched"}">'
            f'{("→ " + s) if s else "未匹配"}</span></div>'
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
    state["selected_group"] = group_name
    state["selected_sheet"] = sheet_name
    state["start_date"] = start_date
    state["end_date"] = end_date

    ddb = state.get("ddb")
    if not ddb:
        return HTMLResponse('<p class="status-err">请先完成鉴权</p>')

    s_date = date.fromisoformat(start_date)
    e_date = date.fromisoformat(end_date)

    from datetime import datetime
    messages = _get_messages(ddb, group_name, s_date, e_date, task_only=False)

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
                msg_date = datetime.fromtimestamp(msg["timestamp"]).date()
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
        pass

    sheet_options = "".join(
        f'<option value="{s}" {"selected" if s == sheet_name else ""}>{s}</option>'
        for s in all_sheets
    )

    rows = ""
    for pt in parsed_tasks:
        li = "".join(f"<li>{t}</li>" for t in pt.tasks)
        rows += f"<tr><td>{pt.date.isoformat()}</td><td><ul class='task-list'>{li}</ul></td><td>{sheet_name or '未匹配'}</td></tr>"

    html = (
        f"<div class='card'><h2>步骤 3：预览与导出</h2>"
        f"<p>群聊：{group_name}</p>"
        f"<p>时间范围：{start_date} ~ {end_date}</p>"
        f"<p>共匹配 {len(parsed_tasks)} 条任务消息</p>"
        f"<div class='form-group'>"
        f"<label>目标Sheet：</label>"
        f"<select id='selected-sheet' name='sheet_name' class='input-text'>"
        f"<option value=''>-- 请选择 --</option>"
        f"{sheet_options}"
        f"</select>"
        f"</div>"
        f"<div class='form-group'>"
        f"<label>导出到文件：</label>"
        f"<input type='text' id='output-path' name='output_path' "
        f"  value='D:/assistants/assignment-analysis.xlsx' class='input-text' style='width:100%'>"
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



@app.post("/api/export")
async def export(request: Request, output_path: str = Form(""), sheet_name: str = Form("")):
    session_id, state = get_session(request)
    parsed_tasks = state.get("parsed_tasks", [])
    # Use sheet_name from the form if provided; fall back to session state
    sheet_name = sheet_name or state.get("selected_sheet", "")

    if not parsed_tasks:
        return HTMLResponse('<p class="status-err">没有可导出的任务</p>')
    if not sheet_name:
        return HTMLResponse('<p class="status-err">未指定目标 Sheet</p>')

    excel_path = config.excel.template_path
    if not Path(excel_path).exists():
        return HTMLResponse(f'<p class="status-err">Excel 模板不存在：{excel_path}</p>')

    # 若未指定输出路径，用默认
    if not output_path:
        output_path = str(Path(config.excel.output_dir) / f"任务记录_{date.today().isoformat()}.xlsx")

    async def export_with_progress():
        try:
            await progress_hub.emit(session_id, ProgressEvent(stage="start", message="开始写入 Excel...", progress=0))
            writer = ExcelWriter(excel_path)
            await progress_hub.emit(session_id, ProgressEvent(stage="write", message="正在写入任务数据...", progress=30))
            analysis_by_date = state.get("analysis_by_date", {})
            for pt in parsed_tasks:
                analysis = "\n".join(analysis_by_date.get(pt.date.isoformat(), []))
                writer.add_task(sheet_name, pt, analysis)
            await progress_hub.emit(session_id, ProgressEvent(stage="save", message="正在保存文件...", progress=80))
            writer.save(output_path)
            writer.close()
            await progress_hub.emit(session_id, ProgressEvent(stage="done", message=f"导出完成！文件保存在：{output_path}", progress=100))
        except Exception as e:
            await progress_hub.emit(session_id, ProgressEvent(stage="error", message=f"导出失败：{e}", progress=0))

    import asyncio
    # 预注册 SSE listener，防止时序问题
    progress_hub.register(session_id)
    asyncio.create_task(export_with_progress())

    return HTMLResponse(
        '<h3>导出进度</h3>'
        '<div id="progress-area" style="display:block;">'
        '<div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>'
        '<p id="progress-message">正在准备导出...</p></div>'
        '<div id="export-success"></div>'
        '<script>'
        'var es = new EventSource("/api/progress/stream");'
        'es.onmessage = function(e) {'
        '  var parts = e.data.split("|");'
        '  var stage = parts[0], msg = parts[1], pct = parts[2];'
        '  document.getElementById("progress-fill").style.width = pct + "%";'
        '  document.getElementById("progress-message").textContent = msg;'
        '  if (stage === "done") {'
        '    es.close();'
        '    document.getElementById("progress-fill").style.background = "var(--success)";'
        '    document.getElementById("export-success").innerHTML = '
        '      "<p class=\\"status-ok\\">✔ 导出成功！</p>"'
        '      + "<p>文件保存在：<code>" + msg.replace("导出完成！文件保存在：", "") + "</code></p>";'
        '  } else if (stage === "error") {'
        '    es.close();'
        '    document.getElementById("progress-fill").style.background = "#e74c3c";'
        '  }'
        '};'
        'es.onerror = function() { es.close(); };'
        '</script>'
    )


@app.get("/api/progress/stream")
async def progress_stream(request: Request):
    session_id, _ = get_session(request)
    async def generate():
        async for event_data in progress_hub.event_stream(session_id):
            yield event_data
    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )

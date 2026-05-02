# app.py
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from config import AppConfig
from core.scanner import WeChatScanner
from core.key_extractor import KeyExtractor
from core.db_decryptor import DBDecryptor
from core.message_fetcher import MessageFetcher, Message
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
            "decryptor": None,
            "fetcher": None,
            "selected_group": None,
            "selected_sheet": None,
            "start_date": None,
            "end_date": None,
            "parsed_tasks": [],
        }
    return session_id, session_state[session_id]


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


# --- API Endpoints ---
@app.get("/api/wechat/status")
async def wechat_status():
    scanner = WeChatScanner()
    info = scanner.scan()
    status_parts = []
    if info.version:
        status_parts.append(
            f'<p class="status-ok">✔ 微信版本：{info.version}</p>'
            f'<p class="status-ok">✔ 安装路径：{info.install_path}</p>'
        )
    else:
        status_parts.append('<p class="status-err">✘ 未检测到微信安装</p>')
    if info.pid:
        status_parts.append(
            f'<p class="status-ok">✔ 微信进程 PID：{info.pid}</p>'
        )
    else:
        status_parts.append('<p class="status-err">✘ 微信未运行</p>')
    if info.data_dir:
        status_parts.append(
            f'<p class="status-ok">✔ 数据目录：{info.data_dir}</p>'
            f'<p>数据库文件：{", ".join(info.db_files)}</p>'
        )
    else:
        status_parts.append('<p class="status-err">✘ 未找到数据目录</p>')
    for err in info.errors:
        status_parts.append(f'<p class="status-warn">⚠ {err}</p>')
    return HTMLResponse("".join(status_parts))


@app.post("/api/key/extract")
async def extract_key(request: Request):
    scanner = WeChatScanner()
    info = scanner.scan()
    if not info.pid:
        return HTMLResponse(
            '<p class="status-err">请先启动微信</p>'
        )
    extractor = KeyExtractor(info.pid)
    key = extractor.extract()
    if not key:
        return HTMLResponse(
            '<p class="status-err">自动提取失败，请尝试手动输入密钥</p>'
        )

    # Test decryption
    db_path = next(
        (str(Path(info.data_dir) / f) for f in info.db_files if f.startswith("MSG")),
        None,
    )
    if not db_path or not Path(db_path).exists():
        return HTMLResponse(
            f'<p class="status-ok">✔ 密钥提取成功：{key[:8]}...{key[-8:]}</p>'
            '<p class="status-err">但未找到 MSG.db 文件</p>'
        )

    try:
        decryptor = DBDecryptor(db_path, key)
        decryptor.open()
        session_id, state = get_session(request)
        state["decryptor"] = decryptor
        state["fetcher"] = MessageFetcher(decryptor)
        return HTMLResponse(
            '<p class="status-ok">✔ 密钥提取成功并验证通过</p>'
            '<div id="next-step-area" style="display:block;">'
            '<a href="/step/2" class="btn btn-primary">下一步：选择群聊 →</a></div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<p class="status-err">✘ 数据库解密失败：{e}</p>'
        )


@app.get("/api/chatrooms/list")
async def list_chatrooms(request: Request):
    _, state = get_session(request)
    fetcher = state.get("fetcher")
    if not fetcher:
        return HTMLResponse('<p class="status-err">请先完成鉴权步骤</p>')

    chatrooms = fetcher.get_chatrooms()
    if not chatrooms:
        return HTMLResponse('<p class="status-warn">未找到群聊记录，请确认微信数据库中有群聊消息</p>')

    excel_path = config.excel.template_path
    sheet_names = []
    try:
        writer = ExcelWriter(excel_path)
        sheet_names = writer.get_sheet_names()
        writer.close()
    except Exception:
        pass

    matcher = SheetMatcher(sheet_names, manual_map=config.matching.group_sheet_map)
    group_names = [c[0] for c in chatrooms]
    matches = matcher.match(group_names)

    html_parts = []
    for group_name in group_names[:50]:  # Limit to 50 chatrooms
        matched = matches.get(group_name)
        sheet_class = "matched" if matched else "unmatched"
        sheet_text = matched or "未匹配"
        html_parts.append(
            f'<div class="chatroom-item" onclick="select(this)"'
            f' data-group="{group_name}" data-sheet="{matched or ""}">'
            f'<span class="chatroom-name">{group_name}</span>'
            f'<span class="chatroom-sheet {sheet_class}">{sheet_text}</span>'
            f'</div>'
        )

    html = "".join(html_parts)
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
    fetcher = state.get("fetcher")
    if not fetcher:
        return HTMLResponse('<p class="status-err">请先完成鉴权</p>')

    chatrooms = fetcher.get_chatrooms()
    if not query:
        return await list_chatrooms(request)

    matched = [c for c in chatrooms if query.lower() in c[0].lower()]

    excel_path = config.excel.template_path
    sheet_names = []
    try:
        writer = ExcelWriter(excel_path)
        sheet_names = writer.get_sheet_names()
        writer.close()
    except Exception:
        pass

    matcher = SheetMatcher(sheet_names, manual_map=config.matching.group_sheet_map)
    group_names = [c[0] for c in matched]
    matches = matcher.match(group_names)

    html_parts = []
    for group_name in group_names:
        matched_sheet = matches.get(group_name)
        display = f"→ {matched_sheet}" if matched_sheet else "未匹配"
        html_parts.append(
            f'<div class="chatroom-item" onclick="select(this)"'
            f' data-group="{group_name}" data-sheet="{matched_sheet or ""}">'
            f'<span class="chatroom-name">{group_name}</span>'
            f'<span class="chatroom-sheet {"matched" if matched_sheet else "unmatched"}">{display}</span>'
            f'</div>'
        )
    return HTMLResponse("".join(html_parts) or '<p>无匹配群聊</p>')


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

    fetcher = state.get("fetcher")
    if not fetcher:
        return HTMLResponse('<p class="status-err">请先完成鉴权</p>')

    s_date = date.fromisoformat(start_date)
    e_date = date.fromisoformat(end_date)

    messages = fetcher.fetch_messages(
        chat_id=group_name,
        start_date=s_date,
        end_date=e_date,
        task_only=True,
    )

    parser = TaskParser()
    parsed_tasks = []
    for msg in messages:
        result = parser.parse(msg.content, msg.msg_id)
        if result:
            parsed_tasks.append(result)

    state["parsed_tasks"] = parsed_tasks

    rows_html = ""
    for pt in parsed_tasks:
        task_items = "".join(
            f'<li>{t}</li>' for t in pt.tasks
        )
        rows_html += (
            f'<tr>'
            f'<td>{pt.date.isoformat()}</td>'
            f'<td><ul class="task-list">{task_items}</ul></td>'
            f'<td>{sheet_name or "未匹配"}</td>'
            f'</tr>'
        )

    html = (
        f'<div class="card">'
        f'<h2>步骤 3：预览与导出</h2>'
        f'<p>群聊：{group_name} → Sheet「{sheet_name or "未选择"}」</p>'
        f'<p>时间范围：{start_date} ~ {end_date}</p>'
        f'<p>共匹配 {len(parsed_tasks)} 条任务消息</p>'
        f'<table class="preview-table">'
        f'<tr><th>日期</th><th>任务内容</th><th>目标Sheet</th></tr>'
        f'{rows_html}'
        f'</table>'
        f'<div class="btn-group">'
        f'<a href="/step/2" class="btn btn-secondary">← 返回修改</a>'
        f'<button class="btn btn-primary" '
        f'hx-post="/api/export" hx-target="#export-result" hx-swap="innerHTML">'
        f'确认导出 →</button>'
        f'</div>'
        f'<div id="export-result"></div>'
        f'</div>'
    )
    return HTMLResponse(html)


@app.post("/api/export")
async def export(request: Request):
    session_id, state = get_session(request)
    parsed_tasks = state.get("parsed_tasks", [])
    sheet_name = state.get("selected_sheet", "")

    if not parsed_tasks:
        return HTMLResponse('<p class="status-err">没有可导出的任务</p>')

    if not sheet_name:
        return HTMLResponse('<p class="status-err">未指定目标 Sheet</p>')

    excel_path = config.excel.template_path
    if not Path(excel_path).exists():
        return HTMLResponse(f'<p class="status-err">Excel 模板不存在：{excel_path}</p>')

    async def export_with_progress():
        try:
            await progress_hub.emit(session_id, ProgressEvent(
                stage="start", message="开始写入 Excel...", progress=0
            ))

            writer = ExcelWriter(excel_path)
            await progress_hub.emit(session_id, ProgressEvent(
                stage="write", message="正在写入任务数据...", progress=30
            ))

            for pt in parsed_tasks:
                writer.add_task(sheet_name, pt)

            await progress_hub.emit(session_id, ProgressEvent(
                stage="save", message="正在保存文件...", progress=80
            ))

            output_path = str(
                Path(config.excel.output_dir) /
                f"任务记录_{date.today().isoformat()}.xlsx"
            )
            writer.save(output_path)
            writer.close()

            await progress_hub.emit(session_id, ProgressEvent(
                stage="done",
                message=f"导出完成！文件保存在：{output_path}",
                progress=100,
            ))
        except Exception as e:
            await progress_hub.emit(session_id, ProgressEvent(
                stage="error", message=f"导出失败：{e}", progress=0
            ))

    import asyncio
    asyncio.create_task(export_with_progress())

    return HTMLResponse(
        '<h3>导出进度</h3>'
        '<div id="progress-area" style="display:block;">'
        '<div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>'
        '<p id="progress-message">正在准备导出...</p>'
        '</div>'
        '<div hx-ext="sse" sse-connect="/api/progress/stream" sse-swap="message">'
        '</div>'
    )


@app.get("/api/progress/stream")
async def progress_stream(request: Request):
    session_id, _ = get_session(request)
    async def generate():
        async for event_data in progress_hub.event_stream(session_id):
            yield event_data
    return StreamingResponse(generate(), media_type="text/event-stream")


# --- Startup ---
if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )

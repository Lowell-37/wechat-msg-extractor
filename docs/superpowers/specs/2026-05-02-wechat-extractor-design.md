# 微信指定群聊聊天记录提取工具 — 设计规格书

**日期**：2026-05-02
**版本**：1.0
**状态**：待实现

---

## 1. 项目概述

一款本地化 Web 工具，从 Windows 微信 PC 版本地数据库中提取指定群聊的聊天记录，解析任务格式的消息，填充到 Excel 模板的对应单元格中。

### 1.1 核心场景

- 微信群聊中每日发送任务消息（格式：`🚩5.2 任务\n1⃣ 任务A\n2⃣ 任务B`）
- Excel 模板按学生分 Sheet，表头为「安排时间 | 任务安排 | 情况分析」
- 工具自动解析任务消息，按日期匹配 Excel 行，填入排时间列（A列）和任务安排列（B列）
- 情况分析列（C列）保留手动填写

### 1.2 提取内容类型

- **文本**：解析任务内容填充到 Excel 单元格
- **语音/图片/视频**：保存到本地目录备查，Excel 中不嵌入

---

## 2. 技术架构

### 2.1 整体架构：Python 单体 Web 应用

```
浏览器 ←→ FastAPI (路由+API+SSE) ←→ 微信解密模块 ←→ MSG.db
```

### 2.2 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI + uvicorn | HTTP 服务 + SSE 进度推送 |
| 模板渲染 | Jinja2 | 服务端页面渲染 |
| 前端交互 | htmx (CDN) + 原生 CSS | 无刷新局部更新 |
| 进程内存读取 | pymem | 从 WeChat.exe 内存提取数据库密钥 |
| 数据库解密 | sqlcipher3 | 解密并查询加密的 SQLite 数据库 |
| Excel 读写 | openpyxl | 读取模板、写入数据、处理合并单元格 |
| 微信环境检测 | psutil + 注册表 | 扫描微信安装路径和数据目录 |

### 2.3 项目结构

```
wechat-extractor/
├── app.py                  # FastAPI 入口，路由注册
├── config.yaml             # 配置文件（群名→Sheet名映射、匹配规则等）
├── requirements.txt        # Python 依赖
├── static/
│   └── style.css           # 全局样式
├── templates/
│   ├── base.html           # 基础布局
│   ├── step1_connect.html  # 步骤1：连接与鉴权
│   ├── step2_select.html   # 步骤2：选择群聊与过滤
│   └── step3_preview.html  # 步骤3：预览与导出
├── core/
│   ├── __init__.py
│   ├── scanner.py          # WeChatScanner：扫描微信路径/版本/进程
│   ├── key_extractor.py    # KeyExtractor：pymem 提取数据库密钥
│   ├── db_decryptor.py     # DBDecryptor：sqlcipher3 解密连接
│   ├── message_fetcher.py  # MessageFetcher：SQL 查询消息记录
│   ├── task_parser.py      # TaskParser：正则解析任务消息
│   ├── matcher.py          # SheetMatcher：群名→Sheet名匹配
│   ├── excel_writer.py     # ExcelWriter：openpyxl 写入操作
│   └── progress.py         # ProgressHub：SSE 进度推送
├── export/                 # 导出的媒体文件目录
│   ├── images/
│   ├── voice/
│   └── video/
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-05-02-wechat-extractor-design.md
```

---

## 3. 核心模块设计

### 3.1 WeChatScanner（微信环境扫描）

**职责**：自动检测微信安装版本、数据目录、进程信息

**实现要点**：
- 扫描 `C:\Program Files\Tencent\WeChat\[版本号]\` 检测版本
- 读取注册表 `HKCU\Software\Tencent\WeChat` 获取安装路径
- 数据目录默认 `%AppData%\Tencent\WeChat\`，遍历子目录找 `MSG*.db`
- 通过 `psutil` 查找 `WeChat.exe` 进程，获取 PID 和进程路径

**输出**：
```python
{
    "version": "3.9.12.17",
    "install_path": "C:\\Program Files\\Tencent\\WeChat\\[3.9.12.17]",
    "data_dir": "C:\\Users\\xxx\\AppData\\Roaming\\Tencent\\WeChat\\wxid_xxx\\",
    "pid": 12345,
    "db_files": ["MSG0.db", "MSG1.db", "MicroMsg.db"]
}
```

### 3.2 KeyExtractor（密钥提取）

**职责**：通过 pymem 附加微信进程，从内存中读取数据库加密密钥

**实现要点**：
- 使用 `pymem.Pymem("WeChat.exe")` 附加进程
- 在内存中搜索 SQLite 密钥特征（64 字节 hex 字符串）
- 验证密钥：尝试用密钥连接 `MicroMsg.db`，查询 `SELECT count(*) FROM sqlite_master` 成功则密钥正确
- 支持 32 位和 64 位微信版本

**输出**：`str` — 64 字符 hex 密钥

### 3.3 DBDecryptor（数据库解密）

**职责**：使用 sqlcipher3 连接加密的 MSG.db，提供查询接口

**实现要点**：
- 复制 `MSG.db` 到临时目录（避免锁冲突）
- 使用 `sqlcipher3.connect(db_path)` + `PRAGMA key="x'<hex_key>'"` 解密
- 提供 `get_connection()` 方法返回可查询的连接

### 3.4 MessageFetcher（消息查询）

**职责**：执行 SQL 查询，根据群名+时间范围+内容类型过滤消息

**关键表结构（微信 MSG.db）**：
| 表 | 字段 | 说明 |
|----|------|------|
| MSG | local_id, TalkerId, Type, SubType, IsSender, CreateTime, StrContent, StrTalker, BytesExtra | 消息主表 |
| Name2ID | usrName, nickName | 用户名→昵称映射 |
| ChatRoom | chatRoomName, UserNameList, DisplayNameList | 群聊信息 |

**查询逻辑**：
```sql
SELECT m.CreateTime, m.Type, m.SubType, m.IsSender,
       m.StrContent, m.StrTalker
FROM MSG m
WHERE m.StrTalker = '<群聊ID>'
  AND m.CreateTime BETWEEN <start_ts> AND <end_ts>
  AND m.Type = 1  -- 文本消息
ORDER BY m.CreateTime ASC
```

**输出**：消息列表，每条包含时间戳、类型、发送者、内容

### 3.5 TaskParser（任务消息解析）

**职责**：从文本消息中识别任务格式，解析出日期和任务列表

**正则规则**：
```python
# 识别是否为任务消息
TASK_MSG_PATTERN = re.compile(r'🚩\s*\d+\.\d+\s*任务')

# 提取日期：🚩5.2 → (5, 2)，结合当前年份 → 2026-05-02
DATE_PATTERN = re.compile(r'🚩\s*(\d+)\.(\d+)')

# 提取任务项：1⃣ xxx 2⃣ xxx
TASK_ITEM_PATTERN = re.compile(r'\d+⃣\s*(.+?)(?=\d+⃣|$)', re.DOTALL)
```

**解析结果**：
```json
{
  "msg_id": 12345,
  "date": "2026-05-02",
  "date_excel_serial": 46145,
  "raw_text": "🚩5.2 任务\n1⃣ 订正作文\n2⃣ 完形填空...",
  "tasks": ["订正作文", "完形填空，首字母填空，语法填空各一篇，需要填写前面两栏"]
}
```

### 3.6 SheetMatcher（群名→Sheet名匹配）

**职责**：将群聊名称匹配到 Excel Sheet 名称

**匹配策略**：
1. **自动匹配**：遍历 Excel 所有 Sheet 名，检查是否完全包含于群名中
   - 例：群名 `0601初2027广州张三`，Sheet 名 `张三` → 匹配成功
   - 例：群名 `0501初2027广州李四`，Sheet 名 `李四` → 匹配成功
2. **配置映射**：`config.yaml` 中的 `group_sheet_map` 优先于自动匹配
3. **手动修正**：未匹配或匹配错误的可在 Web 界面手动选择 Sheet

**输出**：
```json
[
  {"group_name": "0601初2027广州张三", "matched_sheet": "张三", "method": "auto"},
  {"group_name": "0501初2027广州李四", "matched_sheet": "李四", "method": "auto"},
  {"group_name": "班群通知", "matched_sheet": null, "method": "unmatched"}
]
```

### 3.7 ExcelWriter（Excel 写入）

**职责**：将解析后的任务数据写入 Excel 模板

**实现要点**：
- 使用 `openpyxl.load_workbook(template_path)` 加载模板
- A 列日期序列号计算：`(date - datetime(1899, 12, 30)).days`
- 在目标 Sheet 的 A 列查找匹配的日期序列号
- 找到 → 写入 B 列（多条任务用 `\n` 连接）
- 未找到 → 在 A 列最后一行追加新行，日期 + 任务
- **不修改 C 列（情况分析）**
- **不破坏已有合并单元格布局**
- 输出另存为新文件：`任务记录_<日期>.xlsx`

### 3.8 ProgressHub（进度推送）

**职责**：通过 SSE 向前端实时推送提取进度

**推送事件**：
```python
# 阶段变更
{"stage": "extract_key", "message": "正在从微信进程提取密钥...", "progress": 10}
{"stage": "decrypt_db", "message": "正在解密数据库...", "progress": 20}
{"stage": "fetch_messages", "message": "正在读取聊天记录 (156/1230)...", "progress": 50}
{"stage": "parse_tasks", "message": "正在解析任务消息...", "progress": 80}
{"stage": "write_excel", "message": "正在写入 Excel...", "progress": 95}
{"stage": "done", "message": "导出完成", "progress": 100}
```

---

## 4. Web 界面流程

### 步骤 1：连接与鉴权

- 自动扫描：显示微信版本、数据目录、进程状态
- 一键提取密钥按钮（调用 KeyExtractor）
- 手动输入密钥备选
- 验证通过后自动跳转步骤 2

### 步骤 2：选择群聊与过滤

- 展示群聊列表（从 MSG.db ChatRoom 表读取）
- 搜索框：按群名过滤
- 每行显示：群名 → 匹配到的 Sheet（未匹配的标红）
- 时间范围选择器（开始/结束日期）
- 手动修正 Sheet 匹配下拉框

### 步骤 3：预览与导出

- 预览解析出的任务消息（日期 | 任务内容 | 匹配Sheet）
- 支持勾选/取消某条
- 确认后执行写入
- SSE 实时显示进度
- 完成后提供导出文件下载链接

---

## 5. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 微信未运行 | 提示用户先启动微信 |
| 密钥提取失败 | 显示详细错误，提供手动输入入口 |
| 数据库解密失败 | 检查微信版本兼容性，提示可能版本不支持 |
| 群名无匹配 Sheet | 标记警告，用户需手动选择或跳过 |
| Excel 模板被占用 | 提示关闭 Excel 后重试 |
| 日期在模板中无对应行 | 追加新行到最后 |

---

## 6. 配置文件 (config.yaml)

```yaml
wechat:
  auto_detect: true
  # 手动指定路径时使用（auto_detect: false）
  data_dir: null
  version_dir: null

excel:
  template_path: "D:/assistants/任务安排与情况分析.xlsx"
  output_dir: "./export/excel"

matching:
  # 手动映射优先于自动匹配
  group_sheet_map:
    "0601初2027广州张三": "张三"
    # "群名称": "Sheet名称"

task_parsing:
  # 默认使用内置正则，可自定义
  task_msg_pattern: null   # null = 使用默认
  date_pattern: null
  task_item_pattern: null

media:
  save_images: true
  save_voice: true
  save_video: true
  export_dir: "./export"

server:
  host: "127.0.0.1"
  port: 8888
```

---

## 7. 关键风险与依赖

| 风险 | 缓解措施 |
|------|----------|
| 微信版本升级导致密钥位置变化 | 参考 WeChatMsg 开源项目维护的偏移表 |
| sqlcipher3 编译安装复杂 | 提供预编译的 DLL / 使用系统 Python 3.10-3.12 |
| 杀软拦截 pymem 内存读取 | 提示用户添加信任区 |
| MSG.db 文件被微信进程锁定 | 复制到临时目录后操作 |

---

## 8. 非需求（明确排除）

- 不支持实时监听新消息（只做批量提取）
- 不支持多群同时批量导出（单群模式）
- 不嵌入图片/视频到 Excel 单元格
- 不读取/修改 C 列（情况分析）
- 不支持 macOS（仅 Windows）
- 不处理红包、转账、小程序等非内容类消息

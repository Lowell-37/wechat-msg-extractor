# WeChat Message Extractor

从微信本地数据库中提取群聊任务记录（🚩 格式），并写入 Excel 模板的 Web 工具。

## 功能

- 自动检测旧版与新版微信进程、安装路径和数据目录
- 支持旧版微信 MSG 分片库的密钥提取、解密与查询
- 可识别新版微信 `message_*.db`，但当前版本暂不支持其密钥提取和解密导出
- 解析 🚩M.D 任务格式的群聊消息
- 将任务和情况分析写入 Excel 模板对应 Sheet
- 支持群聊→Sheet 自动匹配和手动映射
- Web 界面（FastAPI + htmx），使用单页三步向导
- 返回、刷新和失败重试会保留有效的连接、选择与预览状态

## 使用流程

1. **Step 1 连接微信**：检查客户端、进程和数据目录；旧版微信可自动提取密钥并验证数据库
2. **Step 2 选择数据**：搜索群聊，选择目标 Sheet、日期范围和需要导出的任务
3. **Step 3 预览导出**：确认任务、输出路径和隐私选项，查看实时进度并导出 Excel

每一步都可以通过“上一步/下一步”返回或继续；修改群聊、日期或 Sheet 后，旧预览会自动失效并要求重新预览。

## 快速开始

```bash
# Create and activate a virtual environment (Windows PowerShell)
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install the application and development tools
py -m pip install -e ".[dev]"

# Run the quality gate
py -m pytest -q
py -m ruff check .

# Start the local server
py app.py
```

Supported Python versions are `>=3.11,<3.14` (CI uses Python 3.13; the workflow
uses `python`, not the Windows `py` launcher, to select that interpreter). The
default server address is local-only: `http://127.0.0.1:8888`; it does not
expose the application to the network.

### 新版微信兼容性

微信 4.x 通常运行 `WeChatAppEx.exe`/`Weixin.exe`，并将消息保存为
`message_*.db`。本项目会识别这些进程和文件并显示兼容性提示，但当前的
`pywxdump` 解密链路只支持旧版 `WeChat.exe` 与 `MSG*.db`。请勿覆盖微信原始
目录；如需读取新版历史消息，应先使用支持微信 4.x 的专用导出工具生成副本，
再导入本项目处理。

启用 AI 分析或语音转写时，相关消息内容会发送到你配置的外部服务；仅使用
本地解析和 Excel 导出时，数据默认留在本机。

## 配置

编辑 `config.yaml`：

```yaml
excel:
  template_path: "D:/assistants/assignment-analysis.xlsx"  # Excel 模板路径
  output_dir: "./export/excel"                              # 默认导出目录

matching:
  group_sheet_map: {}  # 手动群聊→Sheet 映射（群名包含 Sheet 名时可自动匹配）

server:
  host: "127.0.0.1"
  port: 8888
```

## 任务消息格式

支持如下格式的 🚩 任务消息：

```
🚩 5.2 任务
1⃣ 滚雪球：完形填空，阅读理解练习
2⃣ 高频结论一到七
```

- 日期从 `🚩 M.D` 解析
- 任务项从序号 `1⃣` `2⃣` `3⃣` 解析
- 非任务消息自动归入"情况分析"列

## Excel 输出格式

| A列（安排时间） | B列（任务安排） | C列（情况分析） |
|---|---|---|
| 2026/5/2 | 1、滚雪球：完形填空，阅读理解练习<br>2、高频结论一到七 | 寒假计划：... |

## 技术栈

- **后端**：Python 3.13 + FastAPI + uvicorn
- **数据库**：SQLite（WeChat MSG.db 解密后查询）
- **Excel**：openpyxl
- **前端**：htmx + 自定义 CSS
- **解密**：AES-CBC + PBKDF2-HMAC-SHA1

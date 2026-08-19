# WeChat Message Extractor

从微信本地数据库中提取群聊任务记录（🚩 格式），并写入 Excel 模板的 Web 工具。

## 功能

- 自动检测旧版与新版微信进程、安装路径和数据目录
- 支持旧版微信 MSG 分片库的密钥提取、解密与查询
- 通过 WechatExplorer 的本机 API 读取新版微信 `message_*.db`（旧版链路保留）
- 解析 🚩M.D 任务格式的群聊消息
- 将任务和情况分析写入 Excel 模板对应 Sheet
- 支持群聊→Sheet 自动匹配和手动映射
- 可在连接页手动选择 `.xlsx` 模板，并保存为本机默认模板
- 支持通用 OpenAI Chat Completions 兼容模型，并在页面中安全配置和测试
- Web 界面（FastAPI + htmx），使用单页三步向导
- 返回、刷新和失败重试会保留有效的连接、选择与预览状态

## 使用流程

1. **Step 1 连接微信**：检查客户端、进程和数据目录；旧版微信可自动提取密钥，新版微信通过 WechatExplorer 本机 API 连接
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
expose the application to the network. 为保护本机 DPAPI 凭据，`server.host`
只接受 `localhost`、IPv4 `127.0.0.0/8` 或 IPv6 `::1` 回环地址；应用会拒绝
`0.0.0.0` 或局域网地址。

### 新版微信兼容性

微信 4.x 通常运行 `WeChatAppEx.exe`/`Weixin.exe`，并将消息保存为
`message_*.db`。旧版 `WeChat.exe` 与 `MSG*.db` 继续使用内置 `pywxdump`
链路；新版通过 [WechatExplorer](https://github.com/Wxw-Gu/WechatExplorer)
的本机 HTTP API 读取。请在 WechatExplorer 的 API Center 开启 API，并生成一个
新 Token。推荐通过无回显的本机 PowerShell 提示输入，并执行安全凭据命令；命令
会将 Token 作为 Windows 机器级 DPAPI 密文保存到 Git 忽略的
`local/secrets/` 目录，随后用本机 API 验证。不要把 Token 写入
`config.yaml`、截图或提交。

```powershell
.\scripts\save_wechat_explorer_token.ps1
```

需要临时覆盖已保存的 Token 时，可仅在启动本项目的终端设置环境变量：

```powershell
$env:WECHATEXPLORER_API_TOKEN = "在 WechatExplorer API Center 生成的令牌"
py app.py
```

默认地址为 `http://127.0.0.1:6131/api/v1`，且程序只接受 `localhost`、
`127.0.0.1` 或 `::1` 等回环地址。若你的本机 API 使用其他端口，可在
`config.yaml` 的 `wechat.explorer_base_url` 修改。持久化凭据使用 Windows
`CRYPTPROTECT_LOCAL_MACHINE`，因此同一台机器上能读取密文文件的本地用户也可
使用它；环境变量的优先级高于持久化凭据。请勿覆盖微信原始目录。

启用 AI 分析或语音转写时，相关消息内容会发送到你配置的外部服务；仅使用
本地解析和 Excel 导出时，数据默认留在本机。

### AI 模型设置

点击页面顶部的“模型设置”，按以下流程启用 AI 分析：

1. 填写提供商名称、API Base URL、模型名称和 API Key。
2. 点击“保存并测试”，等待页面显示“连接测试通过”。
3. 返回预览页，在“外部处理选项”中启用 AI 分析并确认隐私提示。

模型服务需要兼容 OpenAI 的 `/v1/chat/completions` 接口。远程地址必须使用
HTTPS；Ollama、LM Studio 等本机服务可使用 `localhost`、`127.0.0.1` 或
`::1` 的 HTTP 地址。API Key 不会回显到浏览器或写入 YAML，而是作为 Windows
机器级 DPAPI 密文保存在 Git 忽略的 `local/secrets/` 中。填写新 Key 并通过
连接测试后才会替换旧 Key；留空则继续使用已保存的 Key。

只有已启用且通过连接测试的模型才能在预览页勾选。导出过程中若某日 AI 请求
失败，工作簿会写入 `[AI 分析失败]` 说明并继续导出，避免将失败误认为成功。

## 配置

编辑 `config.yaml`：

```yaml
excel:
  template_path: ""                                        # 留空后在连接页选择
  output_dir: "./export/excel"                              # 默认导出目录

matching:
  group_sheet_map: {}  # 手动群聊→Sheet 映射（群名包含 Sheet 名时可自动匹配）

server:
  host: "127.0.0.1"
  port: 8888
```

`config.example.yaml` 中的 `ai` 字段仅作为旧配置迁移和默认值参考。正常使用时
请通过“模型设置”页面管理模型，不要在 `config.yaml` 中保存 API Key。

首次使用或需要更换模板时，在步骤 1 点击“选择 Excel 模板”。服务端会校验
工作簿并保存本机副本；上传成功后，后续群聊匹配、预览和导出都会使用该模板。

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

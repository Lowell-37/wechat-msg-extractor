# WeChat Message Extractor

从微信本地数据库中提取群聊任务记录（🚩 格式），并写入 Excel 模板的 Web 工具。

## 功能

- 自动检测微信版本、进程和数据目录
- 自动提取数据库解密密钥并解密所有 MSG 分片库（MSG0~MSG3）
- 解析 🚩M.D 任务格式的群聊消息
- 将任务和情况分析写入 Excel 模板对应 Sheet
- 支持群聊→Sheet 自动匹配和手动映射
- Web 界面（FastAPI + htmx），操作流程清晰

## 使用流程

1. **Step 1 鉴权**：自动扫描微信进程 → 提取密钥 → 解密数据库
2. **Step 2 选群聊**：显示所有群聊列表，可搜索、选择目标群聊和对应 Sheet
3. **Step 3 预览导出**：预览解析的任务消息，选择导出目标和输出路径

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py
```

打开浏览器访问 `http://127.0.0.1:8888`

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

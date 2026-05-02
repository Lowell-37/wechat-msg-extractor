# 微信聊天记录提取工具 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个本地 Web 工具，从 Windows 微信 PC 版数据库提取指定群聊的任务消息，解析后填充到 Excel 模板对应单元格。

**Architecture:** Python 单体 Web 应用（FastAPI + Jinja2 + htmx），通过 pymem 提取密钥、sqlcipher3 解密 MSG.db、正则解析任务格式、openpyxl 写入 Excel。

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, Jinja2, htmx, pymem, sqlcipher3, openpyxl, psutil

---

## 文件结构

```
wechat-extractor/
├── app.py                  # FastAPI 入口，路由 + SSE
├── config.yaml             # 配置文件
├── requirements.txt        # Python 依赖
├── config.py               # 配置加载模块
├── static/
│   └── style.css           # 全局样式
├── templates/
│   ├── base.html           # 基础布局（含 htmx CDN）
│   ├── step1_connect.html  # 步骤1：连接鉴权
│   ├── step2_select.html   # 步骤2：选择群聊
│   └── step3_preview.html  # 步骤3：预览导出
├── core/
│   ├── __init__.py
│   ├── scanner.py          # WeChatScanner
│   ├── key_extractor.py    # KeyExtractor
│   ├── db_decryptor.py     # DBDecryptor
│   ├── message_fetcher.py  # MessageFetcher
│   ├── task_parser.py      # TaskParser
│   ├── matcher.py          # SheetMatcher
│   ├── excel_writer.py     # ExcelWriter
│   └── progress.py         # ProgressHub
├── tests/
│   ├── __init__.py
│   ├── test_task_parser.py
│   ├── test_matcher.py
│   ├── test_excel_writer.py
│   └── test_message_fetcher.py
├── export/
│   ├── excel/
│   ├── images/
│   ├── voice/
│   └── video/
└── docs/superpowers/
    ├── specs/2026-05-02-wechat-extractor-design.md
    └── plans/2026-05-02-wechat-extractor-plan.md
```

---

### Task 1: 项目脚手架

**Files:**
- Create: `requirements.txt`
- Create: `config.yaml`
- Create: `config.py`
- Create: `core/__init__.py`
- Create: `tests/__init__.py`
- Create: 目录 `static/`, `templates/`, `export/excel/`, `export/images/`, `export/voice/`, `export/video/`

- [ ] **Step 1: 创建 requirements.txt**

```python
# Write: requirements.txt
fastapi==0.115.6
uvicorn[standard]==0.34.0
jinja2==3.1.4
sse-starlette==2.2.1
openpyxl==3.1.5
psutil==6.1.1
pymem==1.14.0
pysqlcipher3==1.2.0
pyyaml==6.0.2
python-multipart==0.0.19
```

- [ ] **Step 2: 创建 config.yaml**

```yaml
wechat:
  auto_detect: true
  data_dir: null
  version_dir: null

excel:
  template_path: "D:/assistants/任务安排与情况分析.xlsx"
  output_dir: "./export/excel"

matching:
  group_sheet_map: {}

task_parsing:
  task_msg_pattern: null
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

- [ ] **Step 3: 创建 config.py**

```python
import os
import yaml
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class WeChatConfig:
    auto_detect: bool = True
    data_dir: Optional[str] = None
    version_dir: Optional[str] = None


@dataclass
class ExcelConfig:
    template_path: str = "D:/assistants/任务安排与情况分析.xlsx"
    output_dir: str = "./export/excel"


@dataclass
class MatchingConfig:
    group_sheet_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class TaskParsingConfig:
    task_msg_pattern: Optional[str] = None
    date_pattern: Optional[str] = None
    task_item_pattern: Optional[str] = None


@dataclass
class MediaConfig:
    save_images: bool = True
    save_voice: bool = True
    save_video: bool = True
    export_dir: str = "./export"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8888


@dataclass
class AppConfig:
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    excel: ExcelConfig = field(default_factory=ExcelConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    task_parsing: TaskParsingConfig = field(default_factory=TaskParsingConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "AppConfig":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, path)
        if not os.path.exists(config_path):
            return cls()
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(
            wechat=WeChatConfig(**data.get("wechat", {})),
            excel=ExcelConfig(**data.get("excel", {})),
            matching=MatchingConfig(**data.get("matching", {})),
            task_parsing=TaskParsingConfig(**data.get("task_parsing", {})),
            media=MediaConfig(**data.get("media", {})),
            server=ServerConfig(**data.get("server", {})),
        )
```

- [ ] **Step 4: 创建空 __init__.py 和目录**

```bash
mkdir -p D:/AI-Agent/wechat-extractor/{core,tests,static,templates,export/{excel,images,voice,video}}
touch D:/AI-Agent/wechat-extractor/core/__init__.py
touch D:/AI-Agent/wechat-extractor/tests/__init__.py
```

- [ ] **Step 5: 安装依赖**

```bash
cd D:/AI-Agent/wechat-extractor
pip install -r requirements.txt
```

---

### Task 2: TaskParser — 任务消息解析

**Files:**
- Create: `core/task_parser.py`
- Create: `tests/test_task_parser.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_task_parser.py
import pytest
from datetime import date
from core.task_parser import TaskParser, ParsedTask


class TestTaskParser:
    def test_is_task_message_positive(self):
        parser = TaskParser()
        msg = "🚩5.2 任务\n1⃣ 订正作文\n2⃣ 完形填空"
        assert parser.is_task_message(msg) is True

    def test_is_task_message_negative(self):
        parser = TaskParser()
        assert parser.is_task_message("今天的作业做完了吗") is False
        assert parser.is_task_message("好的收到") is False
        assert parser.is_task_message("") is False

    def test_extract_date(self):
        parser = TaskParser(year=2026)
        result = parser.parse("🚩5.2 任务\n1⃣ 订正作文")
        assert result.date == date(2026, 5, 2)

    def test_extract_date_with_single_digit_month(self):
        parser = TaskParser(year=2026)
        result = parser.parse("🚩12.25 任务\n1⃣ 复习")
        assert result.date == date(2026, 12, 25)

    def test_extract_tasks(self):
        parser = TaskParser(year=2026)
        msg = "🚩5.2 任务\n1⃣ 订正作文\n2⃣ 完形填空，首字母填空，语法填空各一篇\n3⃣ 背单词"
        result = parser.parse(msg)
        assert len(result.tasks) == 3
        assert result.tasks[0] == "订正作文"
        assert result.tasks[1] == "完形填空，首字母填空，语法填空各一篇"
        assert result.tasks[2] == "背单词"

    def test_parse_returns_none_for_non_task(self):
        parser = TaskParser(year=2026)
        result = parser.parse("普通消息")
        assert result is None

    def test_date_excel_serial(self):
        parser = TaskParser(year=2026)
        result = parser.parse("🚩5.2 任务\n1⃣ 测试")
        # 1899-12-30 is Excel epoch
        from datetime import datetime
        expected_serial = (datetime(2026, 5, 2) - datetime(1899, 12, 30)).days
        assert result.date_excel_serial == expected_serial
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_task_parser.py -v
# Expected: ModuleNotFoundError: No module named 'core.task_parser'
```

- [ ] **Step 3: 实现 TaskParser**

```python
# core/task_parser.py
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List


@dataclass
class ParsedTask:
    msg_id: int
    date: date
    date_excel_serial: int
    raw_text: str
    tasks: List[str] = field(default_factory=list)


class TaskParser:
    def __init__(
        self,
        year: Optional[int] = None,
        task_msg_pattern: Optional[str] = None,
        date_pattern: Optional[str] = None,
        task_item_pattern: Optional[str] = None,
    ):
        self.year = year or date.today().year
        self._task_msg_re = re.compile(
            task_msg_pattern or r"🚩\s*\d+\.\d+\s*任务"
        )
        self._date_re = re.compile(date_pattern or r"🚩\s*(\d+)\.(\d+)")
        self._task_item_re = re.compile(
            task_item_pattern or r"\d+⃣\s*(.+?)(?=\d+⃣|$)",
            re.DOTALL,
        )
        self._excel_epoch = datetime(1899, 12, 30)

    def is_task_message(self, text: str) -> bool:
        if not text:
            return False
        return bool(self._task_msg_re.search(text))

    def parse(self, text: str, msg_id: int = 0) -> Optional[ParsedTask]:
        if not self.is_task_message(text):
            return None

        date_match = self._date_re.search(text)
        if not date_match:
            return None

        month = int(date_match.group(1))
        day = int(date_match.group(2))
        task_date = date(self.year, month, day)
        excel_serial = (datetime(task_date.year, task_date.month, task_date.day) - self._excel_epoch).days

        tasks = []
        for m in self._task_item_re.finditer(text):
            task_text = m.group(1).strip()
            if task_text:
                tasks.append(task_text)

        # Fallback: if no task items found, treat entire message after header as one task
        if not tasks:
            header_end = date_match.end()
            remaining = text[header_end:].strip()
            if remaining:
                tasks.append(remaining)

        return ParsedTask(
            msg_id=msg_id,
            date=task_date,
            date_excel_serial=excel_serial,
            raw_text=text,
            tasks=tasks,
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_task_parser.py -v
# Expected: all 6 tests PASS
```

---

### Task 3: SheetMatcher — 群名匹配 Excel Sheet

**Files:**
- Create: `core/matcher.py`
- Create: `tests/test_matcher.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_matcher.py
import pytest
from core.matcher import SheetMatcher, MatchResult


class TestSheetMatcher:
    @pytest.fixture
    def sheet_names(self):
        return ["张三", "李四", "王五", "Sheet1", "烧烤爽", "闫明明"]

    @pytest.fixture
    def manual_map(self):
        return {"0501初2027广州李四": "李四"}

    def test_auto_match_basic(self, sheet_names):
        matcher = SheetMatcher(sheet_names)
        results = matcher.match(["0601初2027广州张三", "班群通知"])
        assert results["0601初2027广州张三"] == "张三"
        assert results["班群通知"] is None

    def test_manual_map_priority(self, sheet_names, manual_map):
        matcher = SheetMatcher(sheet_names, manual_map=manual_map)
        results = matcher.match(["0501初2027广州李四"])
        assert results["0501初2027广州李四"] == "李四"

    def test_no_match(self, sheet_names):
        matcher = SheetMatcher(sheet_names)
        results = matcher.match(["未知群聊", "另一个群"])
        assert results["未知群聊"] is None
        assert results["另一个群"] is None

    def test_ambiguous_match_picks_first(self, sheet_names):
        # 张三丰 contains 张三, should still match
        matcher = SheetMatcher(sheet_names + ["张三丰"])
        results = matcher.match(["张三丰学习群"])
        # Should match "张三丰" (longer match) or "张三" (shorter)
        # Current simple strategy: first matching sheet in list wins
        assert results["张三丰学习群"] in ("张三", "张三丰")

    def test_get_matched_sheets(self, sheet_names):
        matcher = SheetMatcher(sheet_names)
        matcher.match(["0601初2027广州张三", "班群通知"])
        matched = matcher.get_matched_sheets()
        assert "张三" in matched
        assert "班群通知" not in matched
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_matcher.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: 实现 SheetMatcher**

```python
# core/matcher.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MatchResult:
    group_name: str
    matched_sheet: Optional[str] = None
    method: str = "unmatched"  # "manual", "auto", "unmatched"


class SheetMatcher:
    def __init__(
        self,
        sheet_names: List[str],
        manual_map: Optional[Dict[str, str]] = None,
    ):
        self.sheet_names = sheet_names
        self.manual_map = manual_map or {}
        self._results: Dict[str, str] = {}

    def match(self, group_names: List[str]) -> Dict[str, Optional[str]]:
        self._results = {}
        for group_name in group_names:
            self._results[group_name] = self._match_one(group_name)
        return dict(self._results)

    def _match_one(self, group_name: str) -> Optional[str]:
        # 1. Manual map takes priority
        if group_name in self.manual_map:
            sheet = self.manual_map[group_name]
            if sheet in self.sheet_names:
                return sheet

        # 2. Auto match: sheet name fully contained in group name
        #    Prefer longer sheet names first to avoid 张三 matching before 张三丰
        sorted_sheets = sorted(self.sheet_names, key=len, reverse=True)
        for sheet in sorted_sheets:
            if sheet in group_name:
                return sheet

        return None

    def get_matched_sheets(self) -> List[str]:
        return [s for s in self._results.values() if s is not None]

    def get_all_results(self) -> List[MatchResult]:
        results = []
        for group_name, sheet in self._results.items():
            method = "unmatched"
            if group_name in self.manual_map:
                method = "manual"
            elif sheet is not None:
                method = "auto"
            results.append(MatchResult(
                group_name=group_name,
                matched_sheet=sheet,
                method=method,
            ))
        return results
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_matcher.py -v
# Expected: all 5 tests PASS
```

---

### Task 4: WeChatScanner — 微信环境扫描

**Files:**
- Create: `core/scanner.py`

- [ ] **Step 1: 实现 WeChatScanner**

```python
# core/scanner.py
import os
import glob
from dataclasses import dataclass, field
from typing import Optional, List
import psutil


@dataclass
class WeChatInfo:
    version: Optional[str] = None
    install_path: Optional[str] = None
    data_dir: Optional[str] = None
    pid: Optional[int] = None
    exe_path: Optional[str] = None
    db_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class WeChatScanner:
    _INSTALL_BASE = r"C:\Program Files\Tencent\WeChat"
    _DATA_BASE = os.path.expandvars(r"%APPDATA%\Tencent\WeChat")

    def scan(self) -> WeChatInfo:
        info = WeChatInfo()
        self._detect_install(info)
        self._detect_process(info)
        self._detect_data_dir(info)
        return info

    def _detect_install(self, info: WeChatInfo):
        if not os.path.exists(self._INSTALL_BASE):
            info.errors.append(f"微信安装目录不存在: {self._INSTALL_BASE}")
            return
        try:
            versions = sorted(
                [d for d in os.listdir(self._INSTALL_BASE)
                 if os.path.isdir(os.path.join(self._INSTALL_BASE, d))
                 and d.startswith("[") and d.endswith("]")],
                reverse=True,
            )
            if versions:
                info.version = versions[0].strip("[]")
                info.install_path = os.path.join(self._INSTALL_BASE, versions[0])
        except Exception as e:
            info.errors.append(f"读取安装目录失败: {e}")

    def _detect_process(self, info: WeChatInfo):
        try:
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                if proc.info["name"] and proc.info["name"].lower() == "wechat.exe":
                    info.pid = proc.info["pid"]
                    info.exe_path = proc.info["exe"]
                    if not info.version and proc.info["exe"]:
                        # Extract version from exe path
                        exe_dir = os.path.dirname(proc.info["exe"])
                        info.install_path = exe_dir
                        parent = os.path.basename(exe_dir)
                        if parent.startswith("[") and parent.endswith("]"):
                            info.version = parent.strip("[]")
                    return
        except Exception as e:
            info.errors.append(f"查找微信进程失败: {e}")

    def _detect_data_dir(self, info: WeChatInfo):
        if not os.path.exists(self._DATA_BASE):
            info.errors.append(f"微信数据目录不存在: {self._DATA_BASE}")
            return
        try:
            # Find user data dirs containing MSG.db
            for entry in os.listdir(self._DATA_BASE):
                full_path = os.path.join(self._DATA_BASE, entry)
                if os.path.isdir(full_path):
                    db_files = glob.glob(os.path.join(full_path, "MSG*.db"))
                    if db_files:
                        info.data_dir = full_path
                        info.db_files = [os.path.basename(f)
                                         for f in db_files]
                        return
            # Fallback: use first subdir
            subdirs = [d for d in os.listdir(self._DATA_BASE)
                       if os.path.isdir(os.path.join(self._DATA_BASE, d))]
            if subdirs:
                info.data_dir = os.path.join(self._DATA_BASE, subdirs[0])
        except Exception as e:
            info.errors.append(f"读取数据目录失败: {e}")
```

---

### Task 5: KeyExtractor — 密钥提取

**Files:**
- Create: `core/key_extractor.py`

- [ ] **Step 1: 实现 KeyExtractor**

```python
# core/key_extractor.py
import re
import ctypes
from typing import Optional
import pymem
import pymem.process


class KeyExtractor:
    """从 WeChat.exe 进程内存中提取数据库加密密钥。

    支持 32 位和 64 位微信版本。
    密钥格式：64 字符 hex 字符串，用于 sqlcipher PRAGMA key。
    """

    def __init__(self, pid: int):
        self.pid = pid
        self._pm: Optional[pymem.Pymem] = None

    def extract(self) -> Optional[str]:
        try:
            self._pm = pymem.Pymem()
            self._pm.open_process_from_id(self.pid)

            is_64bit = self._is_64bit_process()
            key = self._search_key(is_64bit)

            if key and self._validate_key(key):
                return key

            return None
        except Exception:
            return None

    def _is_64bit_process(self) -> bool:
        try:
            # Check if the process is 64-bit by examining the PE header
            # A simpler approach: check if the process module's base address > 32-bit
            modules = list(self._pm.list_modules())
            if modules:
                base = modules[0].lpBaseOfDll
                return base > 0xFFFFFFFF
        except Exception:
            pass
        return True  # Default to 64-bit for modern WeChat

    def _search_key(self, is_64bit: bool) -> Optional[str]:
        """在内存中搜索疑似数据库密钥的 64 位 hex 字符串。"""
        try:
            # WeChat stores the key near the sqlcipher PRAGMA key string
            # Search common patterns in process memory
            for module in self._pm.list_modules():
                if "WeChat" not in module.name and "wechat" not in module.name.lower():
                    continue
                try:
                    data = self._pm.read_bytes(module.lpBaseOfDll, module.SizeOfImage)

                    # Look for 64-char hex strings (32 bytes of key material)
                    # Pattern: appears near "DBKey" or "key" strings
                    matches = re.findall(rb"[0-9a-fA-F]{64}", data)
                    for match in matches:
                        key = match.decode("ascii")
                        if self._validate_key(key):
                            return key
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def _validate_key(self, key: str) -> bool:
        """验证密钥格式：64 字符 hex 字符串。"""
        if not key or len(key) != 64:
            return False
        try:
            int(key, 16)
            return True
        except ValueError:
            return False
```

---

### Task 6: DBDecryptor — 数据库解密

**Files:**
- Create: `core/db_decryptor.py`

- [ ] **Step 1: 实现 DBDecryptor**

```python
# core/db_decryptor.py
import os
import shutil
import tempfile
from typing import Optional, List, Any
from contextlib import contextmanager

try:
    from pysqlcipher3 import dbapi2 as sqlcipher
    HAS_SQLCIPHER = True
except ImportError:
    HAS_SQLCIPHER = False


class DBDecryptor:
    """使用 sqlcipher 解密微信本地数据库。"""

    def __init__(self, db_path: str, key: str):
        if not HAS_SQLCIPHER:
            raise ImportError(
                "pysqlcipher3 未安装。请安装: pip install pysqlcipher3"
            )
        self.db_path = db_path
        self.key = key
        self._conn = None
        self._temp_dir = None
        self._temp_db_path = None

    def open(self):
        # Copy DB to temp to avoid file lock conflicts with WeChat
        self._temp_dir = tempfile.mkdtemp(prefix="wechat_extract_")
        self._temp_db_path = os.path.join(self._temp_dir, "MSG_decrypted.db")
        shutil.copy2(self.db_path, self._temp_db_path)

        self._conn = sqlcipher.connect(self._temp_db_path)
        cursor = self._conn.cursor()
        # Set the encryption key
        cursor.execute(f"PRAGMA key=\"x'{self.key}'\"")
        # Verify decryption works
        cursor.execute("SELECT count(*) FROM sqlite_master")
        count = cursor.fetchone()[0]
        if count == 0:
            raise ValueError("数据库解密失败：密钥无效或数据库结构异常")
        cursor.close()
        return self

    def execute_query(self, sql: str, params: tuple = ()) -> List[tuple]:
        if not self._conn:
            raise RuntimeError("数据库未打开，请先调用 open()")
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._temp_dir and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    @contextmanager
    def connection(self):
        try:
            self.open()
            yield self
        finally:
            self.close()
```

---

### Task 7: MessageFetcher — 消息查询

**Files:**
- Create: `core/message_fetcher.py`
- Create: `tests/test_message_fetcher.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_message_fetcher.py
import pytest
from datetime import datetime, date
from core.message_fetcher import MessageFetcher, Message


class TestMessageFetcher:
    @pytest.fixture
    def mock_decryptor(self):
        class MockDecryptor:
            def execute_query(self, sql, params=()):
                return [
                    (1, 1714636800, 1, 0, 1, "🚩5.2 任务\n1⃣ 订正作文", "张三"),
                    (2, 1714637000, 1, 0, 0, "好的收到", "自己"),
                ]

            def close(self):
                pass

            def connection(self):
                return self
        return MockDecryptor()

    def test_fetch_text_messages(self, mock_decryptor):
        fetcher = MessageFetcher(mock_decryptor)
        messages = fetcher.fetch_messages(
            chat_id="test_chatroom",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 2),
        )
        assert len(messages) == 2
        assert messages[0].content == "🚩5.2 任务\n1⃣ 订正作文"
        assert messages[0].sender == "张三"
        assert messages[0].type == 1

    def test_filter_task_messages_only(self, mock_decryptor):
        fetcher = MessageFetcher(mock_decryptor)
        messages = fetcher.fetch_messages(
            chat_id="test_chatroom",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 2),
            task_only=True,
        )
        assert len(messages) == 1
        assert "🚩" in messages[0].content
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_message_fetcher.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: 实现 MessageFetcher**

```python
# core/message_fetcher.py
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from core.db_decryptor import DBDecryptor
from core.task_parser import TaskParser


@dataclass
class Message:
    msg_id: int
    timestamp: int
    type: int
    sub_type: int
    is_sender: int
    content: str
    sender: str

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)


class MessageFetcher:
    def __init__(self, decryptor: DBDecryptor):
        self._db = decryptor

    def fetch_messages(
        self,
        chat_id: str,
        start_date: date,
        end_date: date,
        task_only: bool = False,
    ) -> List[Message]:
        start_ts = int(datetime(start_date.year, start_date.month, start_date.day).timestamp())
        end_ts = int(datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59).timestamp())

        sql = """
            SELECT local_id, CreateTime, Type, SubType, IsSender, StrContent, StrTalker
            FROM MSG
            WHERE StrTalker = ?
              AND CreateTime BETWEEN ? AND ?
              AND Type = 1
            ORDER BY CreateTime ASC
        """
        rows = self._db.execute_query(sql, (chat_id, start_ts, end_ts))

        messages = []
        for row in rows:
            msg = Message(
                msg_id=row[0],
                timestamp=row[1],
                type=row[2],
                sub_type=row[3],
                is_sender=row[4],
                content=row[5] or "",
                sender=row[6] or "",
            )
            if task_only:
                parser = TaskParser()
                if parser.is_task_message(msg.content):
                    messages.append(msg)
            else:
                messages.append(msg)

        return messages

    def get_chatrooms(self) -> List[tuple]:
        """获取所有群聊列表。返回 [(chatroom_id, chatroom_name), ...]"""
        sql = "SELECT chatRoomName, UserNameList, DisplayNameList FROM ChatRoom"
        try:
            rows = self._db.execute_query(sql)
            return [(row[0], row[0]) for row in rows]
        except Exception:
            return []
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_message_fetcher.py -v
# Expected: 2 tests PASS
```

---

### Task 8: ExcelWriter — Excel 写入

**Files:**
- Create: `core/excel_writer.py`
- Create: `tests/test_excel_writer.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_excel_writer.py
import os
import tempfile
import pytest
from datetime import date
from core.excel_writer import ExcelWriter
from core.task_parser import ParsedTask
import openpyxl


@pytest.fixture
def temp_template():
    """Create a temporary Excel template matching the real structure."""
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "张三"
    ws["A1"] = "安排时间"
    ws["B1"] = "任务安排"
    ws["C1"] = "情况分析"
    # Pre-fill some existing data
    ws["A2"] = 46145  # 2026-05-02 serial
    ws["B2"] = "旧任务"
    ws["C2"] = "已完成"
    wb.save(path)
    wb.close()
    return path


@pytest.fixture
def parsed_task():
    return ParsedTask(
        msg_id=1,
        date=date(2026, 5, 2),
        date_excel_serial=46145,
        raw_text="🚩5.2 任务\n1⃣ 订正作文\n2⃣ 完形填空",
        tasks=["订正作文", "完形填空，首字母填空，语法填空各一篇"],
    )


class TestExcelWriter:
    def test_write_to_existing_date(self, temp_template, parsed_task):
        output_path = temp_template.replace(".xlsx", "_out.xlsx")
        writer = ExcelWriter(temp_template)
        writer.add_task("张三", parsed_task)
        writer.save(output_path)

        wb = openpyxl.load_workbook(output_path)
        ws = wb["张三"]
        assert ws["A2"].value == 46145  # Existing date
        assert "订正作文" in str(ws["B2"].value)
        assert "完形填空" in str(ws["B2"].value)
        assert ws["C2"].value == "已完成"  # C column preserved
        wb.close()
        os.unlink(output_path)

    def test_write_to_new_date(self, temp_template):
        new_task = ParsedTask(
            msg_id=2,
            date=date(2026, 4, 30),
            date_excel_serial=46143,
            raw_text="🚩4.30 任务\n1⃣ 语法填空",
            tasks=["语法填空"],
        )
        output_path = temp_template.replace(".xlsx", "_out2.xlsx")
        writer = ExcelWriter(temp_template)
        writer.add_task("张三", new_task)
        writer.save(output_path)

        wb = openpyxl.load_workbook(output_path)
        ws = wb["张三"]
        # Should have appended to last row
        assert ws["A3"].value == 46143
        assert "语法填空" in str(ws["B3"].value)
        wb.close()
        os.unlink(output_path)

    def test_preserves_other_sheets(self, temp_template, parsed_task):
        # Add another sheet that should remain untouched
        wb = openpyxl.load_workbook(temp_template)
        ws2 = wb.create_sheet("李四")
        ws2["A1"] = "安排时间"
        ws2["B1"] = "任务安排"
        ws2["C1"] = "情况分析"
        wb.save(temp_template)
        wb.close()

        output_path = temp_template.replace(".xlsx", "_out3.xlsx")
        writer = ExcelWriter(temp_template)
        writer.add_task("张三", parsed_task)
        writer.save(output_path)

        wb = openpyxl.load_workbook(output_path)
        assert "李四" in wb.sheetnames
        wb.close()
        os.unlink(output_path)

    def test_skip_sheet_not_found(self, temp_template, parsed_task):
        writer = ExcelWriter(temp_template)
        output_path = temp_template.replace(".xlsx", "_out4.xlsx")
        writer.add_task("不存在的Sheet", parsed_task)
        writer.save(output_path)
        # Should not crash, just skip the write
        assert os.path.exists(output_path)
        os.unlink(output_path)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_excel_writer.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: 实现 ExcelWriter**

```python
# core/excel_writer.py
import os
import copy
from typing import Optional, List
from datetime import date
import openpyxl

from core.task_parser import ParsedTask


class ExcelWriter:
    def __init__(self, template_path: str):
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Excel 模板不存在: {template_path}")
        self.template_path = template_path
        self._wb = openpyxl.load_workbook(template_path)
        self._excel_epoch = openpyxl.utils.datetime.to_excel
        # Track written tasks per sheet to avoid overwriting during same session
        self._sheet_date_map: dict = {}  # sheet_name -> {date: row_number}

    def add_task(self, sheet_name: str, task: ParsedTask):
        if sheet_name not in self._wb.sheetnames:
            return

        ws = self._wb[sheet_name]
        target_row = self._find_or_create_date_row(ws, task)

        # Write task content to B column
        if target_row:
            task_text = "\n".join(f"{i+1}、{t}" for i, t in enumerate(task.tasks))
            existing = ws.cell(row=target_row, column=2).value
            if existing:
                # Overwrite with new content (覆盖当日模式)
                ws.cell(row=target_row, column=2).value = task_text
            else:
                ws.cell(row=target_row, column=2).value = task_text

    def _find_or_create_date_row(self, ws, task: ParsedTask) -> int:
        # Search column A for the date serial
        for row in range(2, ws.max_row + 1):
            cell_val = ws.cell(row=row, column=1).value
            if cell_val is not None and int(cell_val) == task.date_excel_serial:
                return row

        # Date not found: append a new row
        new_row = ws.max_row + 1
        ws.cell(row=new_row, column=1).value = task.date_excel_serial
        # Format as date
        ws.cell(row=new_row, column=1).number_format = 'YYYY-MM-DD'
        return new_row

    def save(self, output_path: str):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self._wb.save(output_path)

    def close(self):
        self._wb.close()

    def get_sheet_names(self) -> List[str]:
        return self._wb.sheetnames
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/AI-Agent/wechat-extractor
python -m pytest tests/test_excel_writer.py -v
# Expected: 4 tests PASS
```

---

### Task 9: ProgressHub — SSE 进度推送

**Files:**
- Create: `core/progress.py`

- [ ] **Step 1: 实现 ProgressHub**

```python
# core/progress.py
import asyncio
from typing import Dict, List, AsyncGenerator
from dataclasses import dataclass, field


@dataclass
class ProgressEvent:
    stage: str
    message: str
    progress: int  # 0-100
    detail: dict = field(default_factory=dict)


class ProgressHub:
    def __init__(self):
        self._listeners: Dict[str, asyncio.Queue] = {}

    def register(self, session_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._listeners[session_id] = queue
        return queue

    def unregister(self, session_id: str):
        self._listeners.pop(session_id, None)

    async def emit(self, session_id: str, event: ProgressEvent):
        queue = self._listeners.get(session_id)
        if queue:
            await queue.put(event)

    async def event_stream(self, session_id: str) -> AsyncGenerator[str, None]:
        queue = self.register(session_id)
        try:
            while True:
                event = await queue.get()
                yield f"data: {event.stage}|{event.message}|{event.progress}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self.unregister(session_id)


# Global instance
progress_hub = ProgressHub()
```

---

### Task 10: HTML 模板

**Files:**
- Create: `templates/base.html`
- Create: `templates/step1_connect.html`
- Create: `templates/step2_select.html`
- Create: `templates/step3_preview.html`

- [ ] **Step 1: 创建 base.html**

```html
<!-- templates/base.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>微信聊天提取工具</title>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://unpkg.com/htmx-ext-sse@2.2.2/sse.js"></script>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="container">
        <header>
            <h1>微信聊天提取工具</h1>
            <nav id="steps">
                <span class="step {% if step == 1 %}active{% endif %}">1. 连接鉴权</span>
                <span class="step-divider">→</span>
                <span class="step {% if step == 2 %}active{% endif %}">2. 选择群聊</span>
                <span class="step-divider">→</span>
                <span class="step {% if step == 3 %}active{% endif %}">3. 预览导出</span>
            </nav>
        </header>
        <main id="main-content">
            {% block content %}{% endblock %}
        </main>
        <footer>
            <p>数据仅存储在本地，不上传任何信息</p>
        </footer>
    </div>
    {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 2: 创建 step1_connect.html**

```html
<!-- templates/step1_connect.html -->
{% extends "base.html" %}
{% block content %}
<div class="card" id="step1-card">
    <h2>步骤 1：连接与鉴权</h2>

    <div hx-get="/api/wechat/status" hx-trigger="load" hx-swap="outerHTML">
        <div class="loading">正在检测微信环境...</div>
    </div>

    <div class="card-section">
        <h3>自动提取密钥</h3>
        <button class="btn btn-primary"
                hx-post="/api/key/extract"
                hx-target="#key-status"
                hx-swap="innerHTML">
            一键提取密钥并解密
        </button>
        <div id="key-status"></div>
    </div>

    <div class="card-section">
        <h3>手动输入密钥</h3>
        <form hx-post="/api/key/validate" hx-target="#manual-key-status" hx-swap="innerHTML">
            <input type="text" name="key" placeholder="输入64位hex密钥" maxlength="64"
                   class="input-key" pattern="[0-9a-fA-F]{64}">
            <button type="submit" class="btn btn-secondary">验证并连接</button>
        </form>
        <div id="manual-key-status"></div>
    </div>

    <div id="next-step-area" style="display:none;">
        <a href="/step/2" class="btn btn-primary">下一步：选择群聊 →</a>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 3: 创建 step2_select.html**

```html
<!-- templates/step2_select.html -->
{% extends "base.html" %}
{% block content %}
<div class="card" id="step2-card">
    <h2>步骤 2：选择群聊与过滤</h2>

    <div class="form-group">
        <label>搜索群聊：</label>
        <input type="text" name="search" placeholder="输入群名关键词..."
               hx-get="/api/chatrooms/search"
               hx-trigger="keyup changed delay:300ms"
               hx-target="#chatroom-list"
               hx-swap="innerHTML"
               class="input-search">
    </div>

    <div class="form-group">
        <label>时间范围：</label>
        <input type="date" name="start_date" value="{{ start_date }}" class="input-date">
        <span>至</span>
        <input type="date" name="end_date" value="{{ end_date }}" class="input-date">
    </div>

    <div id="chatroom-list" hx-get="/api/chatrooms/list" hx-trigger="load">
        <div class="loading">正在加载群聊列表...</div>
    </div>

    <div class="btn-group">
        <a href="/step/1" class="btn btn-secondary">← 返回</a>
        <button class="btn btn-primary"
                hx-post="/api/preview"
                hx-target="#main-content"
                hx-swap="outerHTML">
            预览导出内容 →
        </button>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 4: 创建 step3_preview.html**

```html
<!-- templates/step3_preview.html -->
{% extends "base.html" %}
{% block content %}
<div class="card" id="step3-card">
    <h2>步骤 3：预览与导出</h2>

    <div id="preview-content" hx-get="/api/preview/data" hx-trigger="load">
        <div class="loading">正在解析任务消息...</div>
    </div>

    <div id="progress-area" style="display:none;">
        <div class="progress-bar">
            <div class="progress-fill" id="progress-fill" style="width:0%"></div>
        </div>
        <p id="progress-message"></p>
    </div>

    <div class="btn-group">
        <a href="/step/2" class="btn btn-secondary">← 返回</a>
        <button class="btn btn-primary"
                hx-post="/api/export"
                hx-target="#export-result"
                hx-swap="innerHTML">
            执行导出到 Excel
        </button>
    </div>

    <div id="export-result"></div>
</div>

<script>
document.body.addEventListener('htmx:sseMessage', function(evt) {
    var parts = evt.detail.data.split('|');
    var stage = parts[0];
    var message = parts[1];
    var progress = parseInt(parts[2]);

    document.getElementById('progress-area').style.display = 'block';
    document.getElementById('progress-fill').style.width = progress + '%';
    document.getElementById('progress-message').textContent = message;
});
</script>
{% endblock %}
```

---

### Task 11: CSS 样式

**Files:**
- Create: `static/style.css`

- [ ] **Step 1: 创建 style.css**

```css
/* static/style.css */
:root {
    --primary: #07c160;
    --primary-hover: #06ad56;
    --bg: #f5f5f5;
    --card-bg: #ffffff;
    --text: #333333;
    --text-secondary: #888888;
    --border: #e0e0e0;
    --danger: #fa5151;
    --warning: #ffc300;
    --radius: 8px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}

.container {
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
}

header {
    text-align: center;
    margin-bottom: 30px;
}

header h1 {
    font-size: 24px;
    margin-bottom: 12px;
    color: var(--primary);
}

#steps {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
    font-size: 14px;
}

.step {
    padding: 4px 12px;
    border-radius: 12px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    color: var(--text-secondary);
}

.step.active {
    background: var(--primary);
    color: white;
    border-color: var(--primary);
}

.step-divider { color: var(--text-secondary); }

.card {
    background: var(--card-bg);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.card h2 {
    font-size: 18px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
}

.card-section {
    margin-bottom: 20px;
    padding: 16px;
    background: #fafafa;
    border-radius: var(--radius);
}

.card-section h3 {
    font-size: 15px;
    margin-bottom: 12px;
}

.form-group {
    margin-bottom: 16px;
}

.form-group label {
    display: block;
    margin-bottom: 6px;
    font-weight: 500;
    font-size: 14px;
}

.input-search, .input-key, .input-date {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font-size: 14px;
    outline: none;
    transition: border-color 0.2s;
}

.input-search:focus, .input-key:focus, .input-date:focus {
    border-color: var(--primary);
}

.input-date {
    width: auto;
    display: inline-block;
}

.btn {
    display: inline-block;
    padding: 10px 20px;
    border: none;
    border-radius: var(--radius);
    font-size: 14px;
    cursor: pointer;
    text-decoration: none;
    transition: background 0.2s;
}

.btn-primary {
    background: var(--primary);
    color: white;
}
.btn-primary:hover { background: var(--primary-hover); }

.btn-secondary {
    background: #f0f0f0;
    color: var(--text);
}
.btn-secondary:hover { background: #e0e0e0; }

.btn-group {
    margin-top: 20px;
    display: flex;
    gap: 12px;
    justify-content: center;
}

.loading {
    text-align: center;
    padding: 20px;
    color: var(--text-secondary);
}

.chatroom-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
}

.chatroom-item:hover { background: #f9f9f9; }

.chatroom-item.selected { background: #e8f8ee; border-left: 3px solid var(--primary); }

.chatroom-name { flex: 1; }
.chatroom-sheet {
    font-size: 12px;
    color: var(--primary);
    background: #e8f8ee;
    padding: 2px 8px;
    border-radius: 10px;
}

.chatroom-sheet.unmatched {
    color: var(--danger);
    background: #fde8e8;
}

.chatroom-sheet-select {
    margin-left: 8px;
    padding: 4px;
    border-radius: 4px;
    border: 1px solid var(--border);
}

.preview-table {
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 13px;
}

.preview-table th, .preview-table td {
    padding: 10px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border);
}

.preview-table th {
    background: #fafafa;
    font-weight: 500;
}

.preview-table .task-list {
    margin: 0;
    padding-left: 16px;
}

.preview-table .task-list li {
    margin-bottom: 2px;
}

.progress-bar {
    width: 100%;
    height: 8px;
    background: var(--border);
    border-radius: 4px;
    overflow: hidden;
    margin: 16px 0;
}

.progress-fill {
    height: 100%;
    background: var(--primary);
    transition: width 0.3s;
    border-radius: 4px;
}

.status-ok { color: var(--primary); }
.status-err { color: var(--danger); }
.status-warn { color: var(--warning); }

footer {
    text-align: center;
    padding: 20px;
    color: var(--text-secondary);
    font-size: 12px;
}
```

---

### Task 12: FastAPI 应用入口

**Files:**
- Create: `app.py`

- [ ] **Step 1: 实现 app.py**

```python
# app.py
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Query, Form
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
            '<p>手动输入密钥：<input type="text" name="key" placeholder="64位hex密钥"></p>'
        )
    extractor = KeyExtractor(info.pid)
    key = extractor.extract()
    if not key:
        return HTMLResponse(
            '<p class="status-err">自动提取失败，请尝试手动输入密钥</p>'
            '<input type="text" name="key" placeholder="64位hex密钥" '
            'class="input-key" pattern="[0-9a-fA-F]{64}">'
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
        # Store in session
        session_id, state = get_session(request)
        state["decryptor"] = decryptor
        state["fetcher"] = MessageFetcher(decryptor)
        return HTMLResponse(
            f'<p class="status-ok">✔ 密钥提取成功并验证通过</p>'
            '<div id="next-step-area"><a href="/step/2" class="btn btn-primary">'
            '下一步：选择群聊 →</a></div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<p class="status-err">✘ 数据库解密失败：{e}</p>'
            f'<p>密钥：{key[:8]}...{key[-8:]}</p>'
            '<p>请尝试手动输入密钥</p>'
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

    # Match with Excel sheets
    excel_path = config.excel.template_path
    sheet_names = []
    try:
        from core.excel_writer import ExcelWriter
        writer = ExcelWriter(excel_path)
        sheet_names = writer.get_sheet_names()
        writer.close()
    except Exception:
        pass

    matcher = SheetMatcher(sheet_names, manual_map=config.matching.group_sheet_map)
    group_names = [c[0] for c in chatrooms]
    matches = matcher.match(group_names)

    html_parts = []
    for group_name in group_names:
        matched = matches.get(group_name)
        sheet_class = "matched" if matched else "unmatched"
        sheet_text = matched or "未匹配"
        html_parts.append(
            f'<div class="chatroom-item" data-group="{group_name}">'
            f'<span class="chatroom-name">{group_name}</span>'
            f'<span class="chatroom-sheet {sheet_class}">{sheet_text}</span>'
            f'<select class="chatroom-sheet-select" '
            f'hx-post="/api/chatrooms/override" hx-trigger="change" '
            f'hx-vals=\'{{"group": "{group_name}", "sheet": this.value}}\'>'
            f'<option value="">--选择Sheet--</option>'
            + "".join(
                f'<option value="{s}" {"selected" if s == matched else ""}>{s}</option>'
                for s in sheet_names
            )
            + '</select></div>'
        )
    return HTMLResponse("".join(html_parts))


@app.get("/api/chatrooms/search")
async def search_chatrooms(request: Request, query: str = ""):
    # Reuse list endpoint with filter
    _, state = get_session(request)
    fetcher = state.get("fetcher")
    if not fetcher:
        return HTMLResponse('<p class="status-err">请先完成鉴权</p>')

    chatrooms = fetcher.get_chatrooms()
    if not query:
        return await list_chatrooms(request)

    # Filter by search query
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
        sheet_class = "matched" if matched_sheet else "unmatched"
        display = f"→ {matched_sheet}" if matched_sheet else "未匹配"
        html_parts.append(
            f'<div class="chatroom-item" data-group="{group_name}">'
            f'<span class="chatroom-name">{group_name}</span>'
            f'<span class="chatroom-sheet {sheet_class}">{display}</span>'
            f'<select class="chatroom-sheet-select" '
            f'onchange="document.querySelector(\'[data-group=\\\'{group_name}\\\']\')'
            f'.setAttribute(\'data-sheet\', this.value)">'
            f'<option value="">--选择--</option>'
            + "".join(
                f'<option value="{s}" {"selected" if s == matched_sheet else ""}>{s}</option>'
                for s in sheet_names
            )
            + '</select></div>'
        )
    return HTMLResponse("".join(html_parts) or '<p>无匹配群聊</p>')


@app.post("/api/preview")
async def preview(
    request: Request,
    group_name: str = Form(...),
    sheet_name: str = Form(...),
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

    # Generate preview HTML
    rows_html = ""
    for pt in parsed_tasks:
        task_items = "".join(
            f'<li>{t}</li>' for t in pt.tasks
        )
        rows_html += (
            f'<tr>'
            f'<td>{pt.date.isoformat()}</td>'
            f'<td><ul class="task-list">{task_items}</ul></td>'
            f'<td>{sheet_name}</td>'
            f'</tr>'
        )

    html = (
        f'<h3>预览：{group_name} → Sheet「{sheet_name}」</h3>'
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

    # SSE progress
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
        '<div id="progress-area">'
        '<div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>'
        '<p id="progress-message">正在准备导出...</p>'
        '</div>'
        '<div hx-ext="sse" sse-connect="/api/progress/stream" sse-swap="progress">'
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
```

---

### Task 13: 端到端验证

- [ ] **Step 1: 启动服务**

```bash
cd D:/AI-Agent/wechat-extractor
python app.py
# Expected: Uvicorn running on http://127.0.0.1:8888
```

- [ ] **Step 2: 打开浏览器访问 http://127.0.0.1:8888**

验证流程：
1. 步骤 1 显示微信检测状态
2. 点击「一键提取密钥」成功解密
3. 步骤 2 显示群聊列表和 Sheet 匹配
4. 选择群聊，设定时间范围
5. 步骤 3 预览解析出的任务
6. 点击「确认导出」生成 Excel 文件

- [ ] **Step 3: 验证导出结果**

```bash
python -c "
import openpyxl
wb = openpyxl.load_workbook('D:/AI-Agent/wechat-extractor/export/excel/任务记录_$(date +%Y-%m-%d).xlsx')
for sname in wb.sheetnames:
    ws = wb[sname]
    print(f'Sheet: {sname}')
    for row in ws.iter_rows(min_row=1, max_row=5, values_only=True):
        print(row)
    print('---')
"
```

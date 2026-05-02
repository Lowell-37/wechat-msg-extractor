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
            task_msg_pattern or r"\U0001f6a9\s*\d+\.\d+\s*任务"
        )
        self._date_re = re.compile(date_pattern or r"\U0001f6a9\s*(\d+)\.(\d+)")
        self._task_item_re = re.compile(
            task_item_pattern or r"\d+\ufe0f?\u20e3\s*(.+?)(?=\d+\ufe0f?\u20e3|$)",
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

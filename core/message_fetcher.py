from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

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
        start_ts = int(
            datetime(start_date.year, start_date.month, start_date.day).timestamp()
        )
        end_ts = int(
            datetime(
                end_date.year, end_date.month, end_date.day, 23, 59, 59
            ).timestamp()
        )

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

    def get_chatrooms(self) -> List[Tuple[str, str]]:
        """获取所有群聊列表。返回 [(chatroom_id, chatroom_name), ...]"""
        sql = "SELECT chatRoomName, UserNameList, DisplayNameList FROM ChatRoom"
        try:
            rows = self._db.execute_query(sql)
            return [(row[0], row[0]) for row in rows]
        except Exception:
            return []

import os
import shutil
import tempfile
from typing import Optional, List, Any
from contextlib import contextmanager

try:
    from sqlcipher3 import dbapi2 as sqlcipher
    HAS_SQLCIPHER = True
except ImportError:
    HAS_SQLCIPHER = False


class DBDecryptor:
    """使用 sqlcipher 解密微信本地数据库。"""

    def __init__(self, db_path: str, key: str):
        if not HAS_SQLCIPHER:
            raise ImportError(
                "sqlcipher3 未安装。请安装: pip install sqlcipher3"
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

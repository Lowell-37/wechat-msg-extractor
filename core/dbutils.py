"""数据库解密与查询工具。

WeChat 数据库使用自定义 AES-CBC 加密，非标准 SQLCipher。
使用 pywxdump 的密钥提取 + 手动 AES 解密。
"""

import hashlib
import hmac
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from Cryptodome.Cipher import AES

from core.scanner import WeChatScanner

SQLITE_FILE_HEADER = "SQLite format 3\x00"
KEY_SIZE = 32
DEFAULT_PAGESIZE = 4096


def decrypt_db_raw(key_hex: str, db_path: str, out_path: str) -> bool:
    """使用 pywxdump 相同的方法，手动解密 WeChat 数据库。

    key_hex: 64 字符 hex 字符串
    db_path: 加密数据库路径
    out_path: 输出解密后的 SQLite 文件路径
    """
    password = bytes.fromhex(key_hex.strip())

    with open(db_path, "rb") as f:
        blist = f.read()

    salt = blist[:16]
    if len(salt) != 16:
        return False

    # 派生加密密钥 (PBKDF2-HMAC-SHA1, 64000 轮)
    byte_key = hashlib.pbkdf2_hmac("sha1", password, salt, 64000, KEY_SIZE)

    # 派生 MAC 密钥
    mac_salt = bytes([(b ^ 58) for b in salt])
    mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)

    # 验证 HMAC（第一页）
    hash_mac = hmac.new(mac_key, blist[16:4064], hashlib.sha1)
    hash_mac.update(b'\x01\x00\x00\x00')

    expected_mac = first[-32:-12] if len((first := blist[16:4096])) >= 32 else b''
    if hash_mac.digest() != expected_mac:
        return False

    # AES-CBC 解密每一页
    with open(out_path, "wb") as out:
        out.write(SQLITE_FILE_HEADER.encode())
        for i in range(0, len(blist), 4096):
            page = blist[i:i + 4096]
            if i == 0:
                # 第一页：跳过前 16 字节的盐
                data = page[16:]
            else:
                data = page

            # CBC 解密，IV 在页末 -48 到 -32 位置
            iv = data[-48:-32] if len(data) >= 48 else b'\x00' * 16
            encrypted = data[:-48]
            decrypted = AES.new(byte_key, AES.MODE_CBC, iv).decrypt(encrypted)
            out.write(decrypted)
            out.write(data[-48:])  # 保留 HMAC+padding

    return True


@dataclass
class DecryptedDB:
    """解密后的数据库连接。"""
    original_path: str
    key_hex: str
    temp_path: str
    conn: sqlite3.Connection

    def execute(self, sql: str, params: tuple = ()) -> List[tuple]:
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()

    def close(self):
        self.conn.close()
        if os.path.exists(self.temp_path):
            try:
                os.unlink(self.temp_path)
            except Exception:
                pass


class MergedMsgDB:
    """管理多个分片 MSG 数据库，提供统一的查询接口。"""

    def __init__(self, dbs: List[DecryptedDB]):
        self._dbs = dbs

    def execute_all(self, sql: str, params: tuple = ()) -> List[tuple]:
        """在所有分片数据库上执行查询并合并结果。"""
        all_rows = []
        seen = set()
        for db in self._dbs:
            try:
                rows = db.execute(sql, params)
                for row in rows:
                    key = tuple(row) if isinstance(row, (list, tuple)) else (row,)
                    if key not in seen:
                        seen.add(key)
                        all_rows.append(row)
            except Exception:
                pass
        return all_rows

    def execute(self, sql: str, params: tuple = ()) -> List[tuple]:
        """别名，便于替换旧接口。"""
        return self.execute_all(sql, params)

    def close_all(self):
        for db in self._dbs:
            try:
                db.close()
            except Exception:
                pass

    @property
    def key_hex(self) -> str:
        return self._dbs[0].key_hex if self._dbs else ""

    @property
    def original_path(self) -> str:
        return self._dbs[0].original_path if self._dbs else ""


class WeChatDB:
    """微信数据库管理器：自动检测、提取密钥、解密、查询。"""

    def __init__(self):
        self._scanner = WeChatScanner()
        self._info = None
        self._key = None

    def scan_and_extract(self) -> Tuple[bool, str]:
        """扫描微信环境并提取密钥。返回 (成功, 消息)"""
        self._info = self._scanner.scan()
        errors = []

        if not self._info.pid:
            errors.append("微信未运行")
        if not self._info.data_dir:
            errors.append("未找到数据目录")
        if errors:
            return False, "；".join(errors)

        # 使用 pywxdump 提取密钥
        from pywxdump import get_wx_info
        wx_infos = get_wx_info()
        if not wx_infos or not wx_infos[0].get('key'):
            return False, "密钥提取失败"

        self._key = wx_infos[0]['key']
        return True, self._key

    def open_msg_db(self) -> Optional[DecryptedDB]:
        """解密并打开第一个 MSG 数据库（兼容旧接口）。"""
        all_dbs = self.open_all_msg_dbs()
        return all_dbs[0] if all_dbs else None

    def open_all_msg_dbs(self) -> List[DecryptedDB]:
        """解密并打开所有 MSG 分片数据库。返回列表。"""
        if not self._key or not self._info:
            return []

        msg_dir = self._info.data_dir
        if not msg_dir:
            return []

        # 查找所有 MSG*.db（Multi/ 目录下和 Msg/ 目录下）
        candidates = []
        multi_dir = os.path.join(msg_dir, "Multi")
        if os.path.isdir(multi_dir):
            for f in sorted(os.listdir(multi_dir)):
                if f.startswith("MSG") and f.endswith(".db"):
                    candidates.append(os.path.join(multi_dir, f))
        for f in sorted(os.listdir(msg_dir)):
            if f.startswith("MSG") and f.endswith(".db") and os.path.isfile(os.path.join(msg_dir, f)):
                candidates.append(os.path.join(msg_dir, f))

        if not candidates:
            return []

        dbs = []
        for db_path in candidates:
            temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
            os.close(temp_fd)

            try:
                success = decrypt_db_raw(self._key, db_path, temp_path)
                if not success:
                    os.unlink(temp_path)
                    continue
                conn = sqlite3.connect(temp_path)
                conn.row_factory = sqlite3.Row
                dbs.append(DecryptedDB(
                    original_path=db_path,
                    key_hex=self._key,
                    temp_path=temp_path,
                    conn=conn,
                ))
            except Exception:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

        return dbs

    def get_key(self) -> Optional[str]:
        return self._key

    def get_info(self):
        return self._info

import re
from typing import Optional
import pymem


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
            modules = list(self._pm.list_modules())
            if modules:
                base = modules[0].lpBaseOfDll
                return base > 0xFFFFFFFF
        except Exception:
            pass
        return True

    def _search_key(self, is_64bit: bool) -> Optional[str]:
        """在内存中搜索疑似数据库密钥的 64 位 hex 字符串。"""
        try:
            for module in self._pm.list_modules():
                if "WeChat" not in module.name and "wechat" not in module.name.lower():
                    continue
                try:
                    data = self._pm.read_bytes(module.lpBaseOfDll, module.SizeOfImage)
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

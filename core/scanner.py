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
            for entry in os.listdir(self._DATA_BASE):
                full_path = os.path.join(self._DATA_BASE, entry)
                if os.path.isdir(full_path):
                    db_files = glob.glob(os.path.join(full_path, "MSG*.db"))
                    if db_files:
                        info.data_dir = full_path
                        info.db_files = [os.path.basename(f) for f in db_files]
                        return
            subdirs = [d for d in os.listdir(self._DATA_BASE)
                       if os.path.isdir(os.path.join(self._DATA_BASE, d))]
            if subdirs:
                info.data_dir = os.path.join(self._DATA_BASE, subdirs[0])
        except Exception as e:
            info.errors.append(f"读取数据目录失败: {e}")

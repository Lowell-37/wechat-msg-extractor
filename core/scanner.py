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
    _PROCESS_NAMES = {"wechat.exe", "wechatappex.exe", "weixin.exe"}
    _INSTALL_BASES = [
        r"C:\Program Files\Tencent\WeChat",
        r"C:\Program Files (x86)\Tencent\WeChat",
        r"C:\Program Files\Tencent\Weixin",
        r"C:\Program Files (x86)\Tencent\Weixin",
        r"D:\software\WeChat",
    ]
    _DATA_BASES = [
        os.path.expandvars(r"%USERPROFILE%\Documents\xwechat_files"),
        os.path.expandvars(r"%USERPROFILE%\xwechat_files"),
        os.path.expandvars(r"%APPDATA%\Tencent\WeChat"),
        r"D:\WeChat Store\WeChat Files\WeChat Files",
        r"D:\WeChat Store\WeChat Files",
    ]
    _XWECHAT_CONFIG_DIR = os.path.expandvars(r"%APPDATA%\Tencent\xwechat\config")

    def __init__(self, install_path: Optional[str] = None, data_dir: Optional[str] = None):
        self._custom_install = install_path
        self._custom_data = data_dir

    def scan(self) -> WeChatInfo:
        info = WeChatInfo()
        self._detect_install(info)
        self._detect_process(info)
        self._detect_data_dir(info)
        return info

    def _detect_install(self, info: WeChatInfo):
        paths = [self._custom_install] if self._custom_install else []
        if not self._custom_install:
            paths = self._INSTALL_BASES

        for base in paths:
            if not base or not os.path.exists(base):
                continue
            try:
                versions = sorted(
                    [d for d in os.listdir(base)
                     if os.path.isdir(os.path.join(base, d))
                     and d.startswith("[") and d.endswith("]")],
                    reverse=True,
                )
                if versions:
                    info.version = versions[0].strip("[]")
                    info.install_path = os.path.join(base, versions[0])
                    return
                # also check if base itself is the versioned dir
                name = os.path.basename(base)
                if name.startswith("[") and name.endswith("]"):
                    info.version = name.strip("[]")
                    info.install_path = base
                    return
            except Exception as e:
                info.errors.append(f"读取安装目录失败: {e}")

        base = self._custom_install or self._INSTALL_BASES[0]
        info.errors.append(f"微信安装目录不存在: {base}")

    def _detect_process(self, info: WeChatInfo):
        try:
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                if (
                    proc.info["name"]
                    and proc.info["name"].lower() in self._PROCESS_NAMES
                ):
                    info.pid = proc.info["pid"]
                    info.exe_path = proc.info["exe"]
                    if not info.version and proc.info["exe"]:
                        exe_dir = os.path.dirname(proc.info["exe"])
                        info.install_path = exe_dir
                        parent = os.path.basename(exe_dir)
                        if parent.startswith("[") and parent.endswith("]"):
                            info.version = parent.strip("[]")
                        info.errors = [
                            error
                            for error in info.errors
                            if not error.startswith("微信安装目录不存在:")
                        ]
                    return
        except Exception as e:
            info.errors.append(f"查找微信进程失败: {e}")

    def _detect_data_dir(self, info: WeChatInfo):
        paths = [self._custom_data] if self._custom_data else []
        if not self._custom_data:
            paths = self._xwechat_data_bases(info) + list(self._DATA_BASES)

        for base in paths:
            if not base or not os.path.exists(base):
                continue
            try:
                for root, dirs, files in os.walk(base):
                    # Look for MSG*.db anywhere in subtree
                    db_files = sorted(f for f in files if f.startswith("MSG") and f.endswith(".db"))
                    if db_files:
                        # If found in Msg\Multi, prefer the parent Msg dir
                        parent_dir = os.path.dirname(root)
                        if os.path.basename(root) == "Multi" and os.path.isdir(parent_dir):
                            info.data_dir = parent_dir
                            # Copy MSG*.db paths to parent level info
                            info.db_files = [os.path.join("Multi", f) for f in db_files]
                        else:
                            info.data_dir = root
                            info.db_files = db_files
                        return
                    message_files = sorted(
                        f
                        for f in files
                        if f.startswith("message_") and f.endswith(".db")
                    )
                    if message_files:
                        info.data_dir = root
                        info.db_files = message_files
                        info.errors.append(
                            "检测到新版微信 message_*.db 数据；"
                            "当前版本暂不支持解密与导出"
                        )
                        return
            except Exception as e:
                info.errors.append(f"读取数据目录失败: {e}")

        base = self._custom_data or self._DATA_BASES[0]
        info.errors.append(f"微信数据目录不存在: {base}")

    def _xwechat_data_bases(self, info: WeChatInfo) -> list[str]:
        bases = []
        for config_path in sorted(
            glob.glob(os.path.join(self._XWECHAT_CONFIG_DIR, "*.ini"))
        ):
            try:
                with open(config_path, encoding="utf-8-sig") as config_file:
                    configured_root = config_file.read().strip().strip('"')
            except OSError as exc:
                info.errors.append(f"读取新版微信配置失败: {exc}")
                continue
            if not configured_root:
                continue
            if os.path.basename(os.path.normpath(configured_root)).lower() == "xwechat_files":
                candidates = [configured_root]
            else:
                candidates = [
                    os.path.join(configured_root, "xwechat_files"),
                    configured_root,
                ]
            for candidate in candidates:
                normalized = os.path.normcase(os.path.normpath(candidate))
                if all(
                    os.path.normcase(os.path.normpath(base)) != normalized
                    for base in bases
                ):
                    bases.append(candidate)
        return bases

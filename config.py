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

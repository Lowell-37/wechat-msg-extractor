from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum, StrEnum
from typing import Any


class WizardStep(IntEnum):
    CONNECT = 1
    SELECT = 2
    PREVIEW = 3


class StepStatus(StrEnum):
    LOCKED = "locked"
    AVAILABLE = "available"
    ACTIVE = "active"
    COMPLETED = "completed"


@dataclass
class WizardSelection:
    group_id: str = ""
    group_name: str = ""
    start_date: date | None = None
    end_date: date | None = None
    sheet_name: str = ""


@dataclass
class WizardState:
    active_step: WizardStep = WizardStep.CONNECT
    connected: bool = False
    selection: WizardSelection = field(default_factory=WizardSelection)
    preview_tasks: list[Any] = field(default_factory=list)
    selected_task_ids: list[str] = field(default_factory=list)
    preview_ready: bool = False
    output_path: str = ""
    enable_ai: bool = False
    enable_voice: bool = False
    privacy_acknowledged: bool = False

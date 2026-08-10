from collections.abc import MutableMapping, Sequence
from datetime import date
from typing import Any

from schemas.wizard import StepStatus, WizardSelection, WizardState, WizardStep


def get_wizard(state: MutableMapping[str, Any]) -> WizardState:
    """Return the typed wizard state stored in a session dictionary."""
    wizard = state.get("wizard")
    if isinstance(wizard, WizardState):
        return wizard

    wizard = WizardState()
    state["wizard"] = wizard
    return wizard


def request_step(wizard: WizardState, requested: WizardStep) -> WizardStep:
    """Activate a requested step or the furthest step currently allowed."""
    active_step = min(requested, _latest_available_step(wizard))
    wizard.active_step = active_step
    return active_step


def mark_connected(wizard: WizardState) -> None:
    """Record a successful connection without advancing the visible step."""
    wizard.connected = True


def update_selection(
    wizard: WizardState,
    group_id: str,
    group_name: str,
    start_date: date,
    end_date: date,
    sheet_name: str,
) -> None:
    """Store a new selection and discard only derived preview/export state."""
    selection = WizardSelection(group_id, group_name, start_date, end_date, sheet_name)
    if wizard.selection == selection:
        return

    wizard.selection = selection
    wizard.preview_tasks.clear()
    wizard.selected_task_ids.clear()
    wizard.preview_ready = False
    wizard.output_path = ""
    wizard.enable_ai = False
    wizard.enable_voice = False
    wizard.privacy_acknowledged = False
    if wizard.active_step > WizardStep.SELECT:
        wizard.active_step = WizardStep.SELECT


def update_session_selection(
    state: MutableMapping[str, Any],
    group_id: str,
    group_name: str,
    start_date: date,
    end_date: date,
    sheet_name: str,
) -> bool:
    """Store selection compatibility state and invalidate derived data together."""
    wizard = get_wizard(state)
    changed = wizard.selection != WizardSelection(
        group_id, group_name, start_date, end_date, sheet_name
    )
    update_selection(
        wizard, group_id, group_name, start_date, end_date, sheet_name
    )
    state["selected_group"] = group_id
    state["selected_sheet"] = sheet_name
    state["start_date"] = start_date.isoformat()
    state["end_date"] = end_date.isoformat()
    if changed:
        state["parsed_tasks"] = []
        state["analysis_by_date"] = {}
    return changed


def store_preview(wizard: WizardState, tasks: Sequence[Any]) -> None:
    """Persist a generated preview without automatically advancing the wizard."""
    if not wizard.connected or not has_complete_selection(wizard.selection):
        wizard.preview_tasks.clear()
        wizard.selected_task_ids.clear()
        wizard.preview_ready = False
        return

    wizard.preview_tasks = list(tasks)
    wizard.selected_task_ids = [
        str(getattr(task, "msg_id", index)) for index, task in enumerate(tasks)
    ]
    wizard.preview_ready = True


def store_export_preferences(
    wizard: WizardState,
    *,
    selected_task_ids: Sequence[str],
    output_path: str,
    enable_ai: bool,
    enable_voice: bool,
    privacy_acknowledged: bool,
) -> None:
    """Persist retryable export input without changing preview availability."""
    wizard.selected_task_ids = list(dict.fromkeys(selected_task_ids))
    wizard.output_path = output_path
    wizard.enable_ai = enable_ai
    wizard.enable_voice = enable_voice
    wizard.privacy_acknowledged = privacy_acknowledged


def step_statuses(wizard: WizardState) -> dict[WizardStep, StepStatus]:
    """Describe lock, availability, and completion for all visible steps."""
    latest_available = _latest_available_step(wizard)
    return {
        step: _step_status(step, wizard.active_step, latest_available)
        for step in WizardStep
    }


def _latest_available_step(wizard: WizardState) -> WizardStep:
    if (
        wizard.connected
        and has_complete_selection(wizard.selection)
        and wizard.preview_ready
    ):
        return WizardStep.PREVIEW
    if wizard.connected:
        return WizardStep.SELECT
    return WizardStep.CONNECT


def has_complete_selection(selection: WizardSelection) -> bool:
    """Return whether a selection is complete and chronologically valid."""
    return bool(
        selection.group_id
        and selection.group_name
        and selection.start_date
        and selection.end_date
        and selection.start_date <= selection.end_date
        and selection.sheet_name
    )


def _step_status(
    step: WizardStep, active_step: WizardStep, latest_available: WizardStep
) -> StepStatus:
    if step == active_step:
        return StepStatus.ACTIVE
    if step < active_step:
        return StepStatus.COMPLETED
    if step <= latest_available:
        return StepStatus.AVAILABLE
    return StepStatus.LOCKED

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
    wizard.preview_ready = False
    wizard.output_path = ""
    wizard.enable_ai = False
    wizard.enable_voice = False
    wizard.privacy_acknowledged = False
    if wizard.active_step > WizardStep.SELECT:
        wizard.active_step = WizardStep.SELECT


def store_preview(wizard: WizardState, tasks: Sequence[Any]) -> None:
    """Persist a generated preview without automatically advancing the wizard."""
    if not wizard.connected or not _has_complete_selection(wizard.selection):
        wizard.preview_tasks.clear()
        wizard.preview_ready = False
        return

    wizard.preview_tasks = list(tasks)
    wizard.preview_ready = True


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
        and _has_complete_selection(wizard.selection)
        and wizard.preview_ready
    ):
        return WizardStep.PREVIEW
    if wizard.connected:
        return WizardStep.SELECT
    return WizardStep.CONNECT


def _has_complete_selection(selection: WizardSelection) -> bool:
    return bool(
        selection.group_id
        and selection.group_name
        and selection.start_date
        and selection.end_date
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

from datetime import date

import app as app_module
from schemas.wizard import StepStatus, WizardState, WizardStep
from services.wizard import (
    get_wizard,
    mark_connected,
    request_step,
    step_statuses,
    store_preview,
    update_selection,
)


def test_future_step_is_redirected_to_latest_available_step():
    wizard = WizardState()

    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.CONNECT
    assert wizard.active_step is WizardStep.CONNECT


def test_unchanged_selection_preserves_preview_but_changed_date_invalidates_it():
    wizard = WizardState()
    mark_connected(wizard)
    update_selection(
        wizard,
        "room-1",
        "项目群",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "项目群",
    )
    store_preview(wizard, [object()])

    update_selection(
        wizard,
        "room-1",
        "项目群",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "项目群",
    )
    assert wizard.preview_ready is True

    update_selection(
        wizard,
        "room-1",
        "项目群",
        date(2026, 8, 1),
        date(2026, 8, 3),
        "项目群",
    )
    assert wizard.preview_ready is False
    assert wizard.preview_tasks == []
    assert wizard.output_path == ""
    assert wizard.enable_ai is False
    assert wizard.enable_voice is False
    assert wizard.privacy_acknowledged is False


def test_connected_wizard_redirects_to_select_until_preview_is_ready():
    wizard = WizardState()
    mark_connected(wizard)

    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.SELECT

    update_selection(
        wizard,
        "room-1",
        "项目群",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "项目群",
    )
    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.SELECT

    store_preview(wizard, ["task"])
    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.PREVIEW


def test_step_statuses_show_available_steps_without_auto_advancing():
    wizard = WizardState()
    mark_connected(wizard)

    assert wizard.active_step is WizardStep.CONNECT
    assert step_statuses(wizard) == {
        WizardStep.CONNECT: StepStatus.ACTIVE,
        WizardStep.SELECT: StepStatus.AVAILABLE,
        WizardStep.PREVIEW: StepStatus.LOCKED,
    }

    request_step(wizard, WizardStep.SELECT)
    assert step_statuses(wizard)[WizardStep.CONNECT] is StepStatus.COMPLETED
    assert step_statuses(wizard)[WizardStep.SELECT] is StepStatus.ACTIVE


def test_get_wizard_creates_and_reuses_typed_session_wizard():
    state = {}

    wizard = get_wizard(state)

    assert isinstance(wizard, WizardState)
    assert state["wizard"] is wizard
    assert get_wizard(state) is wizard


def test_new_session_state_retains_resources_and_stores_wizard():
    state = app_module._new_session_state(now=123.0)

    assert isinstance(state["wizard"], WizardState)
    assert state["wdb"] is None
    assert state["ddb"] is None
    assert state["last_access"] == 123.0


def test_store_preview_cannot_unlock_preview_without_connection_and_selection():
    wizard = WizardState()

    store_preview(wizard, ["task"])

    assert wizard.preview_ready is False
    assert wizard.preview_tasks == []
    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.CONNECT


def test_stale_preview_flag_cannot_unlock_preview_without_prerequisites():
    wizard = WizardState(preview_ready=True)

    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.CONNECT

    mark_connected(wizard)
    assert request_step(wizard, WizardStep.PREVIEW) is WizardStep.SELECT


def test_changed_group_invalidates_preview_but_unchanged_selection_preserves_it():
    wizard = WizardState()
    mark_connected(wizard)
    update_selection(
        wizard,
        "room-1",
        "Project group",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "Project group",
    )
    store_preview(wizard, ["task"])

    update_selection(
        wizard,
        "room-1",
        "Project group",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "Project group",
    )
    assert wizard.preview_ready is True

    update_selection(
        wizard,
        "room-2",
        "Operations group",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "Project group",
    )
    assert wizard.preview_ready is False
    assert wizard.preview_tasks == []


def test_changed_sheet_invalidates_preview_but_unchanged_selection_preserves_it():
    wizard = WizardState()
    mark_connected(wizard)
    update_selection(
        wizard,
        "room-1",
        "Project group",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "Project group",
    )
    store_preview(wizard, ["task"])

    update_selection(
        wizard,
        "room-1",
        "Project group",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "Project group",
    )
    assert wizard.preview_ready is True

    update_selection(
        wizard,
        "room-1",
        "Project group",
        date(2026, 8, 1),
        date(2026, 8, 2),
        "Operations group",
    )
    assert wizard.preview_ready is False
    assert wizard.preview_tasks == []

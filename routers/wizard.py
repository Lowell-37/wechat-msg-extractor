from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from schemas.wizard import WizardStep
from services.session import get_session
from services.wizard import get_wizard, request_step, step_statuses

router = APIRouter()

_STEP_TEMPLATES = {
    WizardStep.CONNECT: "step1_connect.html",
    WizardStep.SELECT: "step2_select.html",
    WizardStep.PREVIEW: "step3_preview.html",
}


@router.get("/")
async def index(request: Request):
    return _render_wizard_step(request, WizardStep.CONNECT, partial=False)


@router.get("/wizard/{step}")
async def wizard_step(request: Request, step: int):
    requested = _parse_step(step)
    return _render_wizard_step(request, requested, partial=False)


@router.get("/wizard/{step}/partial")
async def wizard_step_partial(request: Request, step: int):
    requested = _parse_step(step)
    return _render_wizard_step(request, requested, partial=True)


@router.get("/step/2")
async def legacy_step_two():
    return RedirectResponse("/wizard/2", status_code=303)


@router.get("/step/3")
async def legacy_step_three():
    return RedirectResponse("/wizard/3", status_code=303)


def _parse_step(step: int) -> WizardStep:
    try:
        return WizardStep(step)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Wizard step not found") from exc


def _render_wizard_step(
    request: Request,
    requested: WizardStep,
    *,
    partial: bool,
):
    session_id, state = get_session(request)
    wizard = get_wizard(state)
    accessible = request_step(wizard, requested)
    if accessible is not requested:
        response = RedirectResponse(
            f"/wizard/{int(accessible)}", status_code=303
        )
        return _with_session_cookie(request, response, session_id)

    today = date.today()  # noqa: DTZ011
    context = {
        "step": int(accessible),
        "wizard": wizard,
        "step_statuses": step_statuses(wizard),
        "start_date": state.get("start_date")
        or (today - timedelta(days=30)).isoformat(),
        "end_date": state.get("end_date") or today.isoformat(),
        "layout_template": "wizard_fragment.html" if partial else "base.html",
    }
    response = request.app.state.templates.TemplateResponse(
        request,
        _STEP_TEMPLATES[accessible],
        context,
    )
    return _with_session_cookie(request, response, session_id)


def _with_session_cookie(request: Request, response, session_id: str):
    if request.cookies.get("session_id") != session_id:
        response.set_cookie(key="session_id", value=session_id)
    return response

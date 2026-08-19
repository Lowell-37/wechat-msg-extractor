"""Routes for configuring an OpenAI-compatible model service."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request

from services.model_settings import (
    ModelConnectionError,
    ModelSettingsError,
    ModelValidationError,
)
from services.session import get_session

router = APIRouter()


@router.get("/settings/model")
async def model_settings_page(request: Request):
    session_id, state = get_session(request)
    csrf_token = state.setdefault(
        "model_settings_csrf", secrets.token_urlsafe(32)
    )
    context = _settings_context(request, csrf_token=csrf_token)
    response = request.app.state.templates.TemplateResponse(
        request,
        "model_settings.html",
        context,
    )
    return _with_session_cookie(request, response, session_id)


@router.post("/settings/model")
async def save_model_settings(
    request: Request,
    provider_name: Annotated[str, Form()] = "",
    api_base: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    enabled: Annotated[str, Form()] = "",
    api_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
):
    session_id, state = get_session(request)
    values = {
        "provider_name": provider_name,
        "api_base": api_base,
        "model": model,
        "enabled": enabled.lower() in {"1", "true", "yes", "on"},
    }
    message = ""
    tone = ""
    status_code = 200
    expected_csrf = state.get("model_settings_csrf", "")
    if not _same_origin(request) or not (
        expected_csrf
        and csrf_token
        and secrets.compare_digest(expected_csrf, csrf_token)
    ):
        message = "页面验证已失效，请刷新后重试。"
        tone = "error"
        status_code = 403
    else:
        try:
            status = await request.app.state.model_settings.save_and_test(
                **values,
                api_key=api_key,
            )
            if status.profile.enabled:
                message = "连接测试通过，模型设置已保存。"
                tone = "success"
            else:
                message = "AI 分析已停用。"
                tone = "neutral"
            values = None
        except ModelValidationError as exc:
            message = str(exc)
            tone = "error"
            status_code = 422
        except ModelConnectionError as exc:
            message = str(exc)
            tone = "error"
            status_code = 400
        except ModelSettingsError:
            message = "无法保存模型设置，请稍后重试。"
            tone = "error"
            status_code = 500
        finally:
            api_key = ""

    context = _settings_context(
        request,
        values=values,
        message=message,
        tone=tone,
        csrf_token=state.setdefault(
            "model_settings_csrf", secrets.token_urlsafe(32)
        ),
    )
    partial = request.headers.get("HX-Request", "").lower() == "true"
    response = request.app.state.templates.TemplateResponse(
        request,
        "components/model_settings_form.html"
        if partial
        else "model_settings.html",
        context,
        status_code=status_code,
    )
    return _with_session_cookie(request, response, session_id)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    return bool(origin) and secrets.compare_digest(origin.rstrip("/"), expected)


def _with_session_cookie(request: Request, response: Any, session_id: str):
    if request.cookies.get("session_id") != session_id:
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            samesite="lax",
        )
    return response


def _settings_context(
    request: Request,
    *,
    values: dict[str, Any] | None = None,
    message: str = "",
    tone: str = "",
    csrf_token: str = "",
) -> dict[str, Any]:
    status = request.app.state.model_settings.status()
    profile = status.profile
    form = values or {
        "provider_name": profile.provider_name,
        "api_base": profile.api_base,
        "model": profile.model,
        "enabled": profile.enabled,
    }
    if not profile.enabled:
        state_label = "已停用"
    elif status.ready:
        state_label = "已验证"
    elif not status.has_api_key:
        state_label = "未配置 API Key"
    else:
        state_label = "尚未通过连接测试"
    return {
        **form,
        "has_api_key": status.has_api_key,
        "ready": status.ready,
        "state_label": state_label,
        "message": message,
        "tone": tone,
        "csrf_token": csrf_token,
    }

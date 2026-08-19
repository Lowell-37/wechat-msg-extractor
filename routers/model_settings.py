"""Routes for configuring an OpenAI-compatible model service."""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request

from services.model_settings import (
    ModelConnectionError,
    ModelSettingsError,
    ModelValidationError,
)

router = APIRouter()


@router.get("/settings/model")
async def model_settings_page(request: Request):
    context = _settings_context(request)
    return request.app.state.templates.TemplateResponse(
        request,
        "model_settings.html",
        context,
    )


@router.post("/settings/model")
async def save_model_settings(
    request: Request,
    provider_name: Annotated[str, Form()] = "",
    api_base: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    enabled: Annotated[str, Form()] = "",
    api_key: Annotated[str, Form()] = "",
):
    values = {
        "provider_name": provider_name,
        "api_base": api_base,
        "model": model,
        "enabled": enabled.lower() in {"1", "true", "yes", "on"},
    }
    message = ""
    tone = ""
    status_code = 200
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
    )
    partial = request.headers.get("HX-Request", "").lower() == "true"
    return request.app.state.templates.TemplateResponse(
        request,
        "components/model_settings_form.html"
        if partial
        else "model_settings.html",
        context,
        status_code=status_code,
    )


def _settings_context(
    request: Request,
    *,
    values: dict[str, Any] | None = None,
    message: str = "",
    tone: str = "",
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
    }

"""Persistent, secret-safe settings for OpenAI-compatible model services."""

import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from services.credential_store import CredentialStore, CredentialStoreError


class ModelSettingsError(RuntimeError):
    """Raised when model settings cannot be validated or persisted safely."""


@dataclass(frozen=True)
class ModelProfile:
    provider_name: str = "DeepSeek"
    api_base: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    enabled: bool = True
    verified: bool = False


@dataclass(frozen=True)
class ResolvedModelProfile:
    profile: ModelProfile
    api_key: str


@dataclass(frozen=True)
class ModelSettingsStatus:
    profile: ModelProfile
    has_api_key: bool
    ready: bool


class ModelSettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def default(cls) -> "ModelSettingsStore":
        base_dir = Path(__file__).resolve().parents[1]
        return cls(base_dir / "local" / "settings" / "model.json")

    def load(self) -> ModelProfile:
        if not self.path.exists():
            return ModelProfile()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return _profile_from_data(data)
        except Exception as exc:
            raise ModelSettingsError("无法读取已保存的模型设置") from exc

    def save(self, profile: ModelProfile) -> None:
        payload = json.dumps(
            asdict(profile),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        try:
            _atomic_write(self.path, payload)
        except Exception as exc:
            raise ModelSettingsError("无法保存模型设置") from exc

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise ModelSettingsError("无法恢复模型设置") from exc


class ModelSettingsService:
    def __init__(
        self,
        store: ModelSettingsStore,
        credential_store: CredentialStore,
        tester: Callable[[ResolvedModelProfile], Awaitable[None]],
    ):
        self.store = store
        self.credential_store = credential_store
        self.tester = tester

    @classmethod
    def default(
        cls,
        tester: Callable[[ResolvedModelProfile], Awaitable[None]],
    ) -> "ModelSettingsService":
        return cls(
            ModelSettingsStore.default(),
            CredentialStore.model_api_key(),
            tester,
        )

    def status(self) -> ModelSettingsStatus:
        profile = self.store.load()
        has_api_key = self.credential_store.path.is_file()
        return ModelSettingsStatus(
            profile=profile,
            has_api_key=has_api_key,
            ready=profile.enabled and profile.verified and has_api_key,
        )

    def resolved_profile(self) -> ResolvedModelProfile:
        status = self.status()
        if not status.ready:
            raise ModelSettingsError("模型尚未启用并通过连接测试")
        try:
            api_key = self.credential_store.load()
        except CredentialStoreError as exc:
            raise ModelSettingsError("无法读取已保存的模型 API Key") from exc
        if not api_key:
            raise ModelSettingsError("请填写 API Key")
        return ResolvedModelProfile(status.profile, api_key)

    async def save_and_test(
        self,
        provider_name: str,
        api_base: str,
        model: str,
        enabled: bool,
        api_key: str = "",
    ) -> ModelSettingsStatus:
        proposed = _validated_profile(
            provider_name,
            api_base,
            model,
            enabled,
        )
        if not enabled:
            self.store.save(proposed)
            return self.status()

        replacement_key = api_key.strip()
        try:
            resolved_key = replacement_key or self.credential_store.load()
        except CredentialStoreError as exc:
            raise ModelSettingsError("无法读取已保存的模型 API Key") from exc
        if not resolved_key:
            raise ModelSettingsError("请填写 API Key")

        try:
            await self.tester(ResolvedModelProfile(proposed, resolved_key))
        except Exception as exc:
            raise ModelSettingsError("模型连接测试失败，请检查地址、模型和 API Key") from exc

        self._commit_verified(proposed, replacement_key)
        return self.status()

    def _commit_verified(
        self,
        proposed: ModelProfile,
        replacement_key: str,
    ) -> None:
        settings_existed = self.store.path.exists()
        previous_profile = self.store.load()
        try:
            previous_key = self.credential_store.load()
        except CredentialStoreError as exc:
            raise ModelSettingsError("无法读取已保存的模型 API Key") from exc

        try:
            if replacement_key:
                self.credential_store.save(replacement_key)
            self.store.save(replace(proposed, verified=True))
        except Exception as exc:
            try:
                if replacement_key:
                    if previous_key:
                        self.credential_store.save(previous_key)
                    else:
                        self.credential_store.delete()
                if settings_existed:
                    self.store.save(previous_profile)
                else:
                    self.store.delete()
            except (CredentialStoreError, ModelSettingsError) as rollback_exc:
                raise ModelSettingsError("无法恢复之前的模型设置") from rollback_exc
            raise ModelSettingsError("无法保存已验证的模型设置") from exc


def _validated_profile(
    provider_name: str,
    api_base: str,
    model: str,
    enabled: bool,
) -> ModelProfile:
    normalized_provider = provider_name.strip()
    normalized_model = model.strip()
    normalized_base = api_base.strip().rstrip("/")
    if not normalized_provider:
        raise ModelSettingsError("提供商名称不能为空")
    if not normalized_model:
        raise ModelSettingsError("模型名称不能为空")

    parsed = urlparse(normalized_base)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if parsed.scheme != "https" and not is_loopback_http:
        raise ModelSettingsError("远程模型地址必须使用 HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ModelSettingsError("模型 API 地址格式无效")
    if parsed.query or parsed.fragment:
        raise ModelSettingsError("模型 API 地址不能包含查询参数或片段")

    return ModelProfile(
        provider_name=normalized_provider,
        api_base=normalized_base,
        model=normalized_model,
        enabled=bool(enabled),
        verified=False,
    )


def _profile_from_data(data: object) -> ModelProfile:
    if not isinstance(data, dict):
        raise TypeError("model settings must be an object")
    return ModelProfile(**data)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="model-settings-",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

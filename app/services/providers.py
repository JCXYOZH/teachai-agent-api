import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import DecryptionError, decrypt_secret, encrypt_secret
from app.core.errors import AppError
from app.models.provider_config import ProviderConfig
from app.models.user import User
from app.schemas.provider import (
    ProviderConfigUpdate,
    ProviderTestRequest,
    ProviderTestResponse,
    ProviderView,
)
from app.services.provider_registry import (
    EDGE_TTS_SPEC,
    PROVIDER_SPECS,
    ProviderSpec,
    get_provider_spec,
)
from app.services.url_guard import validate_provider_base_url


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    message: str
    models: List[str]
    model: Optional[str] = None


@dataclass
class ResolvedProvider:
    spec: ProviderSpec
    config: Optional[ProviderConfig]
    api_key: Optional[str]
    api_key_source: Optional[str]
    base_url: str
    default_model: str
    tts_base_url: Optional[str] = None
    tts_model: Optional[str] = None
    tts_voice: Optional[str] = None


def _secret_hint(secret: Optional[str]) -> Optional[str]:
    if not secret or len(secret) < 8:
        return None
    return f"****{secret[-4:]}"


def _secret_last4(secret: str) -> Optional[str]:
    return secret[-4:] if len(secret) >= 8 else None


def _qwen_tts_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    path = (parsed.path or '').rstrip('/')
    suffix = '/compatible-mode/v1'
    if path.endswith(suffix):
        path = path[: -len(suffix)] + '/api/v1'
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def _response_error(response: httpx.Response) -> str:
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or "")
            if not message:
                message = str(payload.get("message") or payload.get("detail") or "")
    except ValueError:
        message = response.text
    normalized = " ".join(message.split())
    return normalized[:240] or f"Provider returned HTTP {response.status_code}"


async def _probe_openai(
    base_url: str,
    api_key: Optional[str],
    model: str,
    timeout: float,
    *,
    text_only: bool = False,
) -> ProbeResult:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            names: List[str] = []
            try:
                models_response = await client.get(
                    f"{base_url}/models",
                    headers=headers,
                    timeout=min(timeout, 5.0),
                )
            except httpx.HTTPError:
                models_response = None
            if models_response is not None and models_response.status_code == 200:
                try:
                    payload = models_response.json()
                    items = payload.get("data", []) if isinstance(payload, dict) else []
                    names = [
                        str(item["id"])
                        for item in items
                        if isinstance(item, dict) and item.get("id")
                    ]
                except (ValueError, TypeError):
                    names = []

            request_payload: Dict[str, object] = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "max_tokens": 8,
                "temperature": 0,
            }
            if text_only:
                request_payload["modalities"] = ["text"]
            chat_response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=request_payload,
            )
            if chat_response.status_code == 200:
                actual_model = model
                try:
                    payload = chat_response.json()
                    if isinstance(payload, dict):
                        reported_model = payload.get("model")
                        if isinstance(reported_model, str) and reported_model.strip():
                            actual_model = reported_model.strip()
                except ValueError:
                    pass
                detail = f"Chat completion succeeded with {actual_model}."
                if names:
                    detail = f"{detail} {len(names)} models reported."
                return ProbeResult(True, detail, names, actual_model)
            if chat_response.status_code in {401, 403}:
                return ProbeResult(False, "Authentication failed (HTTP 401/403).", [])
            return ProbeResult(False, _response_error(chat_response), [])
    except httpx.TimeoutException:
        return ProbeResult(False, "Connection timed out.", [])
    except httpx.HTTPError:
        return ProbeResult(False, "Provider could not be reached.", [])


async def _probe_anthropic(
    base_url: str,
    api_key: Optional[str],
    model: str,
    timeout: float,
) -> ProbeResult:
    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{base_url}/messages",
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply with OK"}],
                },
            )
        if response.status_code == 200:
            return ProbeResult(True, "Connected. Messages API succeeded.", [])
        if response.status_code in {401, 403}:
            return ProbeResult(False, "Authentication failed (HTTP 401/403).", [])
        return ProbeResult(False, _response_error(response), [])
    except httpx.TimeoutException:
        return ProbeResult(False, "Connection timed out.", [])
    except httpx.HTTPError:
        return ProbeResult(False, "Provider could not be reached.", [])


class ProviderConfigService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def list_providers(self, owner: User) -> List[ProviderView]:
        configs = await self._configs_by_provider(owner.id)
        return [
            self._provider_view(spec, configs.get(spec.id))
            for spec in PROVIDER_SPECS.values()
        ]

    async def get_provider(self, owner: User, provider_id: str) -> ProviderView:
        spec = self._spec(provider_id)
        config = await self._get_config(owner.id, provider_id)
        return self._provider_view(spec, config)

    async def update_config(
        self,
        owner: User,
        provider_id: str,
        payload: ProviderConfigUpdate,
    ) -> ProviderView:
        spec = self._spec(provider_id)
        config = await self._get_config(owner.id, provider_id)
        is_new = config is None
        if config is None:
            config = ProviderConfig(owner_id=owner.id, provider_id=provider_id)
            self.db.add(config)

        if payload.clear_api_key:
            config.encrypted_api_key = None
            config.api_key_last4 = None
        elif payload.api_key is not None:
            config.encrypted_api_key = encrypt_secret(payload.api_key, self.settings)
            config.api_key_last4 = _secret_last4(payload.api_key)

        if "base_url" in payload.model_fields_set:
            if payload.base_url:
                config.base_url = await validate_provider_base_url(
                    payload.base_url, spec, self.settings
                )
            else:
                config.base_url = spec.default_base_url
        elif config.base_url is None:
            config.base_url = self._environment_value(spec.env_base_url_setting) or spec.default_base_url

        if "default_model" in payload.model_fields_set:
            config.default_model = payload.default_model or self._default_model(spec)
        elif config.default_model is None:
            config.default_model = self._default_model(spec)

        if payload.clear_tts:
            config.tts_base_url = None
            config.tts_model = None
            config.tts_voice = None
        else:
            if "tts_base_url" in payload.model_fields_set:
                if payload.tts_base_url:
                    config.tts_base_url = await validate_provider_base_url(
                        payload.tts_base_url, spec, self.settings
                    )
                else:
                    config.tts_base_url = None
            if "tts_model" in payload.model_fields_set:
                config.tts_model = payload.tts_model or None
            if "tts_voice" in payload.model_fields_set:
                config.tts_voice = payload.tts_voice or None

        if payload.is_default is True:
            await self.db.execute(
                update(ProviderConfig)
                .where(
                    ProviderConfig.owner_id == owner.id,
                    ProviderConfig.provider_id != provider_id,
                )
                .values(is_default=False)
            )
            config.is_default = True
        elif payload.is_default is False:
            config.is_default = False
        elif is_new:
            existing_default = await self.db.scalar(
                select(ProviderConfig.id)
                .where(
                    ProviderConfig.owner_id == owner.id,
                    ProviderConfig.is_default.is_(True),
                )
                .limit(1)
            )
            config.is_default = existing_default is None

        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(
                409,
                "PROVIDER_CONFIG_CONFLICT",
                "Provider configuration could not be saved",
            ) from exc
        await self.db.refresh(config)
        return self._provider_view(spec, config)

    async def delete_config(self, owner: User, provider_id: str) -> None:
        self._spec(provider_id)
        config = await self._get_config(owner.id, provider_id)
        if config is None:
            raise AppError(404, "PROVIDER_CONFIG_NOT_FOUND", "Provider config not found")
        was_default = config.is_default
        await self.db.delete(config)
        await self.db.flush()
        if was_default:
            replacement = await self.db.scalar(
                select(ProviderConfig)
                .where(ProviderConfig.owner_id == owner.id)
                .order_by(ProviderConfig.created_at, ProviderConfig.id)
                .limit(1)
            )
            if replacement is not None:
                replacement.is_default = True
        await self.db.commit()

    async def test_connection(
        self,
        owner: User,
        provider_id: str,
        override: Optional[ProviderTestRequest] = None,
    ) -> ProviderTestResponse:
        spec = self._spec(provider_id)
        config = await self._get_config(owner.id, provider_id)
        resolved = self._resolve(spec, config)
        if override is not None:
            if override.api_key is not None:
                resolved.api_key = override.api_key
                resolved.api_key_source = "user"
            if "base_url" in override.model_fields_set:
                if override.base_url is not None:
                    resolved.base_url = await validate_provider_base_url(
                        override.base_url, spec, self.settings
                    )
                else:
                    resolved.base_url = spec.default_base_url
            if override.default_model is not None:
                resolved.default_model = override.default_model

        resolved.base_url = await validate_provider_base_url(
            resolved.base_url, spec, self.settings
        )
        if spec.requires_api_key and not resolved.api_key:
            raise AppError(
                422,
                "PROVIDER_API_KEY_REQUIRED",
                "This provider requires an API key",
            )

        started_at = time.perf_counter()
        if spec.protocol == "anthropic":
            result = await _probe_anthropic(
                resolved.base_url,
                resolved.api_key,
                resolved.default_model,
                self.settings.provider_test_timeout_seconds,
            )
        else:
            probe_options: Dict[str, bool] = {}
            if spec.id == "qwen" and "omni" in resolved.default_model.lower():
                probe_options["text_only"] = True
            result = await _probe_openai(
                resolved.base_url,
                resolved.api_key,
                resolved.default_model,
                self.settings.provider_test_timeout_seconds,
                **probe_options,
            )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return ProviderTestResponse(
            provider_id=provider_id,
            success=result.success,
            message=result.message,
            latency_ms=latency_ms,
            model=result.model or resolved.default_model,
            models=result.models[:50],
        )

    async def resolve_for_generation(
        self, owner: User, provider_id: Optional[str] = None
    ) -> ResolvedProvider:
        selected_id = provider_id
        if selected_id is None:
            default_config = await self.db.scalar(
                select(ProviderConfig)
                .where(
                    ProviderConfig.owner_id == owner.id,
                    ProviderConfig.is_default.is_(True),
                )
                .limit(1)
            )
            selected_id = default_config.provider_id if default_config else "llama-local"
        spec = self._spec(selected_id)
        config = await self._get_config(owner.id, selected_id)
        resolved = self._resolve(spec, config)
        resolved.base_url = await validate_provider_base_url(
            resolved.base_url, spec, self.settings
        )
        if spec.requires_api_key and not resolved.api_key:
            raise AppError(
                422,
                "PROVIDER_API_KEY_REQUIRED",
                "This provider requires an API key",
            )
        return resolved

    async def resolve_tts_provider(
        self,
        owner: User,
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> ResolvedProvider:
        selected_id = provider_id or self.settings.tts_provider_id
        if selected_id == EDGE_TTS_SPEC.id:
            selected_voice = voice or EDGE_TTS_SPEC.tts_default_voice
            if selected_voice not in EDGE_TTS_SPEC.tts_voices:
                raise AppError(
                    422,
                    "TTS_VOICE_NOT_SUPPORTED",
                    f"{selected_voice} is not a supported Edge TTS voice",
                )
            return ResolvedProvider(
                spec=EDGE_TTS_SPEC,
                config=None,
                api_key=None,
                api_key_source=None,
                base_url="",
                default_model=EDGE_TTS_SPEC.tts_default_model or "edge-tts",
                tts_base_url=None,
                tts_model=EDGE_TTS_SPEC.tts_default_model,
                tts_voice=selected_voice,
            )
        spec = self._spec(selected_id)
        if not spec.supports_tts:
            raise AppError(
                422,
                "TTS_NOT_SUPPORTED",
                f"{spec.label} does not provide speech synthesis",
            )
        config = await self._get_config(owner.id, selected_id)
        resolved = self._resolve(spec, config)
        resolved.tts_base_url = await validate_provider_base_url(
            resolved.tts_base_url or resolved.base_url, spec, self.settings
        )
        if model:
            resolved.tts_model = model
        if voice:
            resolved.tts_voice = voice
        if spec.requires_api_key and not resolved.api_key:
            raise AppError(
                422,
                "PROVIDER_API_KEY_REQUIRED",
                "This provider requires an API key",
            )
        return resolved

    async def _configs_by_provider(self, owner_id: UUID) -> Dict[str, ProviderConfig]:
        result = await self.db.execute(
            select(ProviderConfig).where(ProviderConfig.owner_id == owner_id)
        )
        return {config.provider_id: config for config in result.scalars().all()}

    async def _get_config(
        self, owner_id: UUID, provider_id: str
    ) -> Optional[ProviderConfig]:
        return await self.db.scalar(
            select(ProviderConfig).where(
                ProviderConfig.owner_id == owner_id,
                ProviderConfig.provider_id == provider_id,
            )
        )

    def _provider_view(
        self, spec: ProviderSpec, config: Optional[ProviderConfig]
    ) -> ProviderView:
        resolved = self._resolve(spec, config)
        return ProviderView(
            id=spec.id,
            label=spec.label,
            description=spec.description,
            protocol=spec.protocol,  # type: ignore[arg-type]
            default_base_url=spec.default_base_url,
            alternate_base_urls=list(spec.alternate_base_urls),
            requires_api_key=spec.requires_api_key,
            supports_model_discovery=spec.supports_model_discovery,
            default_models=list(spec.default_models),
            configured=config is not None,
            api_key_configured=resolved.api_key is not None,
            api_key_hint=_secret_hint(resolved.api_key),
            api_key_source=resolved.api_key_source,  # type: ignore[arg-type]
            base_url=resolved.base_url,
            default_model=resolved.default_model,
            is_default=bool(config and config.is_default),
            updated_at=config.updated_at if config else None,
            supports_tts=spec.supports_tts,
            tts_models=[spec.tts_default_model] if spec.tts_default_model else [],
            tts_voices=list(spec.tts_voices),
            tts_base_url=config.tts_base_url if config else None,
            tts_model=config.tts_model if config else None,
            tts_voice=config.tts_voice if config else None,
            tts_effective_base_url=resolved.tts_base_url if spec.supports_tts else None,
            tts_effective_model=resolved.tts_model,
            tts_effective_voice=resolved.tts_voice,
        )

    def _resolve(
        self, spec: ProviderSpec, config: Optional[ProviderConfig]
    ) -> ResolvedProvider:
        user_key: Optional[str] = None
        if config is not None and config.encrypted_api_key:
            try:
                user_key = decrypt_secret(config.encrypted_api_key, self.settings)
            except DecryptionError as exc:
                raise AppError(
                    500,
                    "PROVIDER_KEY_DECRYPT_FAILED",
                    "Stored provider credential could not be decrypted",
                ) from exc
        env_key = self._environment_api_key(spec)
        api_key = user_key or env_key
        api_key_source = "user" if user_key else ("environment" if env_key else None)

        base_url = (
            (config.base_url if config is not None else None)
            or self._environment_value(spec.env_base_url_setting)
            or spec.default_base_url
        )
        default_model = (
            (config.default_model if config is not None else None)
            or self._environment_value(spec.env_model_setting)
            or spec.default_models[0]
        )
        tts_base_url: Optional[str] = None
        tts_model: Optional[str] = None
        tts_voice: Optional[str] = None
        if spec.supports_tts:
            configured_base = config.base_url if config is not None else None
            base_is_default = (configured_base or spec.default_base_url).rstrip(
                "/"
            ) == spec.default_base_url.rstrip("/")
            tts_base_url = (
                (config.tts_base_url if config is not None else None)
                or (
                    spec.tts_default_base_url
                    if spec.tts_default_base_url and base_is_default
                    else (
                        _qwen_tts_base_url(base_url)
                        if spec.id == 'qwen'
                        else base_url
                    )
                )
            )
            tts_model = (
                (config.tts_model if config is not None else None)
                or spec.tts_default_model
            )
            tts_voice = (
                (config.tts_voice if config is not None else None)
                or spec.tts_default_voice
            )
        return ResolvedProvider(
            spec=spec,
            config=config,
            api_key=api_key,
            api_key_source=api_key_source,
            base_url=base_url,
            default_model=default_model,
            tts_base_url=tts_base_url,
            tts_model=tts_model,
            tts_voice=tts_voice,
        )

    def _environment_value(self, setting_name: Optional[str]) -> Optional[str]:
        if not setting_name:
            return None
        value = getattr(self.settings, setting_name, None)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _environment_api_key(self, spec: ProviderSpec) -> Optional[str]:
        value = self._environment_value(spec.env_key_setting)
        if value:
            return value
        for alias in spec.env_key_aliases:
            value = self._environment_value(alias)
            if value:
                return value
        return None

    def _default_model(self, spec: ProviderSpec) -> str:
        return (
            self._environment_value(spec.env_model_setting) or spec.default_models[0]
        )

    def _spec(self, provider_id: str) -> ProviderSpec:
        spec = get_provider_spec(provider_id)
        if spec is None:
            raise AppError(404, "PROVIDER_NOT_FOUND", "Provider not found")
        return spec

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.services.provider_registry import ProviderSpec, get_provider_spec
from app.services.url_guard import validate_provider_base_url


def production_settings(**overrides: object) -> Settings:
    base = Settings(
        app_env="production",
        jwt_secret="test-only-jwt-secret-7f3c1a9e5b2d8f4c6a0e1d3b7c9f2a5e",
        email_delivery="agentsend",
        agentsend_api_key="ask_test",
        sms_delivery="spug",
        spug_url="https://push.spug.cc/send/test-template",
    )
    return base.model_copy(update=overrides)


def llama_spec() -> ProviderSpec:
    spec = get_provider_spec("llama-local")
    assert spec is not None
    return spec


async def test_plain_http_external_url_is_rejected_in_production() -> None:
    with pytest.raises(AppError) as error:
        await validate_provider_base_url(
            "http://47.120.68.25:65535/v1", llama_spec(), production_settings()
        )

    assert error.value.code == "INSECURE_PROVIDER_URL"


async def test_allowlisted_host_passes_in_production() -> None:
    settings = production_settings(provider_allow_insecure_http="47.120.68.25:65535")

    resolved = await validate_provider_base_url(
        "http://47.120.68.25:65535/v1/", llama_spec(), settings
    )

    assert resolved == "http://47.120.68.25:65535/v1"


async def test_allowlist_entry_must_match_the_port() -> None:
    settings = production_settings(provider_allow_insecure_http="47.120.68.25:65534")

    with pytest.raises(AppError) as error:
        await validate_provider_base_url(
            "http://47.120.68.25:65535/v1", llama_spec(), settings
        )

    assert error.value.code == "INSECURE_PROVIDER_URL"
import json
from typing import Any, Dict, Tuple

import httpx
from httpx import AsyncClient
from sqlalchemy import select
from support import register_and_verify

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.provider_config import ProviderConfig
from app.services import providers as provider_service
from app.services.providers import ProbeResult

PROVIDER_PATH = f"{settings.api_v1_prefix}/providers"


async def register_user(
    client: AsyncClient, email: str = "provider@example.com"
) -> Tuple[Dict[str, str], str]:
    response = await register_and_verify(
        client, email, display_name="Provider Tester"
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, response.json()["user"]["id"]


async def test_provider_list_contains_registry_defaults(client: AsyncClient) -> None:
    headers, _ = await register_user(client)

    response = await client.get(PROVIDER_PATH, headers=headers)

    assert response.status_code == 200
    providers = {item["id"]: item for item in response.json()}
    assert set(providers) == {
        "openai",
        "anthropic",
        "deepseek",
        "qwen",
        "kimi",
        "glm",
        "llama-local",
    }
    assert providers["llama-local"]["base_url"] == settings.llama_base_url
    assert providers["llama-local"]["default_model"] == settings.llama_model
    assert providers["anthropic"]["protocol"] == "anthropic"
    assert providers["openai"]["configured"] is False


async def test_api_key_is_encrypted_and_masked(client: AsyncClient) -> None:
    headers, _ = await register_user(client)
    raw_key = "sk-provider-secret-1234567890"

    response = await client.put(
        f"{PROVIDER_PATH}/openai/config",
        headers=headers,
        json={"api_key": raw_key, "is_default": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["api_key_configured"] is True
    assert body["api_key_hint"] == "****7890"
    assert body["api_key_source"] == "user"
    assert raw_key not in response.text

    async with AsyncSessionLocal() as session:
        stored = await session.scalar(
            select(ProviderConfig).where(ProviderConfig.provider_id == "openai")
        )
    assert stored is not None
    assert stored.encrypted_api_key
    assert raw_key not in stored.encrypted_api_key
    assert stored.api_key_last4 == "7890"


async def test_only_one_default_provider_per_owner(client: AsyncClient) -> None:
    headers, _ = await register_user(client)

    openai = await client.put(
        f"{PROVIDER_PATH}/openai/config",
        headers=headers,
        json={"api_key": "sk-openai-test-1234", "is_default": True},
    )
    deepseek = await client.put(
        f"{PROVIDER_PATH}/deepseek/config",
        headers=headers,
        json={"api_key": "sk-deepseek-test-5678", "is_default": True},
    )
    assert openai.status_code == 200
    assert deepseek.status_code == 200

    response = await client.get(PROVIDER_PATH, headers=headers)
    providers = {item["id"]: item for item in response.json()}
    assert providers["openai"]["is_default"] is False
    assert providers["deepseek"]["is_default"] is True


async def test_provider_configs_are_owner_scoped(client: AsyncClient) -> None:
    first_headers, _ = await register_user(client, "first-provider@example.com")
    second_headers, _ = await register_user(client, "second-provider@example.com")

    await client.put(
        f"{PROVIDER_PATH}/openai/config",
        headers=first_headers,
        json={"api_key": "sk-first-owner-1234", "default_model": "gpt-4o"},
    )
    await client.put(
        f"{PROVIDER_PATH}/openai/config",
        headers=second_headers,
        json={"api_key": "sk-second-owner-5678", "default_model": "gpt-4o-mini"},
    )

    first = await client.get(f"{PROVIDER_PATH}/openai", headers=first_headers)
    second = await client.get(f"{PROVIDER_PATH}/openai", headers=second_headers)

    assert first.json()["api_key_hint"] == "****1234"
    assert first.json()["default_model"] == "gpt-4o"
    assert second.json()["api_key_hint"] == "****5678"
    assert second.json()["default_model"] == "gpt-4o-mini"


async def test_clear_and_delete_provider_config(client: AsyncClient) -> None:
    headers, _ = await register_user(client)
    original_env_key = settings.openai_api_key
    settings.openai_api_key = ""
    try:
        await client.put(
            f"{PROVIDER_PATH}/openai/config",
            headers=headers,
            json={"api_key": "sk-clear-me-1234"},
        )
        cleared = await client.put(
            f"{PROVIDER_PATH}/openai/config",
            headers=headers,
            json={"clear_api_key": True},
        )
        assert cleared.status_code == 200
        assert cleared.json()["api_key_configured"] is False
        assert cleared.json()["api_key_hint"] is None

        deleted = await client.delete(
            f"{PROVIDER_PATH}/openai/config", headers=headers
        )
        assert deleted.status_code == 204
        provider = await client.get(f"{PROVIDER_PATH}/openai", headers=headers)
        assert provider.json()["configured"] is False
    finally:
        settings.openai_api_key = original_env_key


async def test_deleting_default_promotes_another_config(client: AsyncClient) -> None:
    headers, _ = await register_user(client)
    await client.put(
        f"{PROVIDER_PATH}/openai/config",
        headers=headers,
        json={"api_key": "sk-default-openai-1234", "is_default": True},
    )
    await client.put(
        f"{PROVIDER_PATH}/llama-local/config",
        headers=headers,
        json={"is_default": False},
    )

    response = await client.delete(f"{PROVIDER_PATH}/openai/config", headers=headers)
    assert response.status_code == 204

    provider = await client.get(f"{PROVIDER_PATH}/llama-local", headers=headers)
    assert provider.json()["is_default"] is True


async def test_private_provider_urls_are_rejected(client: AsyncClient) -> None:
    headers, _ = await register_user(client)

    for base_url in ("http://10.0.0.1/v1", "http://169.254.169.254/latest/meta-data"):
        response = await client.put(
            f"{PROVIDER_PATH}/qwen/config",
            headers=headers,
            json={"api_key": "sk-qwen-test-1234", "base_url": base_url},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "PROVIDER_URL_NOT_ALLOWED"


async def test_provider_url_rejects_embedded_credentials(client: AsyncClient) -> None:
    headers, _ = await register_user(client)

    response = await client.put(
        f"{PROVIDER_PATH}/qwen/config",
        headers=headers,
        json={
            "api_key": "sk-qwen-test-1234",
            "base_url": "https://user:password@api.example.com/v1",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PROVIDER_URL"


async def test_unknown_provider_returns_not_found(client: AsyncClient) -> None:
    headers, _ = await register_user(client)

    response = await client.get(f"{PROVIDER_PATH}/missing", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROVIDER_NOT_FOUND"


async def test_connection_test_uses_probe_and_returns_latency(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, _ = await register_user(client)

    async def fake_probe(
        base_url: str, api_key: str, model: str, timeout: float
    ) -> ProbeResult:
        assert base_url == settings.llama_base_url
        assert model == settings.llama_model
        return ProbeResult(True, "Connected. Test probe.", ["model-a"])

    monkeypatch.setattr(provider_service, "_probe_openai", fake_probe)

    response = await client.post(f"{PROVIDER_PATH}/llama-local/test", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Connected. Test probe."
    assert body["models"] == ["model-a"]
    assert body["latency_ms"] >= 0


async def test_connection_test_requires_api_key(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, _ = await register_user(client)
    monkeypatch.setattr(settings, "openai_api_key", "")

    response = await client.post(f"{PROVIDER_PATH}/openai/test", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROVIDER_API_KEY_REQUIRED"


async def test_anthropic_uses_native_probe(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, _ = await register_user(client)
    await client.put(
        f"{PROVIDER_PATH}/anthropic/config",
        headers=headers,
        json={"api_key": "sk-ant-test-1234"},
    )

    async def fake_probe(
        base_url: str, api_key: str, model: str, timeout: float
    ) -> ProbeResult:
        assert base_url == "https://api.anthropic.com/v1"
        assert api_key == "sk-ant-test-1234"
        return ProbeResult(True, "Claude connected.", [])

    monkeypatch.setattr(provider_service, "_probe_anthropic", fake_probe)

    response = await client.post(f"{PROVIDER_PATH}/anthropic/test", headers=headers)

    assert response.status_code == 200
    assert response.json()["success"] is True


async def test_qwen_uses_dashscope_environment_fallback(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, _ = await register_user(client)
    monkeypatch.setattr(settings, "qwen_api_key", "")
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-dashscope-env-9876")

    response = await client.get(f"{PROVIDER_PATH}/qwen", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["api_key_configured"] is True
    assert body["api_key_hint"] == "****9876"
    assert body["api_key_source"] == "environment"
    assert "qwen3.8-omni-flash" in body["default_models"]


async def test_openai_probe_always_runs_chat_completion(monkeypatch: Any) -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "qwen3.8-omni-flash"}]},
            )
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.8-omni-flash"
        assert payload["modalities"] == ["text"]
        return httpx.Response(
            200,
            json={
                "model": "qwen3.8-omni-flash",
                "choices": [{"message": {"content": "OK"}}],
            },
        )

    real_async_client = provider_service.httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(provider_service.httpx, "AsyncClient", fake_async_client)

    result = await provider_service._probe_openai(
        "https://qwen.example.com/v1",
        "sk-qwen-test-1234",
        "qwen3.8-omni-flash",
        10.0,
        text_only=True,
    )

    assert result.success is True
    assert result.model == "qwen3.8-omni-flash"
    assert result.models == ["qwen3.8-omni-flash"]
    assert paths[1].endswith("/chat/completions")

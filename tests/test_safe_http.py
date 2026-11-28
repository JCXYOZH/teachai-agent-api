import asyncio
import socket
import time
from typing import Any, List, Optional

import httpcore
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.services import url_guard
from app.services.images import _validate_download_url
from app.services.provider_registry import ProviderSpec, get_provider_spec
from app.services.safe_http import (
    LIMITS,
    PinnedAsyncHTTPTransport,
    PinnedNetworkBackend,
    pinned_client,
)
from app.services.tts import _download_audio
from app.services.url_guard import OutboundRule, outbound_rule, validate_provider_base_url


def production_settings(**overrides: Any) -> Settings:
    base = Settings(
        app_env="production",
        jwt_secret="test-only-jwt-secret-7f3c1a9e5b2d8f4c6a0e1d3b7c9f2a5e",
        email_delivery="agentsend",
        agentsend_api_key="ask_test",
        sms_delivery="spug",
        spug_url="https://push.spug.cc/send/test-template",
    )
    return base.model_copy(update=overrides)


def development_settings() -> Settings:
    return Settings(app_env="development")


def _spec(provider_id: str) -> ProviderSpec:
    spec = get_provider_spec(provider_id)
    assert spec is not None
    return spec


def _addrinfo(addresses: List[str]) -> List[Any]:
    family = socket.AF_INET6 if ":" in addresses[0] else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, addresses: List[str]) -> List[Any]:
    seen: List[Any] = []

    def fake_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> List[Any]:
        seen.append((host, port, kwargs.get("type")))
        return _addrinfo(addresses)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return seen


def _patch_connect(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    attempts: List[Any] = []

    async def fake_connect(
        self: Any,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Any = None,
    ) -> str:
        attempts.append((host, port, timeout))
        return "stream:" + host

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    return attempts


async def test_backend_connects_to_the_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_resolver(monkeypatch, ["93.184.216.34"])
    attempts = _patch_connect(monkeypatch)

    stream = await PinnedNetworkBackend(OutboundRule()).connect_tcp(
        "example.com", 443, timeout=5.0
    )

    assert stream == "stream:93.184.216.34"
    assert seen == [("example.com", 443, socket.SOCK_STREAM)]
    assert attempts[0][:2] == ("93.184.216.34", 443)
    assert 0 < attempts[0][2] <= 5.0


@pytest.mark.parametrize(
    "addresses", [["10.0.0.5"], ["93.184.216.34", "192.168.1.1"]]
)
async def test_backend_blocks_hosts_with_a_private_answer(
    monkeypatch: pytest.MonkeyPatch, addresses: List[str]
) -> None:
    _patch_resolver(monkeypatch, addresses)
    attempts = _patch_connect(monkeypatch)

    with pytest.raises(httpcore.ConnectError):
        await PinnedNetworkBackend(OutboundRule()).connect_tcp(
            "example.com", 443, timeout=5.0
        )

    assert attempts == []


async def test_backend_fails_over_to_the_next_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, ["1.1.1.1", "8.8.8.8"])
    attempts: List[str] = []

    async def fake_connect(
        self: Any,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Any = None,
    ) -> str:
        attempts.append(host)
        if host == "1.1.1.1":
            raise httpcore.ConnectError("refused")
        return "stream:" + host

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    backend = PinnedNetworkBackend(OutboundRule())

    assert await backend.connect_tcp("example.com", 443, timeout=5.0) == "stream:8.8.8.8"
    assert attempts == ["1.1.1.1", "8.8.8.8"]


async def test_backend_raises_the_last_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, ["1.1.1.1", "8.8.8.8"])
    attempts: List[str] = []

    async def fake_connect(
        self: Any,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Any = None,
    ) -> str:
        attempts.append(host)
        raise httpcore.ConnectTimeout("slow")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)

    with pytest.raises(httpcore.ConnectTimeout):
        await PinnedNetworkBackend(OutboundRule()).connect_tcp(
            "example.com", 443, timeout=5.0
        )

    assert attempts == ["1.1.1.1", "8.8.8.8"]


async def test_backend_deduplicates_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolver(monkeypatch, ["1.1.1.1", "1.1.1.1"])
    attempts = _patch_connect(monkeypatch)

    await PinnedNetworkBackend(OutboundRule()).connect_tcp(
        "example.com", 443, timeout=5.0
    )

    assert [attempt[0] for attempt in attempts] == ["1.1.1.1"]


async def test_backend_allows_loopback_only_when_the_rule_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, ["127.0.0.1", "::1"])
    attempts = _patch_connect(monkeypatch)

    with pytest.raises(httpcore.ConnectError):
        await PinnedNetworkBackend(OutboundRule()).connect_tcp(
            "localhost", 65535, timeout=5.0
        )
    assert attempts == []

    await PinnedNetworkBackend(OutboundRule(allow_loopback=True)).connect_tcp(
        "localhost", 65535, timeout=5.0
    )
    assert attempts[0][0] == "127.0.0.1"


async def test_backend_skips_the_resolver_for_ip_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("getaddrinfo must not be called for IP literals")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    attempts = _patch_connect(monkeypatch)
    loopback = PinnedNetworkBackend(OutboundRule(allow_loopback=True))

    await loopback.connect_tcp("127.0.0.1", 65535, timeout=5.0)
    await loopback.connect_tcp("::1", 65535, timeout=5.0)
    with pytest.raises(httpcore.ConnectError):
        await PinnedNetworkBackend(OutboundRule()).connect_tcp("10.0.0.5", 80, timeout=5.0)

    assert [attempt[0] for attempt in attempts] == ["127.0.0.1", "::1"]


async def test_backend_maps_resolver_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _patch_connect(monkeypatch)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)

    with pytest.raises(httpcore.ConnectError):
        await PinnedNetworkBackend(OutboundRule()).connect_tcp(
            "missing.example.com", 443, timeout=5.0
        )

    assert attempts == []


async def test_backend_bounds_dns_resolution_by_the_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _patch_connect(monkeypatch)

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(2.0)
        return _addrinfo(["93.184.216.34"])

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    started = time.perf_counter()

    with pytest.raises(httpcore.ConnectTimeout):
        await PinnedNetworkBackend(OutboundRule()).connect_tcp(
            "slow.example.com", 443, timeout=0.05
        )

    assert time.perf_counter() - started < 1.0
    assert attempts == []


async def test_pinned_client_installs_the_pinned_backend() -> None:
    spec = _spec("qwen")
    async with pinned_client(
        5.0,
        base_url=spec.default_base_url,
        spec=spec,
        settings=production_settings(),
    ) as client:
        transport = client._transport
        assert isinstance(transport, PinnedAsyncHTTPTransport)
        pool = transport._pool
        backend = pool._network_backend
        assert isinstance(backend, PinnedNetworkBackend)
        # The vendor host is trusted, exactly like at save time.
        assert backend.rule == OutboundRule(allow_any=True)
        assert pool._max_connections == LIMITS.max_connections
        assert pool._max_keepalive_connections == LIMITS.max_keepalive_connections
        assert pool._keepalive_expiry == LIMITS.keepalive_expiry
        assert pool._http2 is False
        assert pool._retries == 0
        assert pool._ssl_context is not None
        assert client.follow_redirects is False
        assert client.timeout.connect == 5.0


async def test_pinned_client_uses_a_public_only_rule_for_downloads() -> None:
    async with pinned_client(
        5.0, base_url="http://127.0.0.1:65535/v1", settings=development_settings()
    ) as client:
        backend = client._transport._pool._network_backend

        assert isinstance(backend, PinnedNetworkBackend)
        assert backend.rule == OutboundRule()
        assert backend.rule.allows("127.0.0.1") is False


async def test_loopback_request_keeps_the_origin_host_header() -> None:
    hosts: List[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode("latin-1").partition(":")
            if name.strip().lower() == "host":
                hosts.append(value.strip())
        body = b"ok"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
            + str(len(body)).encode("ascii")
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with pinned_client(
            5.0,
            base_url=f"http://localhost:{port}/v1",
            spec=_spec("llama-local"),
            settings=production_settings(),
        ) as client:
            response = await client.get(f"http://localhost:{port}/v1/models")
        assert response.status_code == 200
        assert response.text == "ok"
    finally:
        server.close()
        await server.wait_closed()

    assert hosts == [f"localhost:{port}"]


async def test_rule_agrees_with_save_time_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llama, qwen = _spec("llama-local"), _spec("qwen")
    prod = production_settings()
    dev = development_settings()
    loopback = "http://localhost:65535/v1"
    custom = "https://gateway.example.com/v1"

    async def check(
        url: str, spec: ProviderSpec, settings: Settings, addresses: List[str], *, allowed: bool
    ) -> OutboundRule:
        monkeypatch.setattr(url_guard, "_resolve", lambda host, port: list(addresses))
        rule = outbound_rule(url, spec=spec, settings=settings)
        if allowed:
            await validate_provider_base_url(url, spec, settings)
            assert all(rule.allows(address) for address in addresses)
        else:
            with pytest.raises(AppError):
                await validate_provider_base_url(url, spec, settings)
            assert not all(rule.allows(address) for address in addresses)
        return rule

    rule = await check(loopback, llama, prod, ["127.0.0.1"], allowed=True)
    assert rule == OutboundRule(allow_loopback=True)
    await check(loopback, qwen, prod, ["127.0.0.1"], allowed=False)
    rule = await check(loopback, qwen, dev, ["127.0.0.1"], allowed=True)
    assert rule.allow_loopback is True
    rule = await check(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        qwen,
        prod,
        ["47.0.0.1"],
        allowed=True,
    )
    assert rule == OutboundRule(allow_any=True)
    allowlisted = production_settings(provider_allow_insecure_http="47.120.68.25:65535")
    rule = await check(
        "http://47.120.68.25:65535/v1", llama, allowlisted, ["47.120.68.25"], allowed=True
    )
    assert rule == OutboundRule(allow_any=True)
    rule = await check(custom, llama, prod, ["93.184.216.34"], allowed=True)
    assert rule == OutboundRule()
    assert rule.allows("93.184.216.34") is True
    assert rule.allows("10.0.0.1") is False
    await check(custom, llama, prod, ["93.184.216.34", "10.0.0.1"], allowed=False)


async def test_tts_download_rejects_non_public_audio_urls() -> None:
    blocked = (
        "http://127.0.0.1/x.mp3",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "https://user:pass@example.com/x.mp3",
    )
    for url in blocked:
        with pytest.raises(AppError) as error:
            await _download_audio(url, timeout=1.0, max_bytes=1024)
        assert error.value.code == "TTS_AUDIO_NOT_ALLOWED"


async def test_download_url_error_messages_cover_every_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unresolvable(host: str, port: int) -> List[str]:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(url_guard, "_resolve", unresolvable)
    with pytest.raises(AppError) as error:
        await _validate_download_url("https://cdn.example.com/a.png")
    assert error.value.code == "IMAGE_DOWNLOAD_NOT_ALLOWED"
    assert "unavailable" in error.value.message
    with pytest.raises(AppError) as error:
        await _download_audio("https://cdn.example.com/a.mp3", timeout=1.0, max_bytes=1024)
    assert error.value.code == "TTS_AUDIO_NOT_ALLOWED"
    assert "unavailable" in error.value.message

    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: ["10.0.0.1"])
    with pytest.raises(AppError) as error:
        await _validate_download_url("https://cdn.example.com/a.png")
    assert "private or reserved" in error.value.message

    with pytest.raises(AppError) as error:
        await _validate_download_url("file:///etc/passwd")
    assert error.value.message == "Generated image URL is invalid"
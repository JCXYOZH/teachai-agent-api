import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.security import mask_phone, normalize_phone
from app.services.sms import (
    LogSmsSender,
    MemorySmsSender,
    SmsDeliveryError,
    SpugSmsSender,
    build_sms_sender,
    clear_memory_outbox,
    last_sms_code,
)

SPUG_URL = "https://push.spug.cc/send/template-id"
PRODUCTION_BASE: dict = {
    "app_env": "production",
    "jwt_secret": "test-only-jwt-secret-7f3c1a9e5b2d8f4c6a0e1d3b7c9f2a5e",
    "email_delivery": "agentsend",
    "agentsend_api_key": "ask_test",
}


async def test_spug_posts_template_payload() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": 200})

    sender = SpugSmsSender(SPUG_URL, transport=httpx.MockTransport(handler))

    await sender.send_verification_code(
        to="13800138000", code="123456", ttl_seconds=600
    )

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == SPUG_URL
    assert json.loads(request.content) == {
        "name": "尊敬的用户",
        "code": "123456",
        "targets": "13800138000",
    }


async def test_spug_reports_non_success_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad template"})

    sender = SpugSmsSender(SPUG_URL, transport=httpx.MockTransport(handler))

    with pytest.raises(SmsDeliveryError):
        await sender.send_verification_code(
            to="13800138000", code="123456", ttl_seconds=600
        )


async def test_spug_reports_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    sender = SpugSmsSender(SPUG_URL, transport=httpx.MockTransport(handler))

    with pytest.raises(SmsDeliveryError):
        await sender.send_verification_code(
            to="13800138000", code="123456", ttl_seconds=600
        )


async def test_memory_sender_keeps_latest_code() -> None:
    clear_memory_outbox()
    sender = MemorySmsSender()

    await sender.send_verification_code(
        to="13800138000", code="123456", ttl_seconds=600
    )
    await sender.send_verification_code(
        to="13800138000", code="654321", ttl_seconds=600
    )

    assert last_sms_code("13800138000") == "654321"
    assert last_sms_code("13900139000") is None
    clear_memory_outbox()
    assert last_sms_code("13800138000") is None


async def test_log_sender_masks_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []
    monkeypatch.setattr(
        "app.services.sms.logger.warning",
        lambda message, *args: captured.append(message % args),
    )
    sender = LogSmsSender()

    await sender.send_verification_code(
        to="13800138000", code="123456", ttl_seconds=600
    )

    assert len(captured) == 1
    assert "138****8000" in captured[0]
    assert "13800138000" not in captured[0]


def test_build_sms_sender_selects_mode() -> None:
    assert isinstance(
        build_sms_sender(Settings(sms_delivery="memory")), MemorySmsSender
    )
    assert isinstance(build_sms_sender(Settings(sms_delivery="log")), LogSmsSender)
    spug = build_sms_sender(Settings(sms_delivery="spug", spug_url=SPUG_URL))
    assert isinstance(spug, SpugSmsSender)
    assert spug.url == SPUG_URL


def test_build_sms_sender_requires_spug_url() -> None:
    settings = Settings(sms_delivery="log").model_copy(
        update={"sms_delivery": "spug", "spug_url": ""}
    )

    with pytest.raises(SmsDeliveryError):
        build_sms_sender(settings)


def test_production_requires_spug_delivery() -> None:
    with pytest.raises(ValidationError):
        Settings(sms_delivery="log", **PRODUCTION_BASE)


def test_production_requires_https_spug_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            sms_delivery="spug",
            spug_url="http://push.spug.cc/send/template-id",
            **PRODUCTION_BASE,
        )


def test_production_accepts_https_spug_url() -> None:
    settings = Settings(sms_delivery="spug", spug_url=SPUG_URL, **PRODUCTION_BASE)

    assert settings.sms_delivery == "spug"
    assert settings.spug_url == SPUG_URL


def test_normalize_phone_accepts_common_formats() -> None:
    for value in (
        "13800138000",
        " 138 0013 8000 ",
        "+8613800138000",
        "86-13800138000",
        "138-0013-8000",
    ):
        assert normalize_phone(value) == "13800138000", value


def test_normalize_phone_rejects_invalid_values() -> None:
    for value in ("12800138000", "1380013800", "abc", ""):
        with pytest.raises(ValueError):
            normalize_phone(value)


def test_mask_phone() -> None:
    assert mask_phone("13800138000") == "138****8000"
    assert mask_phone("") == ""

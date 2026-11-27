import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.verification_log import VerificationSendLog
from app.services.email import last_verification_code

SEND_CODE_PATH = f"{settings.api_v1_prefix}/auth/register/send-code"
EMAILS = [
    "limit-a@example.com",
    "limit-b@example.com",
    "limit-c@example.com",
]


async def test_ip_minute_limit_blocks_the_next_email_request(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "email_ip_limit_per_minute", 2)

    for email in EMAILS[:2]:
        response = await client.post(SEND_CODE_PATH, json={"email": email})
        assert response.status_code == 202, response.text

    blocked = await client.post(SEND_CODE_PATH, json={"email": EMAILS[2]})

    assert blocked.status_code == 429
    error = blocked.json()["error"]
    assert error["code"] == "EMAIL_SEND_RATE_LIMITED"
    assert 1 <= error["details"]["retry_after"] <= 61
    assert last_verification_code(EMAILS[2]) is None


async def test_ip_hour_limit_blocks_after_configured_budget(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "email_ip_limit_per_minute", 10)
    monkeypatch.setattr(settings, "email_ip_limit_per_hour", 2)

    for email in EMAILS[:2]:
        response = await client.post(SEND_CODE_PATH, json={"email": email})
        assert response.status_code == 202, response.text

    blocked = await client.post(SEND_CODE_PATH, json={"email": EMAILS[2]})

    assert blocked.status_code == 429
    error = blocked.json()["error"]
    assert error["code"] == "EMAIL_SEND_RATE_LIMITED"
    assert error["details"]["retry_after"] > 60


async def test_target_hour_limit_applies_per_address_and_skips_logging(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "email_target_limit_per_hour", 1)

    first = await client.post(SEND_CODE_PATH, json={"email": EMAILS[0]})
    assert first.status_code == 202, first.text

    blocked = await client.post(SEND_CODE_PATH, json={"email": EMAILS[0]})

    assert blocked.status_code == 429
    error = blocked.json()["error"]
    assert error["code"] == "EMAIL_SEND_RATE_LIMITED"
    assert error["details"]["retry_after"] > 60

    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(select(VerificationSendLog))).all()
    assert [(row.channel, row.target) for row in rows] == [("email", EMAILS[0])]


async def test_email_and_sms_limits_are_counted_separately(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "email_ip_limit_per_minute", 1)
    monkeypatch.setattr(settings, "sms_ip_limit_per_minute", 1)

    email_ok = await client.post(SEND_CODE_PATH, json={"email": EMAILS[0]})
    sms_ok = await client.post(SEND_CODE_PATH, json={"phone": "13700000011"})
    assert email_ok.status_code == 202, email_ok.text
    assert sms_ok.status_code == 202, sms_ok.text

    email_blocked = await client.post(SEND_CODE_PATH, json={"email": EMAILS[1]})
    sms_blocked = await client.post(SEND_CODE_PATH, json={"phone": "13700000012"})

    assert email_blocked.status_code == 429
    assert email_blocked.json()["error"]["code"] == "EMAIL_SEND_RATE_LIMITED"
    assert sms_blocked.status_code == 429
    assert sms_blocked.json()["error"]["code"] == "SMS_SEND_RATE_LIMITED"

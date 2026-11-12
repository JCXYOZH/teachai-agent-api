from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import AsyncSessionLocal
from app.models.phone_verification import PhoneVerification
from app.models.verification_log import VerificationSendLog
from app.services.sms import last_sms_code

SEND_CODE_PATH = f"{settings.api_v1_prefix}/auth/register/send-code"
PHONES = ["13700000001", "13700000002", "13700000003", "13700000004"]


async def test_ip_minute_limit_blocks_fourth_phone_request(client: AsyncClient) -> None:
    for phone in PHONES[:3]:
        response = await client.post(SEND_CODE_PATH, json={"phone": phone})
        assert response.status_code == 202, response.text

    blocked = await client.post(SEND_CODE_PATH, json={"phone": PHONES[3]})

    assert blocked.status_code == 429
    error = blocked.json()["error"]
    assert error["code"] == "SMS_SEND_RATE_LIMITED"
    assert 1 <= error["details"]["retry_after"] <= 61
    assert last_sms_code(PHONES[3]) is None


async def test_ip_hour_limit_blocks_after_configured_budget(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sms_ip_limit_per_minute", 10)
    monkeypatch.setattr(settings, "sms_ip_limit_per_hour", 2)

    for phone in PHONES[:2]:
        response = await client.post(SEND_CODE_PATH, json={"phone": phone})
        assert response.status_code == 202, response.text

    blocked = await client.post(SEND_CODE_PATH, json={"phone": PHONES[2]})

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "SMS_SEND_RATE_LIMITED"
    assert blocked.json()["error"]["details"]["retry_after"] > 60


async def test_target_hour_limit_applies_per_phone(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sms_target_limit_per_hour", 1)

    first = await client.post(SEND_CODE_PATH, json={"phone": PHONES[0]})
    assert first.status_code == 202

    blocked = await client.post(SEND_CODE_PATH, json={"phone": PHONES[0]})

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "SMS_SEND_RATE_LIMITED"


async def test_email_channel_is_not_subject_to_sms_limits(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sms_ip_limit_per_minute", 1)

    for email in ("a@example.com", "b@example.com", "c@example.com"):
        response = await client.post(SEND_CODE_PATH, json={"email": email})
        assert response.status_code == 202, response.text


async def test_rate_limit_rows_expire_outside_the_window(client: AsyncClient) -> None:
    sent = await client.post(SEND_CODE_PATH, json={"phone": PHONES[0]})
    assert sent.status_code == 202

    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(select(VerificationSendLog))).all()
        assert len(rows) == 1
        assert rows[0].target == PHONES[0]
        assert rows[0].channel == "phone"
        backdated = utcnow() - timedelta(hours=2)
        await session.execute(
            update(VerificationSendLog).values(created_at=backdated)
        )
        await session.execute(update(PhoneVerification).values(last_sent_at=backdated))
        await session.commit()

    again = await client.post(SEND_CODE_PATH, json={"phone": PHONES[0]})

    assert again.status_code == 202, again.text

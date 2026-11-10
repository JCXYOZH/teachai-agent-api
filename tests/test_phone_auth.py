import pytest
from httpx import AsyncClient
from support import register_and_verify

from app.core.config import settings
from app.services.sms import SmsDeliveryError, last_sms_code

PHONE = "13800138000"
MASKED_PHONE = "138****8000"
PASSWORD = "Teach1234"
SEND_CODE_PATH = f"{settings.api_v1_prefix}/auth/register/send-code"
REGISTER_PATH = f"{settings.api_v1_prefix}/auth/register"
LOGIN_PATH = f"{settings.api_v1_prefix}/auth/login"
ME_PATH = f"{settings.api_v1_prefix}/auth/me"


async def register_phone_account(client: AsyncClient) -> dict:
    sent = await client.post(SEND_CODE_PATH, json={"phone": PHONE})
    assert sent.status_code == 202, sent.text
    code = last_sms_code(PHONE)
    assert code
    registered = await client.post(
        REGISTER_PATH,
        json={"phone": PHONE, "password": PASSWORD, "code": code},
    )
    assert registered.status_code == 201, registered.text
    return registered.json()


async def test_send_phone_code_returns_masked_target(client: AsyncClient) -> None:
    response = await client.post(SEND_CODE_PATH, json={"phone": " 138-0013-8000 "})

    assert response.status_code == 202
    body = response.json()
    assert body["channel"] == "phone"
    assert body["target"] == MASKED_PHONE
    assert body["email"] is None
    assert body["expires_in"] == settings.verification_code_ttl_seconds
    assert body["resend_after"] == settings.verification_resend_seconds
    assert last_sms_code(PHONE)


async def test_email_send_code_response_keeps_email_channel(client: AsyncClient) -> None:
    response = await client.post(
        SEND_CODE_PATH, json={"email": "Teacher@Example.com "}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["channel"] == "email"
    assert body["target"] == "teacher@example.com"
    assert body["email"] == "teacher@example.com"


async def test_register_with_phone_code_creates_account(client: AsyncClient) -> None:
    body = await register_phone_account(client)

    assert body["user"]["phone"] == MASKED_PHONE
    assert body["user"]["email"] is None
    assert body["user"]["display_name"] == "用户8000"
    assert client.cookies.get(settings.refresh_cookie_name)
    assert client.cookies.get(settings.csrf_cookie_name)


async def test_me_returns_masked_phone(client: AsyncClient) -> None:
    body = await register_phone_account(client)

    response = await client.get(
        ME_PATH, headers={"Authorization": f"Bearer {body['access_token']}"}
    )

    assert response.status_code == 200
    assert response.json()["phone"] == MASKED_PHONE
    assert response.json()["email"] is None


async def test_phone_account_can_login_with_identifier(client: AsyncClient) -> None:
    await register_phone_account(client)

    response = await client.post(
        LOGIN_PATH, json={"identifier": PHONE, "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["phone"] == MASKED_PHONE


async def test_phone_account_can_login_with_legacy_email_field(
    client: AsyncClient,
) -> None:
    await register_phone_account(client)

    response = await client.post(
        LOGIN_PATH, json={"email": "+86 138 0013 8000", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["phone"] == MASKED_PHONE


async def test_email_account_can_login_with_identifier(client: AsyncClient) -> None:
    await register_and_verify(client, "learner@example.com")

    response = await client.post(
        LOGIN_PATH, json={"identifier": "Learner@Example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "learner@example.com"


async def test_duplicate_phone_registration_is_rejected(client: AsyncClient) -> None:
    await register_phone_account(client)

    response = await client.post(SEND_CODE_PATH, json={"phone": PHONE})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PHONE_ALREADY_REGISTERED"


async def test_register_requires_exactly_one_contact(client: AsyncClient) -> None:
    both = await client.post(
        REGISTER_PATH,
        json={
            "email": "someone@example.com",
            "phone": PHONE,
            "password": PASSWORD,
            "code": "123456",
        },
    )
    neither = await client.post(
        REGISTER_PATH, json={"password": PASSWORD, "code": "123456"}
    )

    assert both.status_code == 422
    assert both.json()["error"]["code"] == "VALIDATION_ERROR"
    assert neither.status_code == 422


async def test_invalid_phone_number_is_rejected(client: AsyncClient) -> None:
    for bad in ("12345678901", "1380013800", "2380013800"):
        response = await client.post(SEND_CODE_PATH, json={"phone": bad})
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_register_without_pending_phone_code_returns_not_found(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER_PATH, json={"phone": PHONE, "password": PASSWORD, "code": "123456"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PHONE_VERIFICATION_NOT_FOUND"


async def test_wrong_phone_code_counts_attempt_then_accepts_real_code(
    client: AsyncClient,
) -> None:
    sent = await client.post(SEND_CODE_PATH, json={"phone": PHONE})
    assert sent.status_code == 202
    code = last_sms_code(PHONE)
    assert code
    wrong = "000000" if code != "000000" else "111111"

    rejected = await client.post(
        REGISTER_PATH, json={"phone": PHONE, "password": PASSWORD, "code": wrong}
    )

    assert rejected.status_code == 400
    error = rejected.json()["error"]
    assert error["code"] == "INVALID_VERIFICATION_CODE"
    assert error["details"]["attempts_remaining"] == settings.verification_max_attempts - 1

    accepted = await client.post(
        REGISTER_PATH, json={"phone": PHONE, "password": PASSWORD, "code": code}
    )
    assert accepted.status_code == 201


async def test_phone_code_resend_cooldown(client: AsyncClient) -> None:
    first = await client.post(SEND_CODE_PATH, json={"phone": PHONE})
    assert first.status_code == 202

    again = await client.post(SEND_CODE_PATH, json={"phone": PHONE})

    assert again.status_code == 429
    assert again.json()["error"]["code"] == "VERIFICATION_RESEND_TOO_SOON"


async def test_sms_delivery_failure_returns_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingSender:
        async def send_verification_code(
            self, *, to: str, code: str, ttl_seconds: int
        ) -> None:
            raise SmsDeliveryError("boom")

    monkeypatch.setattr("app.services.auth.get_sms_sender", lambda _settings: FailingSender())

    response = await client.post(SEND_CODE_PATH, json={"phone": PHONE})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SMS_DELIVERY_UNAVAILABLE"

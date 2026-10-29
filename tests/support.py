from httpx import AsyncClient

from app.core.config import settings
from app.services.email import last_verification_code

API = settings.api_v1_prefix
DEFAULT_PASSWORD = "Teach1234"


async def register_and_verify(
    client: AsyncClient,
    email: str,
    password: str = DEFAULT_PASSWORD,
    display_name: str = "Teacher",
):
    sent = await client.post(
        f"{API}/auth/register/send-code",
        json={"email": email},
    )
    assert sent.status_code == 202, sent.text

    code = last_verification_code(email.strip().lower())
    assert code, "verification code was not captured"

    registered = await client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
            "code": code,
        },
    )
    assert registered.status_code == 201, registered.text
    return registered


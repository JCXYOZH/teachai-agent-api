from datetime import timedelta

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import update
from support import register_and_verify

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.email_verification import EmailVerification
from app.services.email import last_verification_code

REGISTER_PAYLOAD = {
    'email': 'learner@example.com',
    'password': 'Teach1234',
    'display_name': 'Lin',
}
REFRESH_PATH = f'{settings.api_v1_prefix}/auth/refresh'
LOGOUT_PATH = f'{settings.api_v1_prefix}/auth/logout'
REGISTER_PATH = f'{settings.api_v1_prefix}/auth/register'
SEND_CODE_PATH = f'{settings.api_v1_prefix}/auth/register/send-code'
LOGIN_PATH = f'{settings.api_v1_prefix}/auth/login'


async def register_user(client: AsyncClient) -> Response:
    return await register_and_verify(
        client,
        str(REGISTER_PAYLOAD['email']),
        str(REGISTER_PAYLOAD['password']),
        str(REGISTER_PAYLOAD['display_name']),
    )


def csrf_headers(client: AsyncClient) -> dict:
    return {'X-CSRF-Token': client.cookies.get(settings.csrf_cookie_name) or ''}


def set_cookie(client: AsyncClient, name: str, value: str, path: str) -> None:
    client.cookies.set(name, value, domain='testserver.local', path=path)


async def test_send_code_creates_pending_verification_without_account(
    client: AsyncClient,
) -> None:
    response = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )

    assert response.status_code == 202
    body = response.json()
    assert body['email'] == REGISTER_PAYLOAD['email']
    assert body['expires_in'] == settings.verification_code_ttl_seconds
    assert body['resend_after'] == settings.verification_resend_seconds
    assert client.cookies.get(settings.refresh_cookie_name) is None

    login_response = await client.post(
        LOGIN_PATH,
        json={
            'email': REGISTER_PAYLOAD['email'],
            'password': REGISTER_PAYLOAD['password'],
        },
    )
    assert login_response.status_code == 401


async def test_register_with_valid_code_returns_user_and_sets_secure_cookies(
    client: AsyncClient,
) -> None:
    sent = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )
    assert sent.status_code == 202
    code = last_verification_code(str(REGISTER_PAYLOAD['email']))
    assert code

    response = await client.post(
        REGISTER_PATH,
        json={**REGISTER_PAYLOAD, 'code': code},
    )

    assert response.status_code == 201
    body = response.json()
    assert body['token_type'] == 'bearer'
    assert body['expires_in'] == settings.access_token_minutes * 60
    assert body['user']['email'] == REGISTER_PAYLOAD['email']
    assert client.cookies.get(settings.refresh_cookie_name)
    assert client.cookies.get(settings.csrf_cookie_name)

    set_cookies = response.headers.get_list('set-cookie')
    refresh_header = next(
        header for header in set_cookies if header.startswith(settings.refresh_cookie_name + '=')
    )
    csrf_header = next(
        header for header in set_cookies if header.startswith(settings.csrf_cookie_name + '=')
    )
    assert 'HttpOnly' in refresh_header
    assert 'Path=/api/v1/auth' in refresh_header
    assert 'HttpOnly' not in csrf_header
    assert 'Path=/' in csrf_header


async def test_send_code_rejects_duplicate_verified_email(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.post(
        SEND_CODE_PATH,
        json={'email': 'Learner@Example.com '},
    )

    assert response.status_code == 409
    assert response.json()['error']['code'] == 'EMAIL_ALREADY_REGISTERED'


async def test_existing_account_keeps_original_password(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.post(
        SEND_CODE_PATH,
        json={'email': REGISTER_PAYLOAD['email']},
    )

    assert response.status_code == 409
    login_response = await client.post(
        LOGIN_PATH,
        json={
            'email': REGISTER_PAYLOAD['email'],
            'password': REGISTER_PAYLOAD['password'],
        },
    )
    assert login_response.status_code == 200
    rejected = await client.post(
        LOGIN_PATH,
        json={'email': REGISTER_PAYLOAD['email'], 'password': 'Attacker1234'},
    )
    assert rejected.status_code == 401


async def test_wrong_verification_code_tracks_attempts(client: AsyncClient) -> None:
    sent = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )
    assert sent.status_code == 202
    code = last_verification_code(str(REGISTER_PAYLOAD['email']))
    assert code
    wrong_code = '000000' if code != '000000' else '000001'

    wrong = await client.post(
        REGISTER_PATH,
        json={**REGISTER_PAYLOAD, 'code': wrong_code},
    )

    assert wrong.status_code == 400
    assert wrong.json()['error']['code'] == 'INVALID_VERIFICATION_CODE'
    assert wrong.json()['error']['details']['attempts_remaining'] == settings.verification_max_attempts - 1

    verified = await client.post(
        REGISTER_PATH,
        json={**REGISTER_PAYLOAD, 'code': code},
    )
    assert verified.status_code == 201


async def test_verification_attempts_are_limited(client: AsyncClient) -> None:
    sent = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )
    assert sent.status_code == 202
    code = last_verification_code(str(REGISTER_PAYLOAD['email']))
    assert code
    wrong_code = '000000' if code != '000000' else '000001'

    for _ in range(settings.verification_max_attempts):
        rejected = await client.post(
            REGISTER_PATH,
            json={**REGISTER_PAYLOAD, 'code': wrong_code},
        )
        assert rejected.status_code == 400

    blocked = await client.post(
        REGISTER_PATH,
        json={**REGISTER_PAYLOAD, 'code': code},
    )

    assert blocked.status_code == 429
    assert blocked.json()['error']['code'] == 'VERIFICATION_ATTEMPTS_EXCEEDED'


async def test_expired_verification_code_is_rejected(client: AsyncClient) -> None:
    sent = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )
    assert sent.status_code == 202
    code = last_verification_code(str(REGISTER_PAYLOAD['email']))
    assert code

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(EmailVerification)
            .where(EmailVerification.email == REGISTER_PAYLOAD['email'])
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await db.commit()

    response = await client.post(
        REGISTER_PATH,
        json={**REGISTER_PAYLOAD, 'code': code},
    )

    assert response.status_code == 400
    assert response.json()['error']['code'] == 'VERIFICATION_CODE_EXPIRED'


async def test_verification_code_send_respects_cooldown(client: AsyncClient) -> None:
    sent = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )
    assert sent.status_code == 202

    too_soon = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )
    assert too_soon.status_code == 429
    assert too_soon.json()['error']['code'] == 'VERIFICATION_RESEND_TOO_SOON'

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(EmailVerification)
            .where(EmailVerification.email == REGISTER_PAYLOAD['email'])
            .values(last_sent_at=utcnow() - timedelta(hours=1))
        )
        await db.commit()

    resent = await client.post(
        SEND_CODE_PATH, json={'email': REGISTER_PAYLOAD['email']}
    )
    assert resent.status_code == 202
    code = last_verification_code(str(REGISTER_PAYLOAD['email']))
    verified = await client.post(
        REGISTER_PATH,
        json={**REGISTER_PAYLOAD, 'code': code},
    )
    assert verified.status_code == 201


async def test_login_and_current_user(client: AsyncClient) -> None:
    await register_user(client)
    client.cookies.clear()

    response = await client.post(
        f'{settings.api_v1_prefix}/auth/login',
        json={'email': REGISTER_PAYLOAD['email'], 'password': REGISTER_PAYLOAD['password']},
    )

    assert response.status_code == 200
    access_token = response.json()['access_token']
    me_response = await client.get(
        f'{settings.api_v1_prefix}/auth/me',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    assert me_response.status_code == 200
    assert me_response.json()['email'] == REGISTER_PAYLOAD['email']


async def test_login_rejects_wrong_password_without_enumeration(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.post(
        f'{settings.api_v1_prefix}/auth/login',
        json={'email': REGISTER_PAYLOAD['email'], 'password': 'Wrong1234'},
    )

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'INVALID_CREDENTIALS'


async def test_me_requires_valid_bearer_token(client: AsyncClient) -> None:
    response = await client.get(f'{settings.api_v1_prefix}/auth/me')

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'UNAUTHORIZED'
    assert response.headers['www-authenticate'] == 'Bearer'

    invalid_response = await client.get(
        f'{settings.api_v1_prefix}/auth/me',
        headers={'Authorization': 'Bearer not-a-token'},
    )
    assert invalid_response.status_code == 401
    assert invalid_response.json()['error']['code'] == 'INVALID_ACCESS_TOKEN'


async def test_registration_validates_password_policy(client: AsyncClient) -> None:
    email = 'weak@example.com'
    sent = await client.post(SEND_CODE_PATH, json={'email': email})
    assert sent.status_code == 202
    code = last_verification_code(email)
    assert code

    response = await client.post(
        REGISTER_PATH,
        json={'email': email, 'password': 'onlyletters', 'code': code},
    )

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert response.json()['error']['details']


async def test_refresh_rotates_tokens_and_rejects_replay(client: AsyncClient) -> None:
    first_response = await register_user(client)
    first_access_token = first_response.json()['access_token']
    old_refresh = client.cookies.get(settings.refresh_cookie_name)
    old_csrf = client.cookies.get(settings.csrf_cookie_name)
    assert old_refresh and old_csrf

    refresh_response = await client.post(REFRESH_PATH, headers=csrf_headers(client))

    assert refresh_response.status_code == 200
    body = refresh_response.json()
    new_refresh = client.cookies.get(settings.refresh_cookie_name)
    new_csrf = client.cookies.get(settings.csrf_cookie_name)
    assert body['access_token'] != first_access_token
    assert new_refresh != old_refresh
    assert new_csrf != old_csrf

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url='http://testserver',
        cookies={
            settings.refresh_cookie_name: old_refresh,
            settings.csrf_cookie_name: new_csrf,
        },
    ) as replay_client:
        replay_response = await replay_client.post(
            REFRESH_PATH,
            headers={'X-CSRF-Token': new_csrf},
        )

    assert replay_response.status_code == 401
    assert replay_response.json()['error']['code'] == 'INVALID_REFRESH_TOKEN'


async def test_refresh_requires_csrf(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.post(REFRESH_PATH)

    assert response.status_code == 403
    assert response.json()['error']['code'] == 'CSRF_INVALID'


async def test_logout_revokes_session_and_clears_cookies(client: AsyncClient) -> None:
    await register_user(client)
    saved_refresh = client.cookies.get(settings.refresh_cookie_name)
    saved_csrf = client.cookies.get(settings.csrf_cookie_name)
    assert saved_refresh and saved_csrf

    logout_response = await client.post(LOGOUT_PATH, headers=csrf_headers(client))

    assert logout_response.status_code == 204
    assert client.cookies.get(settings.refresh_cookie_name) is None
    assert client.cookies.get(settings.csrf_cookie_name) is None

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url='http://testserver',
        cookies={
            settings.refresh_cookie_name: saved_refresh,
            settings.csrf_cookie_name: saved_csrf,
        },
    ) as replay_client:
        replay_response = await replay_client.post(
            REFRESH_PATH,
            headers={'X-CSRF-Token': saved_csrf},
        )

    assert replay_response.status_code == 401

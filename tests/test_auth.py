from httpx import ASGITransport, AsyncClient, Response

from app.core.config import settings
from app.main import app

REGISTER_PAYLOAD = {
    'email': 'learner@example.com',
    'password': 'Teach1234',
    'display_name': 'Lin',
}
REFRESH_PATH = f'{settings.api_v1_prefix}/auth/refresh'
LOGOUT_PATH = f'{settings.api_v1_prefix}/auth/logout'


async def register_user(client: AsyncClient) -> Response:
    return await client.post(f'{settings.api_v1_prefix}/auth/register', json=REGISTER_PAYLOAD)


def csrf_headers(client: AsyncClient) -> dict:
    return {'X-CSRF-Token': client.cookies.get(settings.csrf_cookie_name) or ''}


def set_cookie(client: AsyncClient, name: str, value: str, path: str) -> None:
    client.cookies.set(name, value, domain='testserver.local', path=path)


async def test_register_returns_user_and_sets_secure_cookies(client: AsyncClient) -> None:
    response = await register_user(client)

    assert response.status_code == 201
    body = response.json()
    assert body['token_type'] == 'bearer'
    assert body['expires_in'] == settings.access_token_minutes * 60
    assert body['user']['email'] == REGISTER_PAYLOAD['email']
    assert 'refresh_token' not in body
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


async def test_registration_rejects_duplicate_email(client: AsyncClient) -> None:
    assert (await register_user(client)).status_code == 201

    response = await register_user(client)

    assert response.status_code == 409
    assert response.json()['error']['code'] == 'EMAIL_ALREADY_REGISTERED'


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
    response = await client.post(
        f'{settings.api_v1_prefix}/auth/register',
        json={'email': 'weak@example.com', 'password': 'onlyletters'},
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

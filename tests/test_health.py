from httpx import AsyncClient


async def test_health_is_public(client: AsyncClient) -> None:
    response = await client.get('/api/v1/health')

    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok'
    assert body['version'] == '0.1.0'
    assert response.headers['x-request-id']


async def test_readiness_checks_database(client: AsyncClient) -> None:
    response = await client.get('/api/v1/health/ready')

    assert response.status_code == 200
    assert response.json() == {'status': 'ready', 'checks': {'database': 'ok'}, 'detail': None}


async def test_request_id_is_echoed_when_valid(client: AsyncClient) -> None:
    response = await client.get(
        '/api/v1/health',
        headers={'X-Request-ID': 'client-request-1234'},
    )

    assert response.headers['x-request-id'] == 'client-request-1234'


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get('/api/v1/not-a-route')

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'NOT_FOUND'
    assert set(response.json()['error']) == {'code', 'message', 'details'}

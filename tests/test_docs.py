from httpx import AsyncClient

from app.core.config import settings


async def test_docs_use_self_hosted_swagger_assets(client: AsyncClient) -> None:
    response = await client.get("/docs")

    assert response.status_code == 200
    assert "/static/swagger-ui/swagger-ui-bundle.js" in response.text
    assert "/static/swagger-ui/swagger-ui.css" in response.text
    assert "cdn.jsdelivr.net" not in response.text


async def test_swagger_assets_are_served_locally(client: AsyncClient) -> None:
    bundle = await client.get("/static/swagger-ui/swagger-ui-bundle.js")
    stylesheet = await client.get("/static/swagger-ui/swagger-ui.css")
    source_map = await client.get("/static/swagger-ui/swagger-ui.css.map")

    assert bundle.status_code == 200
    assert "javascript" in bundle.headers["content-type"]
    assert stylesheet.status_code == 200
    assert "text/css" in stylesheet.headers["content-type"]
    assert source_map.status_code == 200
    assert '"version"' in source_map.text


async def test_chrome_devtools_workspace_probe_is_quiet(client: AsyncClient) -> None:
    response = await client.get("/.well-known/appspecific/com.chrome.devtools.json")

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"


async def test_openapi_schema_is_available(client: AsyncClient) -> None:
    response = await client.get(f"{settings.api_v1_prefix}/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == settings.app_name

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.config import settings
from app.core.middleware import ServerErrorShieldMiddleware, get_client_ip

ORIGIN = "http://localhost:3000"


async def _boom(request: Request) -> PlainTextResponse:
    raise RuntimeError("boom")


def _client() -> TestClient:
    app = Starlette(routes=[Route("/boom", _boom)])
    app.add_middleware(ServerErrorShieldMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_errors_return_json_500_with_cors_headers() -> None:
    response = _client().get("/boom", headers={"Origin": ORIGIN})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
            "details": None,
        }
    }
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_client_ip_ignores_client_supplied_forwarded_for_prefix(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    scope = {
        "type": "http",
        "client": ("127.0.0.1", 1234),
        "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")],
    }

    assert get_client_ip(Request(scope), settings) == "203.0.113.9"

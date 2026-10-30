from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.middleware import ServerErrorShieldMiddleware

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


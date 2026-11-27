import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.middleware import UploadSizeGuardMiddleware


def _run_guard(content_length: Optional[str]) -> Tuple[List[str], List[Dict[str, Any]]]:
    reached: List[str] = []

    async def app(scope, receive, send) -> None:
        reached.append(scope["path"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages: List[Dict[str, Any]] = []

    async def send(message) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": f"{settings.api_v1_prefix}/assets",
        "headers": [] if content_length is None else [(b"content-length", content_length.encode())],
        "state": {},
    }
    asyncio.run(UploadSizeGuardMiddleware(app, settings)(scope, receive, send))
    return reached, messages


def test_upload_guard_rejects_oversized_upload() -> None:
    reached, messages = _run_guard(str(settings.asset_max_bytes + 5 * 1024 * 1024))

    assert reached == []
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert start["status"] == 413
    assert json.loads(body.decode("utf-8"))["error"]["code"] == "FILE_TOO_LARGE"


def test_upload_guard_rejects_upload_without_content_length() -> None:
    reached, messages = _run_guard(None)

    assert reached == []
    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 411


def test_upload_guard_allows_upload_within_limit() -> None:
    reached, messages = _run_guard(str(1024))

    assert reached == [f"{settings.api_v1_prefix}/assets"]
    assert messages == []


def test_upload_guard_ignores_other_routes() -> None:
    reached: List[str] = []

    async def app(scope, receive, send) -> None:
        reached.append(scope["path"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:
        raise AssertionError("guard must not answer other routes")

    scope = {
        "type": "http",
        "method": "POST",
        "path": f"{settings.api_v1_prefix}/courses",
        "headers": [(b"content-length", str(settings.asset_max_bytes * 10).encode())],
        "state": {},
    }
    asyncio.run(UploadSizeGuardMiddleware(app, settings)(scope, receive, send))

    assert reached == [f"{settings.api_v1_prefix}/courses"]

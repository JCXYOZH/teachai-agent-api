import json

import httpx
import pytest

from app.services.email import AgentsendEmailSender, EmailDeliveryError


async def test_agentsend_reuses_existing_inbox_and_sends() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/inboxes":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "inbox-1",
                            "displayName": "teachai-support",
                            "status": "active",
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path == "/inboxes/inbox-1/messages":
            body = json.loads(request.content)
            assert body["to"] == ["teacher@example.com"]
            assert "123456" in body["bodyText"]
            return httpx.Response(201, json={"id": "message-1"})
        return httpx.Response(404)

    sender = AgentsendEmailSender(
        "ask_test_key",
        "teachai-support",
        transport=httpx.MockTransport(handler),
    )
    await sender.send_verification_code(
        to="teacher@example.com", code="123456", ttl_seconds=600
    )

    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[0].headers["x-api-key"] == "ask_test_key"


async def test_agentsend_creates_inbox_when_missing() -> None:
    created = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/inboxes":
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path == "/inboxes":
            body = json.loads(request.content)
            created.append(body["displayName"])
            return httpx.Response(201, json={"id": "inbox-2"})
        if request.method == "POST" and request.url.path == "/inboxes/inbox-2/messages":
            return httpx.Response(201, json={"id": "message-2"})
        return httpx.Response(404)

    sender = AgentsendEmailSender(
        "ask_test_key",
        "teachai-support",
        transport=httpx.MockTransport(handler),
    )
    await sender.send_verification_code(
        to="teacher@example.com", code="654321", ttl_seconds=600
    )

    assert created == ["teachai-support"]


async def test_agentsend_uses_configured_inbox_without_listing() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(201, json={"id": "message-3"})

    sender = AgentsendEmailSender(
        "ask_test_key",
        "teachai-support",
        inbox_id="inbox-fixed",
        transport=httpx.MockTransport(handler),
    )
    await sender.send_verification_code(
        to="teacher@example.com", code="111111", ttl_seconds=600
    )

    assert calls == [("POST", "/inboxes/inbox-fixed/messages")]


async def test_agentsend_reports_delivery_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    sender = AgentsendEmailSender(
        "ask_test_key",
        "teachai-support",
        inbox_id="inbox-fixed",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EmailDeliveryError):
        await sender.send_verification_code(
            to="teacher@example.com", code="222222", ttl_seconds=600
        )


import asyncio
import logging
import threading
from typing import Dict, List, Optional, Protocol

import httpx

from app.core.config import Settings
from app.core.time import utcnow

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    pass


class EmailSender(Protocol):
    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None: ...


class LogEmailSender:
    """Development sender: writes the code to the application log."""

    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None:
        logger.warning(
            "email_verification_code email=%s code=%s ttl_seconds=%s",
            to,
            code,
            ttl_seconds,
        )


_memory_outbox: Dict[str, List[Dict[str, object]]] = {}
_memory_lock = threading.Lock()


class MemoryEmailSender:
    """Test sender that keeps codes in process memory."""

    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None:
        with _memory_lock:
            _memory_outbox.setdefault(to, []).append(
                {"code": code, "sent_at": utcnow(), "ttl_seconds": ttl_seconds}
            )


def last_verification_code(email: str) -> Optional[str]:
    with _memory_lock:
        entries = _memory_outbox.get(email) or []
        if not entries:
            return None
        return str(entries[-1]["code"])


def clear_memory_outbox() -> None:
    with _memory_lock:
        _memory_outbox.clear()


class AgentsendEmailSender:
    """Client for the documented AgentSend REST API."""

    def __init__(
        self,
        api_key: str,
        inbox_name: str,
        inbox_id: str = "",
        base_url: str = "https://api.agentsend.io",
        timeout: float = 20.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.api_key = api_key
        self.inbox_name = inbox_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self._inbox_id = inbox_id or None
        self._inbox_lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers={"x-api-key": self.api_key},
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    async def _ensure_inbox(self, client: httpx.AsyncClient) -> str:
        if self._inbox_id:
            return self._inbox_id
        async with self._inbox_lock:
            if self._inbox_id:
                return self._inbox_id
            if self.inbox_name:
                response = await client.get("/inboxes", params={"limit": 100})
                if response.status_code == 200:
                    for item in response.json().get("data", []):
                        if (
                            item.get("displayName") == self.inbox_name
                            and item.get("status") == "active"
                            and item.get("id")
                        ):
                            self._inbox_id = str(item["id"])
                            return self._inbox_id
            response = await client.post(
                "/inboxes", json={"displayName": self.inbox_name or "TeachAI"}
            )
            if response.status_code >= 400:
                raise EmailDeliveryError(
                    f"agentsend inbox creation failed ({response.status_code})"
                )
            body = response.json()
            inbox_id = body.get("id")
            if not inbox_id:
                raise EmailDeliveryError("agentsend inbox response is missing id")
            self._inbox_id = str(inbox_id)
            logger.info("agentsend_inbox_created id=%s", self._inbox_id)
            return self._inbox_id

    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None:
        minutes = max(1, ttl_seconds // 60)
        try:
            async with self._client() as client:
                inbox_id = await self._ensure_inbox(client)
                response = await client.post(
                    f"/inboxes/{inbox_id}/messages",
                    json={
                        "to": [to],
                        "subject": "TeachAI 邮箱验证码",
                        "bodyText": (
                            f"你的验证码是 {code}，{minutes} 分钟内有效。"
                            "若非本人操作请忽略。"
                        ),
                    },
                )
        except EmailDeliveryError:
            raise
        except httpx.HTTPError as exc:
            raise EmailDeliveryError("agentsend request failed") from exc
        if response.status_code >= 400:
            raise EmailDeliveryError(
                f"agentsend send failed ({response.status_code})"
            )


_sender: Optional[EmailSender] = None
_sender_key: Optional[tuple] = None


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_delivery == "memory":
        return MemoryEmailSender()
    if settings.email_delivery == "log":
        return LogEmailSender()
    if settings.email_delivery == "agentsend":
        if not settings.agentsend_api_key:
            raise EmailDeliveryError("AGENTSEND_API_KEY is not configured")
        return AgentsendEmailSender(
            settings.agentsend_api_key,
            settings.agentsend_inbox_name,
            settings.agentsend_inbox_id,
            settings.agentsend_base_url,
            settings.agentsend_timeout_seconds,
        )
    raise EmailDeliveryError(f"Unsupported EMAIL_DELIVERY: {settings.email_delivery}")


def get_email_sender(settings: Settings) -> EmailSender:
    global _sender, _sender_key
    key = (
        settings.email_delivery,
        settings.agentsend_api_key,
        settings.agentsend_inbox_name,
        settings.agentsend_inbox_id,
        settings.agentsend_base_url,
    )
    if _sender is None or _sender_key != key:
        _sender = build_email_sender(settings)
        _sender_key = key
    return _sender


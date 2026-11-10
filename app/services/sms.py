import logging
import threading
from typing import Dict, List, Optional, Protocol

import httpx

from app.core.config import Settings
from app.core.security import mask_phone
from app.core.time import utcnow

logger = logging.getLogger(__name__)

SPUG_SENDER_NAME = "尊敬的用户"


class SmsDeliveryError(RuntimeError):
    pass


class SmsSender(Protocol):
    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None: ...


class LogSmsSender:
    """Development sender: writes the code to the application log."""

    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None:
        logger.warning(
            "sms_verification_code phone=%s code=%s ttl_seconds=%s",
            mask_phone(to),
            code,
            ttl_seconds,
        )


_memory_outbox: Dict[str, List[Dict[str, object]]] = {}
_memory_lock = threading.Lock()


class MemorySmsSender:
    """Test sender that keeps codes in process memory."""

    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None:
        with _memory_lock:
            _memory_outbox.setdefault(to, []).append(
                {"code": code, "sent_at": utcnow(), "ttl_seconds": ttl_seconds}
            )


def last_sms_code(phone: str) -> Optional[str]:
    with _memory_lock:
        entries = _memory_outbox.get(phone) or []
        if not entries:
            return None
        return str(entries[-1]["code"])


def clear_memory_outbox() -> None:
    with _memory_lock:
        _memory_outbox.clear()


class SpugSmsSender:
    """Client for push.spug.cc template pushes."""

    def __init__(
        self,
        url: str,
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.transport = transport

    async def send_verification_code(
        self, *, to: str, code: str, ttl_seconds: int
    ) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.url,
                    json={
                        "name": SPUG_SENDER_NAME,
                        "code": code,
                        "targets": to,
                    },
                )
        except httpx.HTTPError as exc:
            raise SmsDeliveryError("spug request failed") from exc
        if response.status_code != 200:
            raise SmsDeliveryError(f"spug send failed ({response.status_code})")


_sender: Optional[SmsSender] = None
_sender_key: Optional[tuple] = None


def build_sms_sender(settings: Settings) -> SmsSender:
    if settings.sms_delivery == "memory":
        return MemorySmsSender()
    if settings.sms_delivery == "log":
        return LogSmsSender()
    if settings.sms_delivery == "spug":
        if not settings.spug_url:
            raise SmsDeliveryError("SPUG_URL is not configured")
        return SpugSmsSender(settings.spug_url, settings.spug_timeout_seconds)
    raise SmsDeliveryError(f"Unsupported SMS_DELIVERY: {settings.sms_delivery}")


def get_sms_sender(settings: Settings) -> SmsSender:
    global _sender, _sender_key
    key = (settings.sms_delivery, settings.spug_url, settings.spug_timeout_seconds)
    if _sender is None or _sender_key != key:
        _sender = build_sms_sender(settings)
        _sender_key = key
    return _sender

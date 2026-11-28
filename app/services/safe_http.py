"""Outbound HTTP whose connections are pinned to validated DNS results.

`app.services.url_guard` decides which addresses a URL may talk to (public
addresses only, plus loopback for the local llama.cpp or a dev/test run).
`PinnedNetworkBackend` resolves the host once, checks every answer against that
rule and then connects to the approved IP literal itself, so the resolver is
never consulted a second time between the check and the connect. That closes
the DNS-rebinding window left by the pre-flight checks in `images.py`/`tts.py`
and by the save-time validation of provider base URLs.

Depends on `httpcore.AsyncConnectionPool(network_backend=...)`; `requirements.txt`
pins the matching `httpcore` version.
"""

import functools
import ipaddress
import logging
import socket
from typing import Any, Iterable, List, Optional

import anyio
import anyio.to_thread
import httpcore
import httpx

from app.core.config import Settings, get_settings
from app.services.provider_registry import ProviderSpec
from app.services.url_guard import OutboundRule, outbound_rule

logger = logging.getLogger(__name__)

LIMITS = httpx.Limits(
    max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0
)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


class PinnedNetworkBackend(httpcore.AnyIOBackend):
    """`httpcore` backend that only connects to addresses allowed by `rule`."""

    def __init__(self, rule: OutboundRule) -> None:
        super().__init__()
        self.rule = rule

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> httpcore.AsyncNetworkStream:
        deadline = None if timeout is None else anyio.current_time() + timeout
        addresses = await self._approved_addresses(host, port, deadline)
        last_error: Optional[BaseException] = None
        for address in addresses:
            remaining = None
            if deadline is not None:
                remaining = max(0.0, deadline - anyio.current_time())
            try:
                return await super().connect_tcp(
                    address,
                    port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                logger.info(
                    "outbound_connect_retry host=%s address=%s error=%s",
                    host,
                    address,
                    type(exc).__name__,
                )
        assert last_error is not None
        raise last_error

    async def _approved_addresses(
        self, host: str, port: int, deadline: Optional[float]
    ) -> List[str]:
        if _is_ip_literal(host):
            # A literal needs no resolver: it is exactly what we connect to.
            if not self.rule.allows(host):
                logger.warning("outbound_blocked host=%s reason=address", host)
                raise httpcore.ConnectError(f"Outbound address is not allowed: {host}")
            return [host]
        remaining = None
        if deadline is not None:
            remaining = max(0.0, deadline - anyio.current_time())
        try:
            with anyio.fail_after(remaining):
                infos = await anyio.to_thread.run_sync(
                    functools.partial(
                        socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
                    ),
                    abandon_on_cancel=True,
                )
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"DNS resolution timed out: {host}") from exc
        except OSError as exc:
            raise httpcore.ConnectError(f"DNS resolution failed: {host}") from exc
        addresses: List[str] = []
        for info in infos:
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise httpcore.ConnectError(f"DNS resolution returned no addresses: {host}")
        # One disallowed answer blocks the whole host: never "try the others".
        for address in addresses:
            if not self.rule.allows(address):
                logger.warning(
                    "outbound_blocked host=%s address=%s reason=address", host, address
                )
                raise httpcore.ConnectError(
                    f"Outbound address is not allowed: {host}"
                )
        return addresses


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """`httpx.AsyncHTTPTransport` whose connection pool uses the pinned backend.

    Only the network backend changes: TLS still verifies the origin host
    (`httpcore` passes `server_hostname` from the origin, so SNI and the
    certificate check are unaffected by connecting to an IP literal), proxies
    from the environment are ignored and redirects are never followed.
    """

    def __init__(self, rule: OutboundRule) -> None:
        super().__init__(trust_env=False, http2=False, limits=LIMITS, retries=0)
        pool = self._pool
        if not isinstance(pool, httpcore.AsyncConnectionPool):
            raise RuntimeError("expected httpcore.AsyncConnectionPool")
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=pool._ssl_context,
            max_connections=LIMITS.max_connections,
            max_keepalive_connections=LIMITS.max_keepalive_connections,
            keepalive_expiry=LIMITS.keepalive_expiry,
            http1=True,
            http2=False,
            retries=0,
            local_address=pool._local_address,
            socket_options=pool._socket_options,
            network_backend=PinnedNetworkBackend(rule),
        )


def pinned_client(
    timeout: float,
    *,
    base_url: str,
    spec: Optional[ProviderSpec] = None,
    settings: Optional[Settings] = None,
) -> httpx.AsyncClient:
    """`httpx.AsyncClient` for requests to `base_url` with DNS pinning.

    `spec=None` means the target is not a provider base URL (a download from a
    provider response): public addresses only, regardless of APP_ENV.
    """
    rule = outbound_rule(base_url, spec=spec, settings=settings or get_settings())
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,
        transport=PinnedAsyncHTTPTransport(rule),
    )
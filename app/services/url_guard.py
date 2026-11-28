import ipaddress
import socket
from dataclasses import dataclass
from typing import List, Optional, Set
from urllib.parse import urlsplit, urlunsplit

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError
from app.services.provider_registry import ProviderSpec


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".").lower()


def _trusted_hosts(spec: ProviderSpec) -> Set[str]:
    return {
        _hostname(url)
        for url in (spec.default_base_url,) + spec.alternate_base_urls
        if _hostname(url)
    }


def is_trusted_provider_url(url: str, spec: ProviderSpec) -> bool:
    return _hostname(url) in _trusted_hosts(spec)


def _is_loopback_hostname(host: str) -> bool:
    return host == "localhost" or host.endswith(".localhost")


def _is_loopback_host(host: str) -> bool:
    """`localhost` names and loopback IP literals alike."""
    if _is_loopback_hostname(host):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowlisted_insecure_host(
    host: str, port: Optional[int], settings: Settings
) -> bool:
    entries = settings.provider_allow_insecure_http_set
    if not entries:
        return False
    candidates = {host}
    if port is not None:
        candidates.add(f"{host}:{port}")
    return bool(entries & candidates)


def _local_allowed(spec: Optional[ProviderSpec], settings: Settings) -> bool:
    """Loopback URLs are only for the local llama.cpp spec, or a dev/test run."""
    return (spec is not None and spec.id == "llama-local") or settings.app_env in {
        "development",
        "test",
    }


@dataclass(frozen=True)
class OutboundRule:
    """Which addresses an outbound connection may use.

    Save-time validation (`validate_provider_base_url`) and connect-time
    enforcement (`app.services.safe_http`) both derive their decision from
    `_rule`, so the two cannot drift apart.
    """

    allow_any: bool = False
    allow_loopback: bool = False

    def allows(self, address: str) -> bool:
        if self.allow_any:
            return True
        try:
            # Drop an IPv6 zone id (`fe80::1%eth0`); Python 3.8 cannot parse it.
            resolved = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        if resolved.is_loopback:
            return self.allow_loopback
        return resolved.is_global


def _rule(
    *, trusted: bool, allowlisted: bool, loopback: bool, local_allowed: bool
) -> OutboundRule:
    if trusted or allowlisted:
        # Vendor/operator hosts skip the address check entirely, exactly like
        # the save-time validation does.
        return OutboundRule(allow_any=True)
    return OutboundRule(allow_loopback=loopback and local_allowed)


def outbound_rule(
    url: str, *, spec: Optional[ProviderSpec], settings: Settings
) -> OutboundRule:
    """Connect-time rule for `url`: the same decision as the save-time validation."""
    if spec is None:
        # Download targets come from provider responses: public addresses only,
        # regardless of APP_ENV.
        return OutboundRule()
    host = _hostname(url)
    try:
        port = urlsplit(url).port
    except ValueError:
        port = None
    return _rule(
        trusted=host in _trusted_hosts(spec),
        allowlisted=_allowlisted_insecure_host(host, port, settings),
        loopback=_is_loopback_host(host),
        local_allowed=_local_allowed(spec, settings),
    )


def _resolve(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def validate_provider_base_url(
    raw_url: str,
    spec: ProviderSpec,
    settings: Settings,
) -> str:
    value = raw_url.strip()
    if not value or len(value) > 500:
        raise AppError(422, "INVALID_PROVIDER_URL", "Provider URL is invalid")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError(422, "INVALID_PROVIDER_URL", "Provider URL is invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AppError(422, "INVALID_PROVIDER_URL", "Provider URL is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise AppError(422, "INVALID_PROVIDER_URL", "Provider URL is invalid") from exc

    host = parsed.hostname.rstrip(".").lower()
    trusted = host in _trusted_hosts(spec)
    allowlisted = _allowlisted_insecure_host(host, port, settings)
    loopback = _is_loopback_host(host)
    rule = _rule(
        trusted=trusted,
        allowlisted=allowlisted,
        loopback=loopback,
        local_allowed=_local_allowed(spec, settings),
    )
    if (
        parsed.scheme == "http"
        and not trusted
        and not loopback
        and not allowlisted
        and settings.app_env == "production"
    ):
        raise AppError(
            422,
            "INSECURE_PROVIDER_URL",
            "HTTPS is required for external provider URLs in production",
        )

    if not trusted and not allowlisted:
        if loopback:
            if not rule.allow_loopback:
                raise AppError(
                    422,
                    "PROVIDER_URL_NOT_ALLOWED",
                    "Loopback provider URLs are not allowed in this environment",
                )
        else:
            try:
                addresses = await run_in_threadpool(
                    _resolve, host, port or (443 if parsed.scheme == "https" else 80)
                )
            except OSError as exc:
                raise AppError(
                    422,
                    "PROVIDER_URL_UNRESOLVABLE",
                    "Provider host could not be resolved",
                ) from exc
            for address in addresses:
                try:
                    resolved = ipaddress.ip_address(address)
                except ValueError as exc:
                    raise AppError(
                        422, "PROVIDER_URL_NOT_ALLOWED", "Provider URL is not allowed"
                    ) from exc
                if not resolved.is_global:
                    raise AppError(
                        422,
                        "PROVIDER_URL_NOT_ALLOWED",
                        "Provider URL resolves to a private or reserved address",
                    )

    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


class UrlNotAllowed(Exception):
    """A provider-supplied download URL is unusable.

    `reason` is one of "invalid" (malformed), "unresolvable" (DNS failure) or
    "address" (not a public address); callers map it to their own error codes.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def resolve_public_addresses(url: str) -> List[str]:
    """Validate a provider-supplied download URL (images, audio) and resolve it."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise UrlNotAllowed("invalid")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UrlNotAllowed("invalid") from exc
    try:
        addresses = await run_in_threadpool(_resolve, parsed.hostname, port)
    except OSError as exc:
        raise UrlNotAllowed("unresolvable") from exc
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UrlNotAllowed("address") from exc
        if not resolved.is_global:
            raise UrlNotAllowed("address")
    return addresses
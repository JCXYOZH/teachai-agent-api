import ipaddress
import socket
from typing import List, Set
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


def _is_loopback_hostname(host: str) -> bool:
    return host == "localhost" or host.endswith(".localhost")


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
    loopback = _is_loopback_hostname(host)
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass

    if parsed.scheme == "http" and not trusted and not loopback and settings.app_env == "production":
        raise AppError(
            422,
            "INSECURE_PROVIDER_URL",
            "HTTPS is required for external provider URLs in production",
        )

    if not trusted:
        local_allowed = spec.id == "llama-local" or settings.app_env in {
            "development",
            "test",
        }
        if loopback and not local_allowed:
            raise AppError(
                422,
                "PROVIDER_URL_NOT_ALLOWED",
                "Loopback provider URLs are not allowed in this environment",
            )
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
        if not loopback:
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

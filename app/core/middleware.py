import logging
import re
import time
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import Settings
from app.core.errors import error_body

logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def get_client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        supplied_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_id if REQUEST_ID_PATTERN.fullmatch(supplied_id) else str(uuid4())
        )
        request.state.request_id = request_id
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed request_id=%s method=%s path=%s",
                request_id,
                request.method,
                request.url.path,
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{duration_ms:.1f}"
        logger.info(
            "request_completed request_id=%s method=%s path=%s status=%s duration_ms=%.1f client_ip=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            get_client_ip(request, self.settings),
        )
        return response
class ServerErrorShieldMiddleware(BaseHTTPMiddleware):
    """Return a JSON 500 from inside the CORS layer.

    Starlette's server-error middleware sits above the CORS middleware, so an
    unhandled exception would otherwise reach the browser as a response without
    ``Access-Control-Allow-Origin`` and be reported as a network failure.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            request_id = getattr(request.state, "request_id", "unknown")
            logger.exception(
                "unhandled_request_error request_id=%s method=%s path=%s",
                request_id,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=500,
                content=error_body("INTERNAL_ERROR", "An unexpected error occurred"),
            )

# Starlette parses multipart bodies into a spooled temporary file before the
# route runs, so an oversized upload is written to disk in full before the
# storage layer can answer 413. Reject it from Content-Length instead.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class UploadSizeGuardMiddleware:
    """Reject oversized uploads before the multipart parser reads the body."""

    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.guarded_path = f"{settings.api_v1_prefix}/assets"
        self.max_request_bytes = settings.asset_max_bytes + MULTIPART_OVERHEAD_BYTES

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        if scope.get("path", "").rstrip("/") != self.guarded_path:
            await self.app(scope, receive, send)
            return

        content_length = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    content_length = None
                break
        if content_length is None or content_length <= self.max_request_bytes:
            await self.app(scope, receive, send)
            return

        state = scope.get("state") or {}
        request_id = state.get("request_id", "unknown") if isinstance(state, dict) else "unknown"
        logger.warning(
            "upload_rejected_too_large request_id=%s content_length=%s limit_bytes=%s",
            request_id,
            content_length,
            self.settings.asset_max_bytes,
        )
        limit_mb = self.settings.asset_max_bytes // (1024 * 1024)
        response = JSONResponse(
            status_code=413,
            content=error_body(
                "FILE_TOO_LARGE",
                f"Uploaded file exceeds the {limit_mb} MB limit",
            ),
        )
        await response(scope, receive, send)

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

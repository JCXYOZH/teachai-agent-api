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
            # 最右一段由我们自己的 Nginx 追加；最左一段是客户端可伪造的。
            return forwarded_for.rsplit(",", 1)[-1].strip()
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
    """在 CORS 层内部返回 JSON 格式的 500 响应。

    Starlette 的服务端异常中间件位于 CORS 中间件之上，未捕获异常会以缺少
    ``Access-Control-Allow-Origin`` 的响应到达浏览器，被前端误判为网络故障。
    这里统一兜底，保证错误响应仍然携带 CORS 头。
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

# Starlette 会在进入路由之前把 multipart 请求体完整写入临时文件，超大文件
# 先落盘之后存储层才可能返回 413。因此这里提前按 Content-Length 拒绝。
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class UploadSizeGuardMiddleware:
    """在 multipart 解析器读取请求体之前拒绝超大文件上传。"""

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

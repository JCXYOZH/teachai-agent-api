import logging
from typing import Any, Dict, List, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = dict(headers or {})


def error_body(code: str, message: str, details: Optional[Any] = None) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def _http_error_metadata(status_code: int) -> tuple:
    mappings = {
        400: ("BAD_REQUEST", "Bad request"),
        401: ("UNAUTHORIZED", "Authentication required"),
        403: ("FORBIDDEN", "Request is not allowed"),
        404: ("NOT_FOUND", "Resource not found"),
        405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
        409: ("CONFLICT", "Request conflicts with current state"),
        413: ("PAYLOAD_TOO_LARGE", "Request payload is too large"),
        429: ("RATE_LIMITED", "Too many requests"),
    }
    return mappings.get(status_code, ("HTTP_ERROR", "Request failed"))


def _validation_details(exc: RequestValidationError) -> List[Dict[str, str]]:
    details: List[Dict[str, str]] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        details.append(
            {
                "field": location,
                "message": str(item.get("msg", "Invalid value")),
                "type": str(item.get("type", "validation_error")),
            }
        )
    return details


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "Request validation failed",
                _validation_details(exc),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code, default_message = _http_error_metadata(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else default_message
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.exception("Unhandled request error request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "An unexpected error occurred"),
        )

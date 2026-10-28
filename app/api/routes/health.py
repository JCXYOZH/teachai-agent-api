import asyncio
import logging

from fastapi import APIRouter

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import check_database
from app.schemas.common import HealthResponse, ReadyResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )


@router.get("/health/ready", response_model=ReadyResponse)
async def readiness() -> ReadyResponse:
    try:
        await asyncio.wait_for(check_database(), timeout=5.0)
    except Exception as exc:
        logger.warning("database_readiness_failed error=%s", type(exc).__name__)
        raise AppError(
            503,
            "DATABASE_UNAVAILABLE",
            "Database is not ready",
        ) from exc

    return ReadyResponse(status="ready", checks={"database": "ok"})

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.outline import (
    CourseOutlineResponse,
    OutlineGenerateRequest,
    OutlineSaveRequest,
)
from app.services.outlines import OutlineService

router = APIRouter(prefix="/courses", tags=["outlines"])


def get_outline_service(db: AsyncSession = Depends(get_db)) -> OutlineService:
    return OutlineService(db, settings)


@router.post(
    "/{course_id}/outline/generate",
    response_model=CourseOutlineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_outline(
    course_id: UUID,
    payload: OutlineGenerateRequest,
    current_user: User = Depends(get_current_user),
    service: OutlineService = Depends(get_outline_service),
) -> CourseOutlineResponse:
    return await service.generate(current_user, course_id, payload)


@router.get("/{course_id}/outline", response_model=CourseOutlineResponse)
async def get_outline(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: OutlineService = Depends(get_outline_service),
) -> CourseOutlineResponse:
    return await service.get_latest(current_user, course_id)


@router.put("/{course_id}/outline", response_model=CourseOutlineResponse)
async def save_outline(
    course_id: UUID,
    payload: OutlineSaveRequest,
    current_user: User = Depends(get_current_user),
    service: OutlineService = Depends(get_outline_service),
) -> CourseOutlineResponse:
    return await service.save_manual(current_user, course_id, payload)

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.classroom import (
    ClassroomGenerateRequest,
    ClassroomView,
    GenerationJobView,
)
from app.services.classrooms import ClassroomService

router = APIRouter(prefix="/courses", tags=["classroom"])
jobs_router = APIRouter(prefix="/classroom", tags=["classroom"])


def get_classroom_service(db: AsyncSession = Depends(get_db)) -> ClassroomService:
    return ClassroomService(db, settings)


@router.post(
    "/{course_id}/classroom/generate",
    response_model=GenerationJobView,
    status_code=status.HTTP_201_CREATED,
)
async def generate_classroom(
    course_id: UUID,
    payload: ClassroomGenerateRequest,
    current_user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> GenerationJobView:
    return await service.generate(current_user, course_id, payload)


@router.get("/{course_id}/classroom", response_model=ClassroomView)
async def get_classroom(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomView:
    return await service.get_latest(current_user, course_id)


@jobs_router.get("/jobs/{job_id}", response_model=GenerationJobView)
async def get_generation_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> GenerationJobView:
    return await service.get_job(current_user, job_id)

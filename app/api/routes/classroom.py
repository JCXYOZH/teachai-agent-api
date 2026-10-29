from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
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
from app.services import generation_jobs
from app.services.classrooms import ClassroomService
from app.services.generation_runner import run_generation_job

router = APIRouter(prefix="/courses", tags=["classroom"])
generation_router = APIRouter(prefix="/generation", tags=["generation"])


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
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> GenerationJobView:
    job = await service.create_job(current_user, course_id, payload)
    background_tasks.add_task(
        run_generation_job, job.id, generation_jobs.JOB_KIND_CLASSROOM
    )
    return job


@router.get("/{course_id}/classroom", response_model=ClassroomView)
async def get_classroom(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomView:
    return await service.get_latest(current_user, course_id)


@generation_router.get("/jobs/{job_id}", response_model=GenerationJobView)
async def get_generation_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> GenerationJobView:
    return await service.get_job(current_user, job_id)

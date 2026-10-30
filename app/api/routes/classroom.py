from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.classroom import (
    ClassroomGenerateRequest,
    ClassroomResponseSubmitRequest,
    ClassroomResponsesView,
    ClassroomSubmitView,
    ClassroomView,
    GenerationJobView,
    IllustrationGenerateRequest,
    NarrationGenerateRequest,
)
from app.services import generation_jobs
from app.services.classroom_responses import ClassroomResponseService
from app.services.classrooms import ClassroomService
from app.services.generation_runner import run_generation_job
from app.services.images import IllustrationService
from app.services.narrations import NarrationService

router = APIRouter(prefix="/courses", tags=["classroom"])
generation_router = APIRouter(prefix="/generation", tags=["generation"])


def get_classroom_service(db: AsyncSession = Depends(get_db)) -> ClassroomService:
    return ClassroomService(db, settings)


def get_response_service(
    db: AsyncSession = Depends(get_db),
) -> ClassroomResponseService:
    return ClassroomResponseService(db, settings)


def get_narration_service(
    db: AsyncSession = Depends(get_db),
) -> NarrationService:
    return NarrationService(db, settings)


def get_illustration_service(
    db: AsyncSession = Depends(get_db),
) -> IllustrationService:
    return IllustrationService(db, settings)


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


@router.post(
    "/{course_id}/classroom/narration",
    response_model=GenerationJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_classroom_narration(
    course_id: UUID,
    background_tasks: BackgroundTasks,
    payload: Optional[NarrationGenerateRequest] = None,
    current_user: User = Depends(get_current_user),
    service: NarrationService = Depends(get_narration_service),
) -> GenerationJobView:
    job = await service.create_job(
        current_user,
        course_id,
        payload or NarrationGenerateRequest(),
    )
    background_tasks.add_task(
        run_generation_job, job.id, generation_jobs.JOB_KIND_NARRATION
    )
    return job


@router.post(
    "/{course_id}/classroom/scenes/{scene_id}/narration",
    response_model=GenerationJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_scene_narration(
    course_id: UUID,
    scene_id: UUID,
    background_tasks: BackgroundTasks,
    payload: Optional[NarrationGenerateRequest] = None,
    current_user: User = Depends(get_current_user),
    service: NarrationService = Depends(get_narration_service),
) -> GenerationJobView:
    job = await service.create_job(
        current_user,
        course_id,
        payload or NarrationGenerateRequest(),
        scene_id=scene_id,
    )
    background_tasks.add_task(
        run_generation_job, job.id, generation_jobs.JOB_KIND_NARRATION
    )
    return job


@router.post(
    "/{course_id}/classroom/illustrations",
    response_model=GenerationJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_classroom_illustrations(
    course_id: UUID,
    background_tasks: BackgroundTasks,
    payload: Optional[IllustrationGenerateRequest] = None,
    current_user: User = Depends(get_current_user),
    service: IllustrationService = Depends(get_illustration_service),
) -> GenerationJobView:
    job = await service.create_job(
        current_user,
        course_id,
        payload or IllustrationGenerateRequest(),
    )
    background_tasks.add_task(
        run_generation_job, job.id, generation_jobs.JOB_KIND_IMAGE
    )
    return job


@router.post(
    "/{course_id}/classroom/scenes/{scene_id}/illustration",
    response_model=GenerationJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_scene_illustration(
    course_id: UUID,
    scene_id: UUID,
    background_tasks: BackgroundTasks,
    payload: Optional[IllustrationGenerateRequest] = None,
    current_user: User = Depends(get_current_user),
    service: IllustrationService = Depends(get_illustration_service),
) -> GenerationJobView:
    job = await service.create_job(
        current_user,
        course_id,
        payload or IllustrationGenerateRequest(),
        scene_id=scene_id,
    )
    background_tasks.add_task(
        run_generation_job, job.id, generation_jobs.JOB_KIND_IMAGE
    )
    return job


@router.put(
    "/{course_id}/classroom/scenes/{scene_id}/responses",
    response_model=ClassroomSubmitView,
)
async def submit_classroom_responses(
    course_id: UUID,
    scene_id: UUID,
    payload: ClassroomResponseSubmitRequest,
    current_user: User = Depends(get_current_user),
    service: ClassroomResponseService = Depends(get_response_service),
) -> ClassroomSubmitView:
    return await service.submit(current_user, course_id, scene_id, payload)


@router.get(
    "/{course_id}/classroom/responses", response_model=ClassroomResponsesView
)
async def list_classroom_responses(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ClassroomResponseService = Depends(get_response_service),
) -> ClassroomResponsesView:
    return await service.list_responses(current_user, course_id)


@generation_router.get("/jobs/{job_id}", response_model=GenerationJobView)
async def get_generation_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> GenerationJobView:
    return await service.get_job(current_user, job_id)

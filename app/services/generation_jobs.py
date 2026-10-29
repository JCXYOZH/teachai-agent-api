import logging
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.time import utcnow
from app.db.session import AsyncSessionLocal
from app.models.classroom import GenerationJob
from app.models.user import User
from app.schemas.classroom import GenerationJobView

logger = logging.getLogger(__name__)

JOB_KIND_OUTLINE = "outline"
JOB_KIND_CLASSROOM = "classroom"
ACTIVE_STATUSES = ("queued", "running")


def job_view(job: GenerationJob) -> GenerationJobView:
    return GenerationJobView(
        id=job.id,
        course_id=job.course_id,
        kind=job.kind,  # type: ignore[arg-type]
        outline_id=job.outline_id,
        stage_id=job.stage_id,
        provider_id=job.provider_id,
        model_name=job.model_name,
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        message=job.message,
        error_code=job.error_code,
        error_message=job.error_message,
        scene_count=job.scene_count,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


async def get_job_view(
    db: AsyncSession, owner: User, job_id: UUID
) -> GenerationJobView:
    job = await db.scalar(
        select(GenerationJob).where(
            GenerationJob.id == job_id,
            GenerationJob.owner_id == owner.id,
        )
    )
    if job is None:
        raise AppError(
            404,
            "GENERATION_JOB_NOT_FOUND",
            "Generation job not found",
        )
    return job_view(job)


async def create_job(
    db: AsyncSession,
    *,
    owner_id: UUID,
    course_id: UUID,
    kind: str,
    outline_id: Optional[UUID] = None,
    provider_id: Optional[str] = None,
    model_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    message: str = "Queued",
) -> GenerationJob:
    active_job_id = await db.scalar(
        select(GenerationJob.id)
        .where(
            GenerationJob.owner_id == owner_id,
            GenerationJob.course_id == course_id,
            GenerationJob.kind == kind,
            GenerationJob.status.in_(ACTIVE_STATUSES),
        )
        .order_by(GenerationJob.created_at.desc())
        .limit(1)
    )
    if active_job_id is not None:
        raise AppError(
            409,
            "GENERATION_IN_PROGRESS",
            "Another generation is already running for this course",
            details={"job_id": str(active_job_id)},
        )

    job = GenerationJob(
        owner_id=owner_id,
        course_id=course_id,
        outline_id=outline_id,
        kind=kind,
        payload=payload or {},
        provider_id=provider_id,
        model_name=model_name,
        status="queued",
        progress=0,
        message=message,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def mark_running(
    db: AsyncSession, job_id: UUID, *, progress: int, message: str
) -> Optional[GenerationJob]:
    job = await db.get(GenerationJob, job_id)
    if job is None:
        return None
    job.status = "running"
    job.progress = progress
    job.message = message
    job.started_at = job.started_at or utcnow()
    await db.commit()
    return job


async def mark_progress(
    db: AsyncSession, job_id: UUID, *, progress: int, message: str
) -> None:
    job = await db.get(GenerationJob, job_id)
    if job is None:
        return
    job.progress = progress
    job.message = message
    await db.commit()


async def complete_job(
    db: AsyncSession,
    job_id: UUID,
    *,
    message: str,
    outline_id: Optional[UUID] = None,
    stage_id: Optional[UUID] = None,
    scene_count: Optional[int] = None,
) -> None:
    job = await db.get(GenerationJob, job_id)
    if job is None:
        return
    if outline_id is not None:
        job.outline_id = outline_id
    if stage_id is not None:
        job.stage_id = stage_id
    if scene_count is not None:
        job.scene_count = scene_count
    job.status = "completed"
    job.progress = 100
    job.message = message
    job.completed_at = utcnow()
    await db.commit()


async def fail_job(
    db: AsyncSession, job_id: UUID, error_code: str, error_message: str
) -> None:
    job = await db.get(GenerationJob, job_id)
    if job is None:
        return
    job.status = "failed"
    job.message = "Generation failed"
    job.error_code = error_code
    job.error_message = error_message[:2000]
    job.completed_at = utcnow()
    await db.commit()


async def recover_interrupted_jobs() -> int:
    async with AsyncSessionLocal() as db:
        jobs = (
            await db.scalars(
                select(GenerationJob).where(
                    GenerationJob.status.in_(ACTIVE_STATUSES)
                )
            )
        ).all()
        for job in jobs:
            job.status = "failed"
            job.message = "Generation interrupted"
            job.error_code = "GENERATION_INTERRUPTED"
            job.error_message = "The server restarted before this job finished"
            job.completed_at = utcnow()
        if jobs:
            await db.commit()
            logger.warning("generation_jobs_interrupted count=%s", len(jobs))
        return len(jobs)

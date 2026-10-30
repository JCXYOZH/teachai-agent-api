import logging
from uuid import UUID

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import generation_jobs
from app.services.classrooms import ClassroomService
from app.services.images import IllustrationService
from app.services.narrations import NarrationService
from app.services.outlines import OutlineService

logger = logging.getLogger(__name__)


async def run_generation_job(job_id: UUID, kind: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            if kind == generation_jobs.JOB_KIND_OUTLINE:
                await OutlineService(db, settings).run_job(job_id)
            elif kind == generation_jobs.JOB_KIND_CLASSROOM:
                await ClassroomService(db, settings).run_job(job_id)
            elif kind == generation_jobs.JOB_KIND_NARRATION:
                await NarrationService(db, settings).run_job(job_id)
            elif kind == generation_jobs.JOB_KIND_IMAGE:
                await IllustrationService(db, settings).run_job(job_id)
            else:
                logger.error(
                    "generation_job_unknown_kind job_id=%s kind=%s", job_id, kind
                )
                await generation_jobs.fail_job(
                    db,
                    job_id,
                    "GENERATION_JOB_INVALID_KIND",
                    f"Unknown generation job kind: {kind}",
                )
        except Exception:
            logger.exception("generation_job_crashed job_id=%s kind=%s", job_id, kind)
            try:
                await db.rollback()
                await generation_jobs.fail_job(
                    db,
                    job_id,
                    "GENERATION_JOB_FAILED",
                    "Generation job crashed",
                )
            except Exception:
                logger.exception(
                    "generation_job_failure_record_failed job_id=%s", job_id
                )

import asyncio
import logging
from datetime import timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import AsyncSessionLocal
from app.models.classroom import GenerationJob
from app.services import generation_jobs
from app.services.generation_runner import run_generation_job

logger = logging.getLogger(__name__)

QUEUED_MESSAGE = "Queued"
CLAIMED_MESSAGE = "Starting"
RECOVERY_INTERVAL_SECONDS = 60.0


class GenerationQueue:
    """Database backed worker pool shared by every API process.

    A worker claims the oldest queued job with ``SELECT ... FOR UPDATE SKIP
    LOCKED``, marks it running in the same transaction and then executes it.
    That keeps the queue correct without Redis and lets several API processes
    poll the same table without ever claiming the same job twice.
    """

    def __init__(self, concurrency: int, poll_seconds: float) -> None:
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        self._wakeup = asyncio.Event()
        self._workers: List[asyncio.Task] = []
        self._recovery: Optional[asyncio.Task] = None

    @property
    def started(self) -> bool:
        return bool(self._workers)

    async def start(self) -> None:
        if self._workers:
            return
        try:
            await self.recover_stale(
                float(settings.generation_job_startup_grace_seconds)
            )
        except Exception:
            logger.exception("generation_queue_startup_recovery_failed")
        for index in range(self.concurrency):
            self._workers.append(asyncio.ensure_future(self._worker_loop(index)))
        self._recovery = asyncio.ensure_future(self._recovery_loop())
        logger.info(
            "generation_queue_started concurrency=%s poll_seconds=%s stale_seconds=%s",
            self.concurrency,
            self.poll_seconds,
            settings.generation_job_stale_seconds,
        )

    async def stop(self) -> None:
        tasks = list(self._workers)
        if self._recovery is not None:
            tasks.append(self._recovery)
            self._recovery = None
        self._workers = []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                continue
            except Exception:
                logger.exception("generation_queue_task_failed_on_stop")
        logger.info("generation_queue_stopped")

    def notify(self) -> None:
        self._wakeup.set()

    async def enqueue(self, job_id: UUID) -> None:
        logger.info("generation_job_enqueued job_id=%s", job_id)
        self.notify()

    async def claim_next(self) -> Optional[GenerationJob]:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                job = await db.scalar(
                    select(GenerationJob)
                    .where(GenerationJob.status == "queued")
                    .order_by(GenerationJob.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if job is None:
                    return None
                job.status = "running"
                job.progress = 0
                job.message = CLAIMED_MESSAGE
                job.started_at = utcnow()
                job.completed_at = None
                job.error_code = None
                job.error_message = None
        logger.info("generation_job_claimed job_id=%s kind=%s", job.id, job.kind)
        return job

    async def recover_stale(self, grace_seconds: float) -> int:
        """Requeue jobs whose worker stopped, fail them after max attempts."""
        cutoff = utcnow() - timedelta(seconds=grace_seconds)
        max_attempts = settings.generation_job_max_attempts
        requeued: List[GenerationJob] = []
        failed: List[GenerationJob] = []
        async with AsyncSessionLocal() as db:
            async with db.begin():
                jobs = (
                    await db.scalars(
                        select(GenerationJob)
                        .where(GenerationJob.status == "running")
                        .where(GenerationJob.updated_at < cutoff)
                        .order_by(GenerationJob.created_at)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
                for job in jobs:
                    payload = dict(job.payload or {})
                    attempts = int(payload.get("attempts") or 0) + 1
                    payload["attempts"] = attempts
                    job.payload = payload
                    if attempts >= max_attempts:
                        job.status = "failed"
                        job.message = "Generation failed"
                        job.error_code = "GENERATION_INTERRUPTED"
                        job.error_message = (
                            "The worker stopped before this job finished "
                            f"(attempt {attempts} of {max_attempts})"
                        )
                        job.completed_at = utcnow()
                        failed.append(job)
                        continue
                    job.status = "queued"
                    job.progress = 0
                    job.message = QUEUED_MESSAGE
                    job.started_at = None
                    job.completed_at = None
                    job.error_code = None
                    job.error_message = None
                    requeued.append(job)
        for job in failed:
            generation_jobs._record_job_outcome(job, "failed")
        if requeued or failed:
            logger.warning(
                "generation_jobs_stale_recovered requeued=%s failed=%s",
                len(requeued),
                len(failed),
            )
        if requeued:
            self.notify()
        return len(requeued)

    async def _worker_loop(self, index: int) -> None:
        while True:
            self._wakeup.clear()
            try:
                job = await self.claim_next()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("generation_queue_claim_failed worker=%s", index)
                await self._sleep(self.poll_seconds)
                continue
            if job is None:
                await self._wait_for_work()
                continue
            try:
                await run_generation_job(job.id, job.kind)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "generation_queue_job_crashed job_id=%s kind=%s", job.id, job.kind
                )

    async def _recovery_loop(self) -> None:
        while True:
            await self._sleep(RECOVERY_INTERVAL_SECONDS)
            try:
                await self.recover_stale(float(settings.generation_job_stale_seconds))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("generation_queue_recovery_failed")

    async def _wait_for_work(self) -> None:
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=self.poll_seconds)
        except asyncio.TimeoutError:
            return

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise


_queue: Optional[GenerationQueue] = None


def get_generation_queue() -> GenerationQueue:
    global _queue
    if _queue is None:
        _queue = GenerationQueue(
            concurrency=settings.generation_worker_concurrency,
            poll_seconds=settings.generation_queue_poll_seconds,
        )
    return _queue


def reset_generation_queue() -> None:
    global _queue
    _queue = None


async def schedule_generation_job(
    background_tasks: BackgroundTasks, job_id: UUID, kind: str
) -> None:
    """Hand a queued job to the worker pool, or fall back to BackgroundTasks."""
    if settings.generation_queue_enabled:
        await get_generation_queue().enqueue(job_id)
        return
    background_tasks.add_task(run_generation_job, job_id, kind)

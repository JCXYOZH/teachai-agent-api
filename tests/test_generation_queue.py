import asyncio
from datetime import timedelta
from typing import Any, Dict, List
from uuid import UUID, uuid4

from fastapi import BackgroundTasks
from httpx import AsyncClient
from sqlalchemy import select, update
from support import register_and_verify

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import AsyncSessionLocal
from app.models.classroom import GenerationJob
from app.models.course import Course
from app.services import generation_jobs
from app.services import generation_queue as queue_module
from app.services.generation_queue import GenerationQueue, schedule_generation_job

API = settings.api_v1_prefix


async def seed_jobs(
    client: AsyncClient, count: int, email: str = "queue@example.com"
) -> List[UUID]:
    registered = await register_and_verify(client, email)
    headers = {"Authorization": "Bearer " + registered.json()["access_token"]}
    created = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": "Queue course", "description": "Generation queue fixture"},
    )
    assert created.status_code == 201, created.text
    course_id = UUID(created.json()["id"])
    job_ids: List[UUID] = []
    async with AsyncSessionLocal() as session:
        owner_id = await session.scalar(
            select(Course.owner_id).where(Course.id == course_id)
        )
        for _ in range(count):
            job = GenerationJob(
                owner_id=owner_id,
                course_id=course_id,
                kind="classroom",
                status="queued",
                message="Queued",
            )
            session.add(job)
            await session.flush()
            job_ids.append(job.id)
        await session.commit()
    return job_ids


async def mark_running_stale(
    job_id: UUID, *, attempts: int, age_seconds: int
) -> None:
    stale_at = utcnow() - timedelta(seconds=age_seconds)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(GenerationJob)
            .where(GenerationJob.id == job_id)
            .values(
                status="running",
                progress=30,
                started_at=stale_at,
                updated_at=stale_at,
                payload={"attempts": attempts},
            )
        )
        await session.commit()


async def test_claim_next_marks_job_running(client: AsyncClient) -> None:
    job_ids = await seed_jobs(client, 1, "queue-claim@example.com")
    queue = GenerationQueue(concurrency=1, poll_seconds=0.1)

    claimed = await queue.claim_next()

    assert claimed is not None
    assert claimed.id == job_ids[0]
    assert claimed.status == "running"
    assert claimed.started_at is not None
    assert await queue.claim_next() is None

    async with AsyncSessionLocal() as session:
        stored = await session.get(GenerationJob, job_ids[0])
        assert stored is not None
        assert stored.status == "running"


async def test_concurrent_claims_never_share_a_job(client: AsyncClient) -> None:
    await seed_jobs(client, 1, "queue-lock@example.com")
    queue = GenerationQueue(concurrency=1, poll_seconds=0.1)

    claims = await asyncio.gather(
        queue.claim_next(),
        queue.claim_next(),
        queue.claim_next(),
    )

    assert len([claim for claim in claims if claim is not None]) == 1


async def test_worker_pool_enforces_concurrency_limit(
    client: AsyncClient, monkeypatch: Any
) -> None:
    await seed_jobs(client, 3, "queue-workers@example.com")
    state: Dict[str, int] = {"current": 0, "peak": 0, "finished": 0}

    async def fake_run(job_id: UUID, kind: str) -> None:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0.05)
        async with AsyncSessionLocal() as session:
            await generation_jobs.complete_job(session, job_id, message="Done")
        state["current"] -= 1
        state["finished"] += 1

    monkeypatch.setattr(queue_module, "run_generation_job", fake_run)
    monkeypatch.setattr(settings, "generation_job_startup_grace_seconds", 60)
    queue = GenerationQueue(concurrency=1, poll_seconds=0.05)

    await queue.start()
    try:
        for _ in range(100):
            if state["finished"] == 3:
                break
            await asyncio.sleep(0.05)
    finally:
        await queue.stop()

    assert state["finished"] == 3
    assert state["peak"] == 1


async def test_stale_running_job_is_requeued(client: AsyncClient) -> None:
    job_ids = await seed_jobs(client, 1, "queue-stale@example.com")
    await mark_running_stale(job_ids[0], attempts=0, age_seconds=120)
    queue = GenerationQueue(concurrency=1, poll_seconds=0.1)

    assert await queue.recover_stale(60) == 1

    async with AsyncSessionLocal() as session:
        stored = await session.get(GenerationJob, job_ids[0])
        assert stored is not None
        assert stored.status == "queued"
        assert stored.payload["attempts"] == 1
        assert stored.started_at is None
        assert stored.completed_at is None


async def test_fresh_running_job_is_left_alone(client: AsyncClient) -> None:
    job_ids = await seed_jobs(client, 1, "queue-fresh@example.com")
    await mark_running_stale(job_ids[0], attempts=0, age_seconds=5)
    queue = GenerationQueue(concurrency=1, poll_seconds=0.1)

    assert await queue.recover_stale(60) == 0

    async with AsyncSessionLocal() as session:
        stored = await session.get(GenerationJob, job_ids[0])
        assert stored is not None
        assert stored.status == "running"


async def test_stale_job_fails_after_max_attempts(
    client: AsyncClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "generation_job_max_attempts", 2)
    job_ids = await seed_jobs(client, 1, "queue-giveup@example.com")
    await mark_running_stale(job_ids[0], attempts=1, age_seconds=120)
    queue = GenerationQueue(concurrency=1, poll_seconds=0.1)

    assert await queue.recover_stale(60) == 0

    async with AsyncSessionLocal() as session:
        stored = await session.get(GenerationJob, job_ids[0])
        assert stored is not None
        assert stored.status == "failed"
        assert stored.error_code == "GENERATION_INTERRUPTED"
        assert stored.payload["attempts"] == 2


async def test_schedule_wakes_the_worker_pool(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "generation_queue_enabled", True)
    monkeypatch.setattr(queue_module, "_queue", None)
    queue = queue_module.get_generation_queue()
    background = BackgroundTasks()

    await schedule_generation_job(
        background, uuid4(), generation_jobs.JOB_KIND_CLASSROOM
    )

    assert background.tasks == []
    assert queue._wakeup.is_set()


async def test_schedule_falls_back_to_background_tasks(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "generation_queue_enabled", False)
    background = BackgroundTasks()

    await schedule_generation_job(
        background, uuid4(), generation_jobs.JOB_KIND_CLASSROOM
    )

    assert len(background.tasks) == 1
    assert background.tasks[0].func is queue_module.run_generation_job

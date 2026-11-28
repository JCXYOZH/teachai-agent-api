from typing import Dict, Tuple
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from support import register_and_verify

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import AsyncSessionLocal
from app.models.classroom import GenerationJob
from app.models.course import Course
from app.services import generation_jobs
from app.services.generation_jobs import create_job

API = settings.api_v1_prefix
KIND = "narration"


async def seed_course(
    client: AsyncClient, email: str
) -> Tuple[Dict[str, str], UUID, UUID]:
    registered = await register_and_verify(client, email)
    headers = {"Authorization": "Bearer " + registered.json()["access_token"]}
    created = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": "Unique job course", "description": "Unique job fixture"},
    )
    assert created.status_code == 201, created.text
    course_id = UUID(created.json()["id"])
    async with AsyncSessionLocal() as session:
        owner_id = await session.scalar(
            select(Course.owner_id).where(Course.id == course_id)
        )
    return headers, owner_id, course_id


async def add_job(owner_id: UUID, course_id: UUID, status: str) -> UUID:
    async with AsyncSessionLocal() as session:
        job = GenerationJob(
            owner_id=owner_id,
            course_id=course_id,
            kind=KIND,
            status=status,
            message="Queued" if status != "failed" else "Generation failed",
        )
        session.add(job)
        await session.commit()
        return job.id


async def test_partial_index_rejects_two_active_jobs(
    client: AsyncClient,
) -> None:
    _, owner_id, course_id = await seed_course(client, "unique-index@example.com")
    await add_job(owner_id, course_id, "queued")

    with pytest.raises(IntegrityError):
        await add_job(owner_id, course_id, "running")

    # Finished jobs never conflict with an active one.
    await add_job(owner_id, course_id, "failed")
    await add_job(owner_id, course_id, "completed")


async def test_create_job_reports_concurrent_insert_as_409(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, owner_id, course_id = await seed_course(client, "unique-race@example.com")
    existing_id = await add_job(owner_id, course_id, "queued")

    real_active_job_id = generation_jobs._active_job_id
    lookups = 0

    async def racing_active_job_id(db, owner, course, kind):
        # First lookup (the pre-check) misses the concurrent insert; the lookup
        # after the IntegrityError sees it.
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        return await real_active_job_id(db, owner, course, kind)

    monkeypatch.setattr(generation_jobs, "_active_job_id", racing_active_job_id)

    async with AsyncSessionLocal() as session:
        with pytest.raises(AppError) as error:
            await create_job(
                session,
                owner_id=owner_id,
                course_id=course_id,
                kind=KIND,
            )

    assert error.value.status_code == 409
    assert error.value.code == "GENERATION_IN_PROGRESS"
    assert error.value.details == {"job_id": str(existing_id)}
    assert lookups == 2

    # A different kind for the same course is still allowed.
    async with AsyncSessionLocal() as session:
        job = await create_job(
            session, owner_id=owner_id, course_id=course_id, kind="image"
        )
        assert job.status == "queued"

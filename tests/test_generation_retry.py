from typing import Any, Dict, Tuple
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from support import register_and_verify

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.classroom import GenerationJob
from app.models.course import Course
from app.services import generation_queue as queue_module

API = settings.api_v1_prefix


async def seed_course(
    client: AsyncClient, email: str
) -> Tuple[Dict[str, str], UUID, UUID]:
    registered = await register_and_verify(client, email)
    headers = {"Authorization": "Bearer " + registered.json()["access_token"]}
    created = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": "Retry course", "description": "Retry fixture"},
    )
    assert created.status_code == 201, created.text
    course_id = UUID(created.json()["id"])
    async with AsyncSessionLocal() as session:
        owner_id = await session.scalar(
            select(Course.owner_id).where(Course.id == course_id)
        )
    return headers, owner_id, course_id


async def add_job(
    owner_id: UUID,
    course_id: UUID,
    *,
    status: str = "failed",
    kind: str = "narration",
    payload: Any = None,
) -> UUID:
    async with AsyncSessionLocal() as session:
        job = GenerationJob(
            owner_id=owner_id,
            course_id=course_id,
            kind=kind,
            status=status,
            message="Generation failed" if status == "failed" else "Queued",
            error_code="IMAGE_PROVIDER_FAILED" if status == "failed" else None,
            error_message="boom" if status == "failed" else None,
            payload=payload
            if payload is not None
            else {"scene_ids": [str(uuid4())], "attempts": 2},
        )
        session.add(job)
        await session.commit()
        return job.id


async def test_retry_failed_job_requeues_payload(
    client: AsyncClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(queue_module, "run_generation_job", _noop)
    headers, owner_id, course_id = await seed_course(
        client, "retry-ok@example.com"
    )
    failed_id = await add_job(owner_id, course_id)

    response = await client.post(
        f"{API}/generation/jobs/{failed_id}/retry", headers=headers
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] != str(failed_id)
    assert body["status"] == "queued"
    assert body["kind"] == "narration"

    async with AsyncSessionLocal() as session:
        created = await session.get(GenerationJob, UUID(body["id"]))
        assert created is not None
        assert "attempts" not in created.payload
        assert created.payload["scene_ids"]

        original = await session.get(GenerationJob, failed_id)
        assert original is not None
        assert original.status == "failed"


async def test_retry_rejects_unfinished_jobs(client: AsyncClient) -> None:
    headers, owner_id, course_id = await seed_course(
        client, "retry-running@example.com"
    )
    running_id = await add_job(owner_id, course_id, status="running")

    response = await client.post(
        f"{API}/generation/jobs/{running_id}/retry", headers=headers
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "GENERATION_JOB_NOT_RETRYABLE"


async def test_retry_is_owner_scoped(client: AsyncClient) -> None:
    _, owner_id, course_id = await seed_course(client, "retry-owner@example.com")
    failed_id = await add_job(owner_id, course_id)
    other_headers, _, _ = await seed_course(client, "retry-intruder@example.com")

    response = await client.post(
        f"{API}/generation/jobs/{failed_id}/retry", headers=other_headers
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "GENERATION_JOB_NOT_FOUND"


async def test_retry_blocked_by_active_job(client: AsyncClient) -> None:
    headers, owner_id, course_id = await seed_course(
        client, "retry-active@example.com"
    )
    failed_id = await add_job(owner_id, course_id)
    await add_job(owner_id, course_id, status="queued")

    response = await client.post(
        f"{API}/generation/jobs/{failed_id}/retry", headers=headers
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "GENERATION_IN_PROGRESS"


async def _noop(job_id: UUID, kind: str) -> None:
    return None

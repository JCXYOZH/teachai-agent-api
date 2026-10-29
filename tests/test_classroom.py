import json
from typing import Any, Dict, List
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.classroom import GenerationJob
from app.models.course import Course
from app.services.classroom_dsl import DSL_VERSION, compile_scenes
from app.services.classroom_schema import (
    ClassroomParseError,
    normalize_classroom_plan,
)
from app.services.classrooms import ClassroomService
from app.services.generation_jobs import recover_interrupted_jobs
from app.services.provider_registry import get_provider_spec
from app.services.providers import ResolvedProvider

API = settings.api_v1_prefix


async def test_classroom_local_provider_disables_thinking(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_call(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "{}"

    monkeypatch.setattr("app.services.classrooms.call_openai_compatible", fake_call)
    spec = get_provider_spec("llama-local")
    assert spec is not None
    service = ClassroomService(None, settings)  # type: ignore[arg-type]

    await service._call_provider(
        ResolvedProvider(
            spec=spec,
            config=None,
            api_key=None,
            api_key_source=None,
            base_url="http://127.0.0.1:65535/v1",
            default_model="Qwen3.6-35B-A3B",
        ),
        "system",
        "user",
    )

    assert captured["disable_thinking"] is True


CLASSROOM_RESPONSE: Dict[str, Any] = {
    "name": "极限课堂",
    "description": "用直观例子理解极限。",
    "style": "Deep ink classroom with warm chalk accents.",
    "scenes": [
        {
            "type": "slide",
            "title": "从变化率出发",
            "bullets": ["平均变化率", "趋近过程", "瞬时速度"],
            "narration": "我们先比较一段时间内的平均速度，再让时间间隔逐渐缩短。",
        },
        {
            "type": "quiz",
            "title": "检查理解",
            "narration": "请选择一个最符合定义的答案。",
            "questions": [
                {
                    "question": "当间隔趋于零时，平均变化率趋向什么？",
                    "options": [
                        {"label": "瞬时变化率", "value": "A"},
                        {"label": "平均速度", "value": "B"},
                    ],
                    "answer": ["A"],
                    "analysis": "间隔趋零时得到瞬时变化率。",
                }
            ],
        },
    ],
}


def classroom_json(payload: Dict[str, Any] = CLASSROOM_RESPONSE) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def register_user(client: AsyncClient, email: str = "classroom@example.com") -> Dict[str, str]:
    response = await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": "Teach1234", "display_name": "Classroom"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def job_state(
    client: AsyncClient, headers: Dict[str, str], job_id: str
) -> Dict[str, Any]:
    response = await client.get(f"{API}/generation/jobs/{job_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def create_course(
    client: AsyncClient,
    headers: Dict[str, str],
    title: str = "函数的极限",
    description: str = "面向大学新生的微积分入门课程",
) -> str:
    response = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": title, "description": description},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def save_outline(client: AsyncClient, headers: Dict[str, str], course_id: str) -> None:
    response = await client.put(
        f"{API}/courses/{course_id}/outline",
        headers=headers,
        json={
            "course_title": "极限与连续",
            "language_directive": "用简体中文教学，保留必要英文术语。",
            "scenes": [
                {
                    "type": "slide",
                    "title": "从变化率出发",
                    "description": "用平均速度引出瞬时变化率。",
                    "key_points": ["平均变化率", "趋近过程"],
                },
                {
                    "type": "quiz",
                    "title": "检查理解",
                    "description": "判断极限的直观含义。",
                    "key_points": ["极限定义"],
                },
            ],
        },
    )
    assert response.status_code == 200, response.text


async def upload_material(client: AsyncClient, headers: Dict[str, str], course_id: str, content: str) -> None:
    response = await client.post(
        f"{API}/assets",
        headers=headers,
        data={"course_id": course_id},
        files={"file": ("limits.txt", content.encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text


def test_normalize_classroom_accepts_fences_and_repairs_quiz() -> None:
    raw = "```json\n" + classroom_json() + "\n```\n以上是课堂。"

    plan = normalize_classroom_plan(raw, "备用课堂")

    assert plan.name == "极限课堂"
    assert len(plan.scenes) == 2
    assert plan.scenes[0]["bullets"] == ["平均变化率", "趋近过程", "瞬时速度"]
    quiz = plan.scenes[1]
    assert quiz["type"] == "quiz"
    assert quiz["questions"][0]["type"] == "single"
    assert quiz["questions"][0]["answer"] == ["A"]
    assert quiz["questions"][0]["hasAnswer"] is True


def test_normalize_classroom_rejects_unusable_payload() -> None:
    try:
        normalize_classroom_plan("not json", "课程")
    except ClassroomParseError as exc:
        assert "valid JSON" in str(exc)
    else:
        raise AssertionError("invalid json should fail")

    try:
        normalize_classroom_plan(json.dumps({"scenes": []}), "课程")
    except ClassroomParseError as exc:
        assert "does not contain scenes" in str(exc)
    else:
        raise AssertionError("empty scenes should fail")


def test_compile_scenes_builds_dark_slide_and_speech_action() -> None:
    plan = normalize_classroom_plan(classroom_json(), "备用课堂")
    stage_id = uuid4()

    scenes = compile_scenes(plan, stage_id, "用简体中文教学。")

    assert [scene["order"] for scene in scenes] == [1, 2]
    slide = scenes[0]
    assert slide["stage_id"] == str(stage_id)
    assert slide["content"]["type"] == "slide"
    canvas = slide["content"]["canvas"]
    assert canvas["viewportSize"] == 1280
    assert canvas["viewportRatio"] == 720 / 1280
    assert canvas["background"] == {"type": "solid", "color": "#0B1714"}
    assert slide["actions"][0]["type"] == "speech"
    assert slide["actions"][0]["text"].startswith("我们先比较")

    quiz = scenes[1]
    assert quiz["content"]["type"] == "quiz"
    assert quiz["content"]["questions"][0]["answer"] == ["A"]
    assert quiz["actions"][0]["type"] == "speech"


async def test_generate_get_and_regenerate_classroom(client: AsyncClient, monkeypatch: Any) -> None:
    headers = await register_user(client)
    course_id = await create_course(client, headers)
    await save_outline(client, headers, course_id)
    await upload_material(
        client,
        headers,
        course_id,
        "极限描述函数在某点附近的变化趋势。MATERIAL-MARKER-7412",
    )
    prompts: List[str] = []

    async def fake_call(
        self: ClassroomService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        prompts.append(user_prompt)
        assert resolved.spec.id == "llama-local"
        return classroom_json()

    monkeypatch.setattr(ClassroomService, "_call_provider", fake_call)

    generated = await client.post(
        f"{API}/courses/{course_id}/classroom/generate",
        headers=headers,
        json={"instructions": "强调直觉"},
    )
    assert generated.status_code == 201, generated.text
    created_job = generated.json()
    assert created_job["kind"] == "classroom"
    assert created_job["status"] == "queued"

    job = await job_state(client, headers, created_job["id"])
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert job["scene_count"] == 2
    assert job["stage_id"]
    assert job["provider_id"] == "llama-local"
    assert job["model_name"] == settings.llama_model
    assert "MATERIAL-MARKER-7412" in prompts[0]
    assert "极限与连续" in prompts[0]
    assert "强调直觉" in prompts[0]

    fetched = await client.get(f"{API}/courses/{course_id}/classroom", headers=headers)
    assert fetched.status_code == 200, fetched.text
    classroom = fetched.json()
    assert classroom["id"] == job["stage_id"]
    assert classroom["version"] == 1
    assert classroom["dsl_version"] == DSL_VERSION
    assert classroom["scene_count"] == 2
    assert [scene["order"] for scene in classroom["scenes"]] == [1, 2]
    assert classroom["scenes"][0]["type"] == "slide"
    assert classroom["scenes"][1]["type"] == "quiz"
    assert classroom["scenes"][0]["content"]["canvas"]["viewportSize"] == 1280
    assert classroom["scenes"][0]["actions"][0]["type"] == "speech"

    regenerated = await client.post(
        f"{API}/courses/{course_id}/classroom/generate",
        headers=headers,
        json={},
    )
    assert regenerated.status_code == 201
    regenerated_job = await job_state(client, headers, regenerated.json()["id"])
    assert regenerated_job["status"] == "completed"
    assert regenerated_job["stage_id"] != job["stage_id"]

    latest = await client.get(f"{API}/courses/{course_id}/classroom", headers=headers)
    assert latest.json()["version"] == 2


async def test_classroom_requires_an_outline(client: AsyncClient) -> None:
    headers = await register_user(client, "no-outline-classroom@example.com")
    course_id = await create_course(client, headers)

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/generate",
        headers=headers,
        json={},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OUTLINE_NOT_FOUND"


async def test_classroom_and_jobs_are_owner_scoped(client: AsyncClient, monkeypatch: Any) -> None:
    first_headers = await register_user(client, "classroom-owner@example.com")
    second_headers = await register_user(client, "classroom-other@example.com")
    course_id = await create_course(client, first_headers)
    await save_outline(client, first_headers, course_id)

    async def fake_call(
        self: ClassroomService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return classroom_json()

    monkeypatch.setattr(ClassroomService, "_call_provider", fake_call)

    generated = await client.post(
        f"{API}/courses/{course_id}/classroom/generate",
        headers=first_headers,
        json={},
    )
    assert generated.status_code == 201, generated.text
    job_id = generated.json()["id"]

    hidden_classroom = await client.get(f"{API}/courses/{course_id}/classroom", headers=second_headers)
    assert hidden_classroom.status_code == 404
    assert hidden_classroom.json()["error"]["code"] == "COURSE_NOT_FOUND"

    hidden_job = await client.get(f"{API}/generation/jobs/{job_id}", headers=second_headers)
    assert hidden_job.status_code == 404
    assert hidden_job.json()["error"]["code"] == "GENERATION_JOB_NOT_FOUND"


async def test_invalid_model_output_marks_job_failed(client: AsyncClient, monkeypatch: Any) -> None:
    headers = await register_user(client, "invalid-classroom@example.com")
    course_id = await create_course(client, headers)
    await save_outline(client, headers, course_id)
    attempts = 0

    async def fake_call(
        self: ClassroomService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        nonlocal attempts
        attempts += 1
        return "not json"

    monkeypatch.setattr(ClassroomService, "_call_provider", fake_call)

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/generate",
        headers=headers,
        json={},
    )

    assert response.status_code == 201, response.text
    failed = await job_state(client, headers, response.json()["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "CLASSROOM_INVALID_RESPONSE"
    assert attempts == 2

    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(GenerationJob).where(GenerationJob.course_id == course_id))
    assert job is not None
    assert job.status == "failed"
    assert job.error_code == "CLASSROOM_INVALID_RESPONSE"


async def test_duplicate_generation_is_rejected(client: AsyncClient) -> None:
    headers = await register_user(client, "duplicate-job@example.com")
    course_id = await create_course(client, headers)
    await save_outline(client, headers, course_id)

    async with AsyncSessionLocal() as session:
        owner_id = await session.scalar(
            select(Course.owner_id).where(Course.id == UUID(course_id))
        )
        job = GenerationJob(
            owner_id=owner_id,
            course_id=UUID(course_id),
            kind="classroom",
            status="queued",
            progress=0,
            message="Queued",
        )
        session.add(job)
        await session.commit()
        existing_job_id = job.id

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/generate",
        headers=headers,
        json={},
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "GENERATION_IN_PROGRESS"
    assert error["details"]["job_id"] == str(existing_job_id)


async def test_startup_recovery_fails_interrupted_jobs(client: AsyncClient) -> None:
    headers = await register_user(client, "recover-job@example.com")
    course_id = await create_course(client, headers)

    async with AsyncSessionLocal() as session:
        owner_id = await session.scalar(
            select(Course.owner_id).where(Course.id == UUID(course_id))
        )
        job = GenerationJob(
            owner_id=owner_id,
            course_id=UUID(course_id),
            kind="classroom",
            status="running",
            progress=40,
            message="Generating classroom",
        )
        session.add(job)
        await session.commit()
        interrupted_job_id = job.id

    recovered = await recover_interrupted_jobs()
    assert recovered == 1

    state = await job_state(client, headers, str(interrupted_job_id))
    assert state["status"] == "failed"
    assert state["error_code"] == "GENERATION_INTERRUPTED"

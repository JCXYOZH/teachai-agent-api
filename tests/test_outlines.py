import json
from typing import Any, Dict, List

from httpx import AsyncClient
from sqlalchemy import func, select
from support import register_and_verify

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.outline import CourseOutline
from app.schemas.outline import OutlineSceneInput
from app.services.outline_prompt import SYSTEM_PROMPT as OUTLINE_SYSTEM_PROMPT
from app.services.outline_schema import (
    OutlineParseError,
    normalize_generated_outline,
    normalize_manual_outline,
)
from app.services.outlines import OutlineService
from app.services.provider_registry import get_provider_spec
from app.services.providers import ResolvedProvider

API = settings.api_v1_prefix


OUTLINE_RESPONSE: Dict[str, Any] = {
    "languageDirective": "用简体中文教学，保留必要英文术语。",
    "courseTitle": "极限与连续",
    "outlines": [
        {
            "id": "model-id-1",
            "type": "slide",
            "title": "从变化率出发",
            "description": "用平均速度引出瞬时变化率。",
            "keyPoints": ["平均变化率", "趋近过程", "平均变化率"],
            "teachingObjective": "理解极限的直观含义。",
            "estimatedDuration": 12,
            "order": 9,
        },
        {
            "type": "interactive",
            "title": "拖动参数观察极限",
            "description": "通过模拟观察函数逼近过程。",
            "keyPoints": ["变量控制", "逼近方向", "极限值"],
            "widgetType": "simulation",
            "widgetOutline": {"concept": "limit", "keyVariables": ["delta"]},
        },
    ],
}


def outline_json(payload: Dict[str, Any] = OUTLINE_RESPONSE) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def register_user(
    client: AsyncClient, email: str = "outline@example.com"
) -> Dict[str, str]:
    response = await register_and_verify(client, email, display_name="Outline")
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


async def upload_material(
    client: AsyncClient,
    headers: Dict[str, str],
    course_id: str,
    content: str,
) -> None:
    response = await client.post(
        f"{API}/assets",
        headers=headers,
        data={"course_id": course_id},
        files={"file": ("limits.txt", content.encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text


def test_generated_outline_accepts_fences_and_normalizes_fields() -> None:
    raw = "```json\n" + outline_json() + "\n```\n这里是一些解释。"

    result = normalize_generated_outline(raw, "备用标题")

    assert result.course_title == "极限与连续"
    assert result.language_directive.startswith("用简体中文")
    assert [scene["id"] for scene in result.scenes] == ["scene-1", "scene-2"]
    assert [scene["order"] for scene in result.scenes] == [1, 2]
    assert result.scenes[0]["key_points"] == ["平均变化率", "趋近过程"]
    assert result.scenes[1]["type"] == "interactive"
    assert result.scenes[1]["widget_type"] == "simulation"


def test_generated_outline_accepts_bare_array_and_unknown_types() -> None:
    raw = json.dumps(
        [
            {
                "type": "video",
                "title": "导入",
                "description": "",
                "keyPoints": [],
            }
        ],
        ensure_ascii=False,
    )

    result = normalize_generated_outline(raw, "课程")

    assert result.course_title == "课程"
    assert result.scenes[0]["type"] == "slide"
    assert result.scenes[0]["order"] == 1


def test_generated_outline_keeps_array_widget_outline() -> None:
    raw = json.dumps(
        {
            "courseTitle": "数组配置",
            "outlines": [
                {
                    "type": "interactive",
                    "title": "分类树",
                    "description": "展示分类节点。",
                    "widgetType": "simulation",
                    "widgetOutline": ["展示库分类节点", "数据处理"],
                }
            ],
        },
        ensure_ascii=False,
    )

    result = normalize_generated_outline(raw, "课程")

    assert result.scenes[0]["widget_outline"] == ["展示库分类节点", "数据处理"]


def test_generated_outline_rejects_invalid_json() -> None:
    try:
        normalize_generated_outline("not json", "课程")
    except OutlineParseError as exc:
        assert "valid JSON" in str(exc)
    else:
        raise AssertionError("invalid json should fail")


def test_manual_outline_repairs_duplicate_ids_and_order() -> None:
    result = normalize_manual_outline(
        "手工课程",
        "",
        [
            OutlineSceneInput(
                id="same",
                type="slide",
                title="第一场",
                description="",
                key_points=["A", "A"],
                order=9,
            ),
            OutlineSceneInput(
                id="same",
                type="quiz",
                title="第二场",
                description="",
                key_points=["B"],
            ),
        ],
    )

    assert result.course_title == "手工课程"
    assert result.scenes[0]["id"] == "same"
    assert result.scenes[1]["id"] == "scene-2"
    assert result.scenes[0]["key_points"] == ["A"]
    assert [scene["order"] for scene in result.scenes] == [1, 2]


async def test_generate_get_regenerate_and_manual_save(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client)
    course_id = await create_course(client, headers)
    await upload_material(
        client,
        headers,
        course_id,
        "极限描述函数在某点附近的变化趋势。MATERIAL-MARKER-9384",
    )
    prompts: List[str] = []

    async def fake_call(
        self: OutlineService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        prompts.append(user_prompt)
        assert resolved.spec.id == "llama-local"
        return outline_json()

    monkeypatch.setattr(OutlineService, "_call_provider", fake_call)

    generated = await client.post(
        f"{API}/courses/{course_id}/outline/generate",
        headers=headers,
        json={"scene_count": 3, "instructions": "强调直观理解"},
    )
    assert generated.status_code == 201, generated.text
    created_job = generated.json()
    assert created_job["kind"] == "outline"
    assert created_job["status"] == "queued"

    job = await job_state(client, headers, created_job["id"])
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert job["outline_id"]

    fetched = await client.get(f"{API}/courses/{course_id}/outline", headers=headers)
    assert fetched.status_code == 200
    first = fetched.json()
    assert first["id"] == job["outline_id"]
    assert first["version"] == 1
    assert first["source"] == "generated"
    assert first["provider_id"] == "llama-local"
    assert first["model_name"] == settings.llama_model
    assert first["scene_count"] == 2
    assert first["material_asset_count"] == 1
    assert first["material_char_count"] > 0
    assert first["materials_truncated"] is False
    assert "MATERIAL-MARKER-9384" in prompts[0]

    regenerated = await client.post(
        f"{API}/courses/{course_id}/outline/generate",
        headers=headers,
        json={},
    )
    assert regenerated.status_code == 201
    regenerated_job = await job_state(client, headers, regenerated.json()["id"])
    assert regenerated_job["status"] == "completed"
    assert regenerated_job["outline_id"] != first["id"]
    latest = await client.get(f"{API}/courses/{course_id}/outline", headers=headers)
    assert latest.json()["version"] == 2

    saved = await client.put(
        f"{API}/courses/{course_id}/outline",
        headers=headers,
        json={
            "course_title": "修订后的极限课",
            "language_directive": "全部使用简体中文。",
            "scenes": [
                {
                    "type": "quiz",
                    "title": "检查理解",
                    "description": "完成一道概念题。",
                    "key_points": ["极限定义", "极限定义"],
                    "order": 99,
                },
                {
                    "type": "slide",
                    "title": "总结",
                    "description": "回顾本课。",
                    "key_points": ["总结"],
                },
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    manual = saved.json()
    assert manual["version"] == 3
    assert manual["source"] == "manual"
    assert manual["provider_id"] is None
    assert manual["scenes"][0]["id"] == "scene-1"
    assert manual["scenes"][0]["order"] == 1
    assert manual["scenes"][0]["key_points"] == ["极限定义"]

    latest = await client.get(f"{API}/courses/{course_id}/outline", headers=headers)
    assert latest.json()["version"] == 3

    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count(CourseOutline.id)).where(
                CourseOutline.course_id == course_id
            )
        )
    assert count == 3


async def test_default_provider_is_used(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client, "default-provider-outline@example.com")
    course_id = await create_course(client, headers)
    await client.put(
        f"{API}/providers/openai/config",
        headers=headers,
        json={
            "api_key": "sk-outline-provider-1234",
            "default_model": "gpt-4o-mini",
            "is_default": True,
        },
    )

    async def fake_call(
        self: OutlineService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        assert resolved.spec.id == "openai"
        assert resolved.default_model == "gpt-4o-mini"
        return outline_json()

    monkeypatch.setattr(OutlineService, "_call_provider", fake_call)

    response = await client.post(
        f"{API}/courses/{course_id}/outline/generate",
        headers=headers,
        json={},
    )

    assert response.status_code == 201, response.text
    assert response.json()["provider_id"] == "openai"


async def test_generation_retries_invalid_model_output(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client, "retry-outline@example.com")
    course_id = await create_course(client, headers)
    attempts = 0

    async def fake_call(
        self: OutlineService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return "not valid json"
        assert "previous response could not be used" in user_prompt
        return outline_json()

    monkeypatch.setattr(OutlineService, "_call_provider", fake_call)

    response = await client.post(
        f"{API}/courses/{course_id}/outline/generate",
        headers=headers,
        json={},
    )

    assert response.status_code == 201, response.text
    assert attempts == 2


async def test_generation_fails_after_two_invalid_outputs(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client, "invalid-outline@example.com")
    course_id = await create_course(client, headers)

    async def fake_call(
        self: OutlineService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return "still not json"

    monkeypatch.setattr(OutlineService, "_call_provider", fake_call)

    response = await client.post(
        f"{API}/courses/{course_id}/outline/generate",
        headers=headers,
        json={},
    )

    assert response.status_code == 201, response.text
    failed = await job_state(client, headers, response.json()["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "OUTLINE_INVALID_RESPONSE"


async def test_outline_local_provider_disables_thinking(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    class FakeResult:
        content = '{"ok": true}'
        response_metadata = {"finish_reason": "stop"}

    class FakeChat:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def ainvoke(self, _messages: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr("app.services.outlines.ChatOpenAI", FakeChat)
    spec = get_provider_spec("llama-local")
    assert spec is not None
    service = OutlineService(None, settings)  # type: ignore[arg-type]

    raw = await service._call_openai_compatible(
        ResolvedProvider(
            spec=spec,
            config=None,
            api_key=None,
            api_key_source=None,
            base_url="http://127.0.0.1:65535/v1",
            default_model="Qwen3.6-35B-A3B",
        ),
        OUTLINE_SYSTEM_PROMPT,
        "user",
    )

    assert raw == '{"ok": true}'
    assert captured["model_kwargs"]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


async def test_outline_with_array_widget_outline_can_be_read(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client, "array-widget-outline@example.com")
    course_id = await create_course(client, headers)

    async def fake_call(
        self: OutlineService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return json.dumps(
            {
                "courseTitle": "数组配置",
                "outlines": [
                    {
                        "type": "interactive",
                        "title": "分类树",
                        "description": "展示分类节点。",
                        "widgetType": "simulation",
                        "widgetOutline": ["展示库分类节点", "数据处理"],
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(OutlineService, "_call_provider", fake_call)

    generated = await client.post(
        f"{API}/courses/{course_id}/outline/generate",
        headers=headers,
        json={},
    )
    assert generated.status_code == 201, generated.text
    job = await job_state(client, headers, generated.json()["id"])
    assert job["status"] == "completed"

    fetched = await client.get(f"{API}/courses/{course_id}/outline", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["scenes"][0]["widget_outline"] == [
        "展示库分类节点",
        "数据处理",
    ]


async def test_material_limit_sets_truncation_flag(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client, "truncate-outline@example.com")
    course_id = await create_course(client, headers)
    await upload_material(client, headers, course_id, "A" * 5000)
    monkeypatch.setattr(settings, "outline_material_char_limit", 1000)

    async def fake_call(
        self: OutlineService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return outline_json()

    monkeypatch.setattr(OutlineService, "_call_provider", fake_call)

    response = await client.post(
        f"{API}/courses/{course_id}/outline/generate",
        headers=headers,
        json={},
    )

    assert response.status_code == 201, response.text
    assert (await job_state(client, headers, response.json()["id"]))[
        "status"
    ] == "completed"
    outline = await client.get(f"{API}/courses/{course_id}/outline", headers=headers)
    body = outline.json()
    assert body["materials_truncated"] is True
    assert body["material_char_count"] <= 1000


async def test_manual_save_without_previous_outline(client: AsyncClient) -> None:
    headers = await register_user(client, "manual-outline@example.com")
    course_id = await create_course(client, headers, title="手工课程")

    response = await client.put(
        f"{API}/courses/{course_id}/outline",
        headers=headers,
        json={
            "scenes": [
                {
                    "type": "slide",
                    "title": "开场",
                    "description": "介绍课程。",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 1
    assert body["course_title"] == "手工课程"
    assert body["source"] == "manual"


async def test_outlines_are_owner_scoped(client: AsyncClient) -> None:
    first_headers = await register_user(client, "outline-owner@example.com")
    second_headers = await register_user(client, "outline-other@example.com")
    course_id = await create_course(client, first_headers)
    await client.put(
        f"{API}/courses/{course_id}/outline",
        headers=first_headers,
        json={
            "scenes": [
                {"type": "slide", "title": "私有大纲", "description": "仅本人可见"}
            ]
        },
    )

    hidden = await client.get(
        f"{API}/courses/{course_id}/outline", headers=second_headers
    )

    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "COURSE_NOT_FOUND"


async def test_missing_outline_and_course(client: AsyncClient) -> None:
    headers = await register_user(client, "missing-outline@example.com")
    course_id = await create_course(client, headers)

    missing = await client.get(f"{API}/courses/{course_id}/outline", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "OUTLINE_NOT_FOUND"

    unknown = await client.get(
        f"{API}/courses/00000000-0000-0000-0000-000000000000/outline",
        headers=headers,
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "COURSE_NOT_FOUND"


async def test_manual_save_rejects_empty_or_unknown_scenes(client: AsyncClient) -> None:
    headers = await register_user(client, "invalid-manual@example.com")
    course_id = await create_course(client, headers)

    empty = await client.put(
        f"{API}/courses/{course_id}/outline",
        headers=headers,
        json={"scenes": []},
    )
    assert empty.status_code == 422

    unknown_type = await client.put(
        f"{API}/courses/{course_id}/outline",
        headers=headers,
        json={"scenes": [{"type": "video", "title": "x"}]},
    )
    assert unknown_type.status_code == 422

from typing import Any, Dict, List

from httpx import AsyncClient
from sqlalchemy import select
from support import register_and_verify
from test_classroom import classroom_json, create_course, save_outline, upload_material

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.chat import CourseChatMessage
from app.services.chat import ChatService
from app.services.classrooms import ClassroomService
from app.services.llm import LLMProviderError

API = settings.api_v1_prefix


async def login(client: AsyncClient, email: str = "chat@example.com") -> Dict[str, str]:
    response = await register_and_verify(client, email, display_name="Chat")
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def build_classroom(
    client: AsyncClient,
    headers: Dict[str, str],
    monkeypatch: Any,
) -> str:
    async def fake_call(self: Any, resolved: Any, system_prompt: str, user_prompt: str) -> str:
        return classroom_json()

    monkeypatch.setattr(ClassroomService, "_call_provider", fake_call)
    course_id = await create_course(client, headers)
    await save_outline(client, headers, course_id)
    generated = await client.post(
        f"{API}/courses/{course_id}/classroom/generate", headers=headers, json={}
    )
    assert generated.status_code == 201, generated.text
    return course_id


async def test_chat_requires_a_generated_classroom(client: AsyncClient) -> None:
    headers = await login(client, "chat-no-classroom@example.com")
    course_id = await create_course(client, headers)

    response = await client.get(f"{API}/courses/{course_id}/chat", headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CHAT_CLASSROOM_REQUIRED"


async def test_chat_answers_and_persists_history(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await login(client, "chat-history@example.com")
    course_id = await build_classroom(client, headers, monkeypatch)
    answers = ["FIRST-ANSWER", "SECOND-ANSWER"]
    captured: List[str] = []

    async def fake_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        assert resolved.spec.id == "llama-local"
        captured.append(user_prompt)
        return answers[len(captured) - 1]

    monkeypatch.setattr(ChatService, "_call_provider", fake_provider)

    first = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "What is a limit? QUESTION-MARKER-ONE"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["reply"]["content"] == "FIRST-ANSWER"
    assert body["reply"]["role"] == "assistant"
    assert body["question"]["content"].endswith("QUESTION-MARKER-ONE")
    assert body["reply"]["provider_id"] == "llama-local"
    assert body["reply"]["stage_index"] == 1
    assert body["remaining_today"] == settings.chat_daily_message_limit - 1

    assert "QUESTION-MARKER-ONE" in captured[0]
    assert "<outline>" in captured[0]
    assert "(the learner is not on a specific scene)" in captured[0]

    second = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "And what about continuity?"},
    )
    assert second.status_code == 200, second.text
    assert "Learner: What is a limit? QUESTION-MARKER-ONE" in captured[1]
    assert "Teacher: FIRST-ANSWER" in captured[1]

    thread = await client.get(f"{API}/courses/{course_id}/chat", headers=headers)
    assert thread.status_code == 200, thread.text
    payload = thread.json()
    assert [item["role"] for item in payload["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert payload["daily_used"] == 2
    assert payload["session_id"] == body["session_id"]


async def test_chat_uses_current_scene_and_optional_materials(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await login(client, "chat-scene@example.com")
    course_id = await build_classroom(client, headers, monkeypatch)
    await upload_material(client, headers, course_id, "MATERIAL-MARKER-9182")
    captured: List[str] = []

    async def fake_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        captured.append(user_prompt)
        return "SCENE-ANSWER"

    monkeypatch.setattr(ChatService, "_call_provider", fake_provider)

    classroom = await client.get(f"{API}/courses/{course_id}/classroom", headers=headers)
    scenes = classroom.json()["scenes"]
    scene = scenes[1]

    response = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={
            "message": "Explain this scene",
            "stage_id": classroom.json()["id"],
            "scene_id": scene["id"],
            "include_materials": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"]["scene_id"] == scene["id"]
    assert body["reply"]["scene_title"] == scene["title"]
    assert body["reply"]["stage_title"] == classroom.json()["name"]

    prompt = captured[0]
    assert "Stage 1:" in prompt
    assert scene["title"] in prompt
    assert "MATERIAL-MARKER-9182" in prompt

    stale = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "Still there?", "scene_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert stale.status_code == 200, stale.text
    assert stale.json()["reply"]["scene_id"] is None
    assert "MATERIAL-MARKER-9182" not in captured[1]


async def test_chat_clear_removes_history(client: AsyncClient, monkeypatch: Any) -> None:
    headers = await login(client, "chat-clear@example.com")
    course_id = await build_classroom(client, headers, monkeypatch)

    async def fake_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        return "CLEARABLE"

    monkeypatch.setattr(ChatService, "_call_provider", fake_provider)

    sent = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "Hello teacher"},
    )
    assert sent.status_code == 200, sent.text

    cleared = await client.delete(f"{API}/courses/{course_id}/chat", headers=headers)
    assert cleared.status_code == 204

    thread = await client.get(f"{API}/courses/{course_id}/chat", headers=headers)
    assert thread.status_code == 200, thread.text
    payload = thread.json()
    assert payload["session_id"] is not None
    assert payload["messages"] == []
    assert payload["daily_used"] == 1


async def test_chat_is_owner_scoped(client: AsyncClient, monkeypatch: Any) -> None:
    owner = await login(client, "chat-owner@example.com")
    other = await login(client, "chat-other@example.com")
    course_id = await build_classroom(client, owner, monkeypatch)

    async def fake_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        return "OWNER-ONLY"

    monkeypatch.setattr(ChatService, "_call_provider", fake_provider)

    read = await client.get(f"{API}/courses/{course_id}/chat", headers=other)
    assert read.status_code == 404
    write = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=other,
        json={"message": "Can I read this?"},
    )
    assert write.status_code == 404
    cleared = await client.delete(f"{API}/courses/{course_id}/chat", headers=other)
    assert cleared.status_code == 404


async def test_chat_enforces_daily_limit(client: AsyncClient, monkeypatch: Any) -> None:
    headers = await login(client, "chat-limit@example.com")
    course_id = await build_classroom(client, headers, monkeypatch)

    async def fake_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        return "LIMITED"

    monkeypatch.setattr(ChatService, "_call_provider", fake_provider)
    monkeypatch.setattr(settings, "chat_daily_message_limit", 1)

    first = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "First"},
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "Second"},
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "CHAT_DAILY_LIMIT_REACHED"


async def test_chat_retries_empty_answer_once(client: AsyncClient, monkeypatch: Any) -> None:
    headers = await login(client, "chat-empty@example.com")
    course_id = await build_classroom(client, headers, monkeypatch)
    calls: List[str] = []

    async def flaky_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        calls.append(resolved.spec.id)
        return "" if len(calls) == 1 else "RECOVERED"

    monkeypatch.setattr(ChatService, "_call_provider", flaky_provider)

    response = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "Answer me"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["reply"]["content"] == "RECOVERED"
    assert calls == ["llama-local", "llama-local"]

    async def empty_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        calls.append(resolved.spec.id)
        return ""

    monkeypatch.setattr(ChatService, "_call_provider", empty_provider)
    failed = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "Answer me again"},
    )
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "CHAT_EMPTY_RESPONSE"
    assert calls == ["llama-local", "llama-local", "llama-local", "llama-local"]


async def test_chat_falls_back_when_local_provider_unreachable(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await login(client, "chat-fallback@example.com")
    course_id = await build_classroom(client, headers, monkeypatch)
    await client.put(
        f"{API}/providers/openai/config",
        headers=headers,
        json={"api_key": "sk-chat-fallback-1234", "default_model": "gpt-4o-mini"},
    )
    attempts: List[str] = []

    async def fake_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        attempts.append(resolved.spec.id)
        if resolved.spec.id == "llama-local":
            raise LLMProviderError("Model provider request failed")
        return "FALLBACK-ANSWER"

    monkeypatch.setattr(ChatService, "_call_provider", fake_provider)

    response = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "Use the fallback"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["reply"]["content"] == "FALLBACK-ANSWER"
    assert response.json()["reply"]["provider_id"] == "openai"
    assert attempts == ["llama-local", "openai"]


async def test_chat_message_validation(client: AsyncClient, monkeypatch: Any) -> None:
    headers = await login(client, "chat-validation@example.com")
    course_id = await build_classroom(client, headers, monkeypatch)

    async def fake_provider(self: ChatService, resolved: Any, user_prompt: str) -> str:
        return "PONG"

    monkeypatch.setattr(ChatService, "_call_provider", fake_provider)

    blank = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "   "},
    )
    assert blank.status_code == 422

    too_long = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "x" * (settings.chat_max_message_chars + 1)},
    )
    assert too_long.status_code == 422

    messy = await client.post(
        f"{API}/courses/{course_id}/chat/messages",
        headers=headers,
        json={"message": "hello\u0000world"},
    )
    assert messy.status_code == 200, messy.text
    assert messy.json()["question"]["content"] == "helloworld"

    async with AsyncSessionLocal() as session:
        stored = await session.scalars(select(CourseChatMessage))
        assert "helloworld" in [item.content for item in stored.all()]

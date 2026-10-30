import struct
from typing import Any, Dict, List
from uuid import UUID, uuid4

from httpx import AsyncClient
from support import register_and_verify

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.classroom import CourseScene, CourseStage
from app.models.provider_config import ProviderConfig
from app.services.provider_registry import get_provider_spec
from app.services.providers import (
    ProviderConfigService,
    ResolvedProvider,
    _qwen_tts_base_url,
)
from app.services.tts import (
    SynthesizedAudio,
    _dashscope_generation_url,
    _synthesize_edge_tts,
    synthesize_speech,
)

API = settings.api_v1_prefix


def wav_bytes(seconds: float = 1.0) -> bytes:
    sample_rate = 8000
    channels = 1
    width = 2
    data_size = int(sample_rate * channels * width * seconds)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH",
            16,
            1,
            channels,
            sample_rate,
            sample_rate * channels * width,
            channels * width,
            width * 8,
        )
        + b"data"
        + struct.pack("<I", data_size)
    )
    return header + b"\x00" * data_size


async def register_user(
    client: AsyncClient, email: str = "narration@example.com"
) -> tuple:
    response = await register_and_verify(client, email, display_name="Narrator")
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, UUID(body["user"]["id"])


async def create_course(client: AsyncClient, headers: Dict[str, str]) -> str:
    response = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": "旁白测试课程", "description": "TTS 集成测试"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def seed_classroom(
    owner_id: UUID,
    course_id: str,
    texts: List[str],
) -> tuple:
    async with AsyncSessionLocal() as db:
        stage = CourseStage(
            owner_id=owner_id,
            course_id=UUID(course_id),
            version=1,
            name="旁白课堂",
            description="",
            style="",
            language_directive="用简体中文教学。",
            scene_count=len(texts),
        )
        db.add(stage)
        await db.flush()
        scenes = []
        for index, text in enumerate(texts, start=1):
            scene = CourseScene(
                owner_id=owner_id,
                stage_id=stage.id,
                scene_type="slide",
                title=f"场景 {index}",
                order_index=index,
                content={"type": "slide", "schemaVersion": 1},
                actions=[
                    {
                        "id": f"scene-{index}-speech",
                        "type": "speech",
                        "text": text,
                        "speed": 1.0,
                    }
                ],
            )
            scenes.append(scene)
            db.add(scene)
        await db.commit()
        return str(stage.id), [str(scene.id) for scene in scenes]


async def configure_qwen(client: AsyncClient, headers: Dict[str, str]) -> None:
    response = await client.put(
        f"{API}/providers/qwen/config",
        headers=headers,
        json={"api_key": "sk-qwen-test-12345678"},
    )
    assert response.status_code == 200, response.text


def install_fake_tts(monkeypatch: Any, calls: List[Dict[str, Any]]) -> None:
    async def fake_synthesize(
        resolved: ResolvedProvider,
        text: str,
        *,
        speed: float,
        timeout: float,
        max_bytes: int,
    ) -> SynthesizedAudio:
        calls.append(
            {
                "provider": resolved.spec.id,
                "model": resolved.tts_model,
                "voice": resolved.tts_voice,
                "text": text,
                "speed": speed,
            }
        )
        data = wav_bytes()
        return SynthesizedAudio(
            data=data, format="wav", mime_type="audio/wav", duration_ms=1000
        )

    monkeypatch.setattr("app.services.narrations.synthesize_speech", fake_synthesize)


async def job_state(client: AsyncClient, headers: Dict[str, str], job_id: str) -> dict:
    response = await client.get(f"{API}/generation/jobs/{job_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_narration_generates_audio_and_serves_it(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client)
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id, ["第一段旁白内容。", "第二段旁白内容。"])
    await configure_qwen(client, headers)
    calls: List[Dict[str, Any]] = []
    install_fake_tts(monkeypatch, calls)

    created = await client.post(
        f"{API}/courses/{course_id}/classroom/narration", headers=headers, json={}
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["kind"] == "narration"
    assert job["status"] == "queued"
    assert job["provider_id"] == "qwen"
    assert job["model_name"] == "qwen3-tts-flash"

    finished = await job_state(client, headers, job["id"])
    assert finished["status"] == "completed", finished
    assert finished["scene_count"] == 2
    assert len(calls) == 2
    assert calls[0]["voice"] == "Cherry"
    assert calls[0]["model"] == "qwen3-tts-flash"

    classroom = await client.get(
        f"{API}/courses/{course_id}/classroom", headers=headers
    )
    assert classroom.status_code == 200, classroom.text
    scenes = classroom.json()["scenes"]
    speech = scenes[0]["actions"][0]
    assert speech["type"] == "speech"
    assert speech["audioFormat"] == "wav"
    assert speech["audioDurationMs"] == 1000
    media_id = speech["audioId"]

    detail = await client.get(f"{API}/media/{media_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["kind"] == "audio"
    assert body["mime_type"] == "audio/wav"
    assert body["duration_ms"] == 1000
    assert body["voice"] == "Cherry"
    assert body["scene_id"] == scenes[0]["id"]
    assert body["content_url"] == f"{API}/media/{media_id}/content"

    content = await client.get(f"{API}/media/{media_id}/content", headers=headers)
    assert content.status_code == 200, content.text
    assert content.headers["content-type"].startswith("audio/wav")
    assert content.headers["etag"]
    assert content.content == wav_bytes()

    other_headers, _ = await register_user(client, "intruder@example.com")
    hidden = await client.get(f"{API}/media/{media_id}", headers=other_headers)
    assert hidden.status_code == 404
    hidden_content = await client.get(
        f"{API}/media/{media_id}/content", headers=other_headers
    )
    assert hidden_content.status_code == 404


async def test_narration_cache_and_scene_regeneration(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client, "cache@example.com")
    course_id = await create_course(client, headers)
    stage_id, scene_ids = await seed_classroom(
        owner_id, course_id, ["重复的旁白文本。", "重复的旁白文本。"]
    )
    await configure_qwen(client, headers)
    calls: List[Dict[str, Any]] = []
    install_fake_tts(monkeypatch, calls)

    first = await client.post(
        f"{API}/courses/{course_id}/classroom/narration", headers=headers, json={}
    )
    assert first.status_code == 202, first.text
    job = await job_state(client, headers, first.json()["id"])
    assert job["status"] == "completed", job
    assert job["scene_count"] == 2
    assert len(calls) == 1, "identical narration should reuse cached audio"

    repeated = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[0]}/narration",
        headers=headers,
        json={},
    )
    assert repeated.status_code == 202, repeated.text
    repeated_job = await job_state(client, headers, repeated.json()["id"])
    assert repeated_job["status"] == "completed", repeated_job
    assert repeated_job["scene_count"] == 1
    assert len(calls) == 1, "unchanged scene should not hit the provider"

    changed = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[0]}/narration",
        headers=headers,
        json={"voice": "Serena"},
    )
    assert changed.status_code == 202, changed.text
    changed_job = await job_state(client, headers, changed.json()["id"])
    assert changed_job["status"] == "completed", changed_job
    assert len(calls) == 2
    assert calls[-1]["voice"] == "Serena"

    classroom = await client.get(
        f"{API}/courses/{course_id}/classroom", headers=headers
    )
    scene = classroom.json()["scenes"][0]
    detail = await client.get(
        f"{API}/media/{scene['actions'][0]['audioId']}", headers=headers
    )
    assert detail.json()["voice"] == "Serena"
    assert detail.json()["action_id"] == "scene-1-speech"
    second = await client.get(
        f"{API}/media/{classroom.json()['scenes'][1]['actions'][0]['audioId']}",
        headers=headers,
    )
    assert second.json()["voice"] == "Cherry"
    assert classroom.json()["id"] == stage_id


async def test_narration_rejects_provider_without_tts(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "no-tts@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id, ["旁白。"])

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/narration",
        headers=headers,
        json={"provider_id": "deepseek"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "TTS_NOT_SUPPORTED"


async def test_edge_narration_needs_no_api_key(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client, "edge@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id, ["Edge TTS 旁白内容。"])
    calls: List[Dict[str, Any]] = []
    install_fake_tts(monkeypatch, calls)
    monkeypatch.setattr(settings, "tts_provider_id", "edge-tts")

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/narration",
        headers=headers,
        json={},
    )
    assert response.status_code == 202, response.text
    job = await job_state(client, headers, response.json()["id"])
    assert job["status"] == "completed", job
    assert job["provider_id"] == "edge-tts"
    assert job["model_name"] == "edge-tts"
    assert calls[0]["voice"] == "zh-CN-XiaoxiaoNeural"


async def test_edge_narration_rejects_unknown_voice(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "edge-voice@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id, ["Edge TTS 旁白内容。"])

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/narration",
        headers=headers,
        json={"provider_id": "edge-tts", "voice": "not-a-voice"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TTS_VOICE_NOT_SUPPORTED"


async def test_narration_requires_classroom(client: AsyncClient) -> None:
    headers, _ = await register_user(client, "empty@example.com")
    course_id = await create_course(client, headers)

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/narration", headers=headers, json={}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CLASSROOM_NOT_FOUND"


async def test_tts_routing_prefers_dashscope_native_for_qwen(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_dashscope(**kwargs: Any) -> SynthesizedAudio:
        captured.update(kwargs)
        return SynthesizedAudio(b"audio", "mp3", "audio/mpeg", None)

    monkeypatch.setattr("app.services.tts._synthesize_dashscope", fake_dashscope)
    spec = get_provider_spec("qwen")
    assert spec is not None
    resolved = ResolvedProvider(
        spec=spec,
        config=None,
        api_key="sk-test",
        api_key_source="user",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        tts_base_url="https://dashscope.aliyuncs.com",
        tts_model="qwen3-tts-flash",
        tts_voice="Cherry",
    )

    audio = await synthesize_speech(
        resolved, "你好，欢迎上课。", speed=1.0, timeout=5.0, max_bytes=1024
    )

    assert audio.format == "mp3"
    assert captured["model"] == "qwen3-tts-flash"
    assert captured["voice"] == "Cherry"
    assert captured["base_url"] == "https://dashscope.aliyuncs.com"


def test_qwen_maas_compatible_base_derives_native_tts_base() -> None:
    assert (
        _qwen_tts_base_url(
            "https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )
        == "https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/api/v1"
    )
    assert _qwen_tts_base_url("https://ai.offer.us.ci/v1") == "https://ai.offer.us.ci/v1"
    assert (
        _dashscope_generation_url(
            "https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/api/v1"
        )
        == "https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )

    spec = get_provider_spec("qwen")
    assert spec is not None
    config = ProviderConfig(
        owner_id=uuid4(),
        provider_id="qwen",
        base_url="https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        default_model="qwen3.8-omni-flash",
    )
    resolved = ProviderConfigService(None, settings)._resolve(spec, config)
    assert (
        resolved.tts_base_url
        == "https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/api/v1"
    )


async def test_tts_routing_uses_native_for_maas_workspace(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_dashscope(**kwargs: Any) -> SynthesizedAudio:
        captured.update(kwargs)
        return SynthesizedAudio(b"audio", "mp3", "audio/mpeg", None)

    monkeypatch.setattr("app.services.tts._synthesize_dashscope", fake_dashscope)
    spec = get_provider_spec("qwen")
    assert spec is not None
    resolved = ResolvedProvider(
        spec=spec,
        config=None,
        api_key="sk-test",
        api_key_source="user",
        base_url="https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        default_model="qwen3.8-omni-flash",
        tts_base_url="https://ws-we1zc8l516uvnhti.cn-beijing.maas.aliyuncs.com/api/v1",
        tts_model="qwen3-tts-flash",
        tts_voice="Cherry",
    )

    await synthesize_speech(
        resolved, "同学们好，欢迎来到今天的课堂。", speed=1.0, timeout=5.0, max_bytes=1024
    )

    assert captured["base_url"] == resolved.tts_base_url
    assert captured["model"] == "qwen3-tts-flash"


async def test_tts_routing_uses_openai_speech_for_gateway(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_openai(**kwargs: Any) -> SynthesizedAudio:
        captured.update(kwargs)
        return SynthesizedAudio(b"audio", "mp3", "audio/mpeg", None)

    monkeypatch.setattr(
        "app.services.tts._synthesize_openai_compatible", fake_openai
    )
    spec = get_provider_spec("qwen")
    assert spec is not None
    resolved = ResolvedProvider(
        spec=spec,
        config=None,
        api_key="sk-test",
        api_key_source="user",
        base_url="https://ai.offer.us.ci/v1",
        default_model="qwen-plus",
        tts_base_url="https://ai.offer.us.ci/v1",
        tts_model="qwen3-tts-flash",
        tts_voice="Cherry",
    )

    await synthesize_speech(
        resolved, "你好", speed=1.25, timeout=5.0, max_bytes=1024
    )

    assert captured["base_url"] == "https://ai.offer.us.ci/v1"
    assert captured["model"] == "qwen3-tts-flash"
    assert captured["speed"] == 1.25
    assert captured["response_format"] == "mp3"


async def test_edge_tts_collects_audio_and_maps_speed(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, **kwargs: Any) -> None:
            captured.update({"text": text, "voice": voice, **kwargs})

        async def stream(self) -> Any:
            yield {"type": "SentenceBoundary", "data": b""}
            yield {"type": "audio", "data": b"mp3-audio"}

    monkeypatch.setattr("app.services.tts.edge_tts.Communicate", FakeCommunicate)

    audio = await _synthesize_edge_tts(
        voice="zh-CN-XiaoxiaoNeural",
        text="同学们好",
        speed=1.2,
        timeout=5.0,
        max_bytes=1024,
    )

    assert audio.data == b"mp3-audio"
    assert audio.format == "mp3"
    assert captured["voice"] == "zh-CN-XiaoxiaoNeural"
    assert captured["rate"] == "+20%"

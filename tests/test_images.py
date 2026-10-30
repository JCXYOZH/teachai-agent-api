import base64
import io
from typing import Any, Dict, List, Tuple
from uuid import UUID

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from support import register_and_verify

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import AsyncSessionLocal
from app.models.classroom import CourseScene, CourseStage
from app.models.media import MediaAsset
from app.services.images import (
    GeneratedImage,
    _cloudflare_image_url,
    _content_hash,
    _decode_data_uri,
    _image_bytes_from_string,
    _pollinations_url,
    _qwen_image_generation_url,
    _qwen_image_source,
    _validate_download_url,
    normalize_image,
)
from app.services.providers import ResolvedProvider

API = settings.api_v1_prefix


def webp_bytes(color: Tuple[int, int, int] = (22, 54, 43)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(output, format="WEBP")
    return output.getvalue()


async def register_user(
    client: AsyncClient, email: str = "illustration@example.com"
) -> Tuple[Dict[str, str], str]:
    response = await register_and_verify(client, email, display_name="Illustrator")
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def create_course(client: AsyncClient, headers: Dict[str, str]) -> str:
    response = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": "配图测试课程", "description": "图片生成集成测试"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def configure_qwen(client: AsyncClient, headers: Dict[str, str]) -> None:
    response = await client.put(
        f"{API}/providers/qwen/config",
        headers=headers,
        json={"api_key": "sk-qwen-image-12345678"},
    )
    assert response.status_code == 200, response.text


async def seed_classroom(
    owner_id: str, course_id: str, slide_count: int = 2
) -> Tuple[str, List[str]]:
    async with AsyncSessionLocal() as db:
        stage = CourseStage(
            owner_id=UUID(owner_id),
            course_id=UUID(course_id),
            version=1,
            name="配图课堂",
            description="",
            style="",
            language_directive="用简体中文教学。",
            scene_count=slide_count,
        )
        db.add(stage)
        await db.flush()
        scenes: List[CourseScene] = []
        for index in range(1, slide_count + 1):
            scene_id = f"scene-{index}"
            scene = CourseScene(
                owner_id=UUID(owner_id),
                stage_id=stage.id,
                scene_type="slide",
                title=f"第 {index} 页主题",
                order_index=index,
                content={
                    "type": "slide",
                    "schemaVersion": 1,
                    "canvas": {
                        "id": scene_id,
                        "viewportSize": 1280,
                        "viewportRatio": 0.5625,
                        "elements": [
                            {
                                "id": f"{scene_id}-title",
                                "textType": "title",
                                "content": f"<p>第 {index} 页主题</p>",
                            },
                            {
                                "id": f"{scene_id}-bullet-1",
                                "textType": "content",
                                "content": f"<p>第 {index} 页的教学要点</p>",
                            },
                        ],
                    },
                },
                actions=[],
            )
            scenes.append(scene)
            db.add(scene)
        await db.commit()
        return str(stage.id), [str(scene.id) for scene in scenes]


def install_fake_images(
    monkeypatch: Any, calls: List[Dict[str, Any]], *, fail_qwen: bool = False
) -> None:
    async def fake_qwen(
        resolved: ResolvedProvider, **kwargs: Any
    ) -> GeneratedImage:
        calls.append({"provider": "qwen", **kwargs})
        if fail_qwen:
            raise AppError(502, "IMAGE_PROVIDER_ERROR", "qwen unavailable")
        return GeneratedImage(
            data=webp_bytes(),
            width=320,
            height=180,
            provider_id="qwen",
            model=str(kwargs["model"]),
        )

    async def fake_pollinations(**kwargs: Any) -> GeneratedImage:
        calls.append({"provider": "pollinations", **kwargs})
        return GeneratedImage(
            data=webp_bytes((58, 43, 35)),
            width=320,
            height=180,
            provider_id="pollinations",
            model=str(kwargs["model"]),
        )

    monkeypatch.setattr("app.services.images.generate_qwen_image", fake_qwen)
    monkeypatch.setattr(
        "app.services.images.generate_pollinations_image", fake_pollinations
    )


async def job_state(
    client: AsyncClient, headers: Dict[str, str], job_id: str
) -> dict:
    response = await client.get(f"{API}/generation/jobs/{job_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_qwen_illustration_and_authenticated_media(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client)
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id, 2)
    await configure_qwen(client, headers)
    calls: List[Dict[str, Any]] = []
    install_fake_images(monkeypatch, calls)

    created = await client.post(
        f"{API}/courses/{course_id}/classroom/illustrations",
        headers=headers,
        json={},
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["kind"] == "image"
    assert job["provider_id"] == "qwen"
    assert job["model_name"] == "qwen-image-3.0"

    finished = await job_state(client, headers, job["id"])
    assert finished["status"] == "completed", finished
    assert finished["scene_count"] == 2
    assert [call["provider"] for call in calls] == ["qwen", "qwen"]
    assert all(call["model"] == "qwen-image-3.0" for call in calls)

    classroom = await client.get(
        f"{API}/courses/{course_id}/classroom", headers=headers
    )
    assert classroom.status_code == 200, classroom.text
    scenes = classroom.json()["scenes"]
    illustration = scenes[0]["content"]["illustration"]
    assert illustration["providerId"] == "qwen"
    assert illustration["format"] == "webp"
    assert illustration["width"] == 320
    assert illustration["alt"] == "第 1 页主题"

    media_id = illustration["mediaId"]
    detail = await client.get(f"{API}/media/{media_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["kind"] == "image"
    assert detail.json()["mime_type"] == "image/webp"

    content = await client.get(f"{API}/media/{media_id}/content", headers=headers)
    assert content.status_code == 200, content.text
    assert content.headers["content-type"].startswith("image/webp")
    assert content.content.startswith(b"RIFF")

    other_headers, _ = await register_user(client, "image-intruder@example.com")
    hidden = await client.get(
        f"{API}/media/{media_id}/content", headers=other_headers
    )
    assert hidden.status_code == 404


async def test_qwen_failure_falls_back_to_pollinations(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client, "fallback@example.com")
    course_id = await create_course(client, headers)
    stage_id, scene_ids = await seed_classroom(owner_id, course_id, 1)
    await configure_qwen(client, headers)
    calls: List[Dict[str, Any]] = []
    install_fake_images(monkeypatch, calls, fail_qwen=True)

    created = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[0]}/illustration",
        headers=headers,
        json={},
    )
    assert created.status_code == 202, created.text
    finished = await job_state(client, headers, created.json()["id"])
    assert finished["status"] == "completed", finished
    assert finished["provider_id"] == "pollinations"
    assert [call["provider"] for call in calls] == ["qwen", "pollinations"]

    classroom = await client.get(
        f"{API}/courses/{course_id}/classroom", headers=headers
    )
    illustration = classroom.json()["scenes"][0]["content"]["illustration"]
    assert illustration["providerId"] == "pollinations"

    async with AsyncSessionLocal() as db:
        asset = await db.scalar(
            select(MediaAsset).where(
                MediaAsset.id == UUID(illustration["mediaId"]),
                MediaAsset.stage_id == UUID(stage_id),
            )
        )
        assert asset is not None
        assert asset.provider_id == "pollinations"
        assert asset.media_metadata["width"] == 320
        assert "url" not in asset.media_metadata
        assert "http" not in str(asset.media_metadata)


async def test_illustration_cache_regeneration_and_classroom_limit(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client, "image-cache@example.com")
    course_id = await create_course(client, headers)
    _, scene_ids = await seed_classroom(owner_id, course_id, 8)
    await configure_qwen(client, headers)
    calls: List[Dict[str, Any]] = []
    install_fake_images(monkeypatch, calls)

    first = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[0]}/illustration",
        headers=headers,
        json={},
    )
    assert first.status_code == 202, first.text
    assert (await job_state(client, headers, first.json()["id"]))["status"] == "completed"
    assert len(calls) == 1

    repeated = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[0]}/illustration",
        headers=headers,
        json={},
    )
    assert repeated.status_code == 202, repeated.text
    assert (await job_state(client, headers, repeated.json()["id"]))["status"] == "completed"
    assert len(calls) == 1, "unchanged illustration should reuse the stored asset"

    regenerated = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[0]}/illustration",
        headers=headers,
        json={"regenerate": True},
    )
    assert regenerated.status_code == 202, regenerated.text
    assert (await job_state(client, headers, regenerated.json()["id"]))["status"] == "completed"
    assert len(calls) == 2

    all_slides = await client.post(
        f"{API}/courses/{course_id}/classroom/illustrations",
        headers=headers,
        json={},
    )
    assert all_slides.status_code == 202, all_slides.text
    all_slides_job = await job_state(client, headers, all_slides.json()["id"])
    assert all_slides_job["status"] == "completed", all_slides_job
    assert all_slides_job["scene_count"] == 5
    assert len(calls) == 7, (
        "the illustrated scene is skipped and the remaining five pending "
        "scenes are generated to reach the six image limit"
    )

    beyond_limit = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[6]}/illustration",
        headers=headers,
        json={},
    )
    assert beyond_limit.status_code == 422, beyond_limit.text
    assert beyond_limit.json()["error"]["code"] == "IMAGE_LIMIT_REACHED"


async def test_image_normalization_and_provider_helpers() -> None:
    source = io.BytesIO()
    Image.new("RGBA", (2400, 1200), (20, 40, 30, 255)).save(source, format="PNG")
    data, width, height = await normalize_image(
        source.getvalue(), max_width=1600, max_height=900, quality=82
    )
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "WEBP"
    assert (width, height) == (1600, 800)
    assert decoded.size == (1600, 800)
    assert not decoded.getexif()

    assert (
        _qwen_image_generation_url(
            "https://ws-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )
        == "https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1"
        "/services/aigc/multimodal-generation/generation"
    )
    assert _qwen_image_source(
        {
            "output": {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "image", "image": "https://example.com/a.png"}
                            ]
                        }
                    }
                ]
            }
        }
    ) == "https://example.com/a.png"

    url = _pollinations_url(
        base_url="https://image.pollinations.ai/prompt",
        prompt="深墨绿教学插画",
        model="flux",
        width=1024,
        height=576,
        seed=42,
    )
    assert url.startswith("https://image.pollinations.ai/prompt/")
    assert "width=1024" in url
    assert "height=576" in url
    assert "model=flux" in url
    assert "seed=42" in url
    assert "nologo=true" in url

    assert _content_hash(
        provider_id="qwen",
        model="qwen-image-3.0",
        prompt="主题",
        width=1024,
        height=576,
    ) == _content_hash(
        provider_id="qwen",
        model="qwen-image-3.0",
        prompt="主题",
        width=1024,
        height=576,
    )

async def test_remaining_quota_is_respected_for_whole_classroom(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client, "image-quota@example.com")
    course_id = await create_course(client, headers)
    _, scene_ids = await seed_classroom(owner_id, course_id, 8)
    await configure_qwen(client, headers)
    calls: List[Dict[str, Any]] = []
    install_fake_images(monkeypatch, calls)

    for scene_id in scene_ids[:3]:
        response = await client.post(
            f"{API}/courses/{course_id}/classroom/scenes/{scene_id}/illustration",
            headers=headers,
            json={},
        )
        assert response.status_code == 202, response.text
        state = await job_state(client, headers, response.json()["id"])
        assert state["status"] == "completed", state
    assert len(calls) == 3

    rest = await client.post(
        f"{API}/courses/{course_id}/classroom/illustrations",
        headers=headers,
        json={},
    )
    assert rest.status_code == 202, rest.text
    job = await job_state(client, headers, rest.json()["id"])
    assert job["status"] == "completed", job
    assert job["scene_count"] == 3, "only the three remaining slots may be filled"
    assert len(calls) == 6

    again = await client.post(
        f"{API}/courses/{course_id}/classroom/illustrations",
        headers=headers,
        json={},
    )
    assert again.status_code == 422, again.text
    assert again.json()["error"]["code"] == "IMAGE_LIMIT_REACHED"


async def test_non_slide_scene_is_not_illustrated(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "image-quiz@example.com")
    course_id = await create_course(client, headers)
    stage_id, _ = await seed_classroom(owner_id, course_id, 1)
    async with AsyncSessionLocal() as db:
        quiz = CourseScene(
            owner_id=UUID(owner_id),
            stage_id=UUID(stage_id),
            scene_type="quiz",
            title="课堂小测",
            order_index=2,
            content={"type": "quiz", "schemaVersion": 1, "questions": []},
            actions=[],
        )
        db.add(quiz)
        await db.commit()
        await db.refresh(quiz)
        quiz_id = str(quiz.id)

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{quiz_id}/illustration",
        headers=headers,
        json={},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "IMAGE_SCENE_NOT_FOUND"


async def test_fallback_chain_walks_providers_in_order(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers, owner_id = await register_user(client, "image-chain@example.com")
    course_id = await create_course(client, headers)
    _, scene_ids = await seed_classroom(owner_id, course_id, 1)
    await configure_qwen(client, headers)
    calls: List[str] = []

    async def failing_qwen(
        resolved: ResolvedProvider, **kwargs: Any
    ) -> GeneratedImage:
        calls.append("qwen")
        raise AppError(502, "IMAGE_PROVIDER_ERROR", "qwen unavailable")

    async def failing_pollinations(**kwargs: Any) -> GeneratedImage:
        calls.append("pollinations")
        raise AppError(502, "IMAGE_PROVIDER_ERROR", "pollinations unavailable")

    async def fake_huggingface(**kwargs: Any) -> GeneratedImage:
        calls.append("huggingface")
        assert kwargs["token"] == "hf-test-token"
        return GeneratedImage(
            data=webp_bytes((40, 60, 50)),
            width=320,
            height=180,
            provider_id="huggingface",
            model=str(kwargs["model"]),
        )

    monkeypatch.setattr("app.services.images.generate_qwen_image", failing_qwen)
    monkeypatch.setattr(
        "app.services.images.generate_pollinations_image", failing_pollinations
    )
    monkeypatch.setattr(
        "app.services.images.generate_huggingface_image", fake_huggingface
    )
    monkeypatch.setattr(settings, "image_fallback_chain", "pollinations,huggingface")
    monkeypatch.setattr(settings, "image_hf_token", "hf-test-token")

    created = await client.post(
        f"{API}/courses/{course_id}/classroom/scenes/{scene_ids[0]}/illustration",
        headers=headers,
        json={},
    )
    assert created.status_code == 202, created.text
    finished = await job_state(client, headers, created.json()["id"])
    assert finished["status"] == "completed", finished
    assert finished["provider_id"] == "huggingface"
    assert calls == ["qwen", "pollinations", "huggingface"]


async def test_private_download_urls_are_rejected() -> None:
    blocked = (
        "http://127.0.0.1/a.png",
        "http://10.0.0.5/a.png",
        "http://169.254.10.1/a.png",
        "http://[::1]/a.png",
        "file:///etc/passwd",
        "https://user:pass@example.com/a.png",
    )
    for url in blocked:
        with pytest.raises(AppError) as error:
            await _validate_download_url(url)
        assert error.value.code == "IMAGE_DOWNLOAD_NOT_ALLOWED"


def test_data_uri_and_base64_images_are_decoded() -> None:
    payload = webp_bytes()
    encoded = base64.b64encode(payload).decode("ascii")
    data_uri = f"data:image/webp;base64,{encoded}"
    assert _decode_data_uri(data_uri) == payload
    assert _image_bytes_from_string(encoded) == payload
    assert (
        _qwen_image_source(
            {
                "output": {
                    "choices": [
                        {"message": {"content": [{"type": "image", "image": data_uri}]}}
                    ]
                }
            }
        )
        == data_uri
    )
    with pytest.raises(AppError):
        _decode_data_uri("data:text/plain;base64,Zm9v")
    with pytest.raises(AppError):
        _image_bytes_from_string("not base64!")


def test_cloudflare_image_url_requires_safe_ids() -> None:
    prefix = "https://api.cloudflare.com/client/v4/accounts/abc123/ai/run/"
    assert _cloudflare_image_url("abc123", "flux-1-schnell") == (
        prefix + "@cf/flux-1-schnell"
    )
    assert _cloudflare_image_url(
        "abc123", "@cf/black-forest-labs/flux-1-schnell"
    ) == (prefix + "@cf/black-forest-labs/flux-1-schnell")
    with pytest.raises(AppError):
        _cloudflare_image_url("../evil", "flux-1-schnell")
    with pytest.raises(AppError):
        _cloudflare_image_url("abc123", "@cf/../../evil")
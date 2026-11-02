import io
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Tuple
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn
from support import register_and_verify

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.classroom import CourseScene, CourseStage
from app.models.media import MediaAsset
from app.services.exporters import html_blocks, render_export
from app.services.media_storage import LocalMediaStorage

API = settings.api_v1_prefix


async def register_user(
    client: AsyncClient, email: str = "export@example.com"
) -> Tuple[Dict[str, str], str]:
    response = await register_and_verify(client, email, display_name="Exporter")
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def create_course(client: AsyncClient, headers: Dict[str, str]) -> str:
    response = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": "导出测试课程", "description": "课堂导出集成测试"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def slide_content(scene_id: str, index: int) -> Dict[str, Any]:
    return {
        "type": "slide",
        "schemaVersion": 1,
        "canvas": {
            "id": scene_id,
            "viewportSize": 1280,
            "viewportRatio": 0.5625,
            "theme": {"backgroundColor": "#0B1714", "fontColor": "#EDF4E9"},
            "background": {"type": "solid", "color": "#0B1714"},
            "elements": [
                {
                    "id": f"{scene_id}-title",
                    "left": 90,
                    "top": 112,
                    "width": 1100,
                    "height": 150,
                    "rotate": 0,
                    "type": "text",
                    "textType": "title",
                    "content": (
                        '<p style="margin:0;font-size:54px;font-weight:600;'
                        'color:#F1ECDD;">第 '
                        f"{index} 页标题</p>"
                    ),
                },
                {
                    "id": f"{scene_id}-bullet-1",
                    "left": 90,
                    "top": 300,
                    "width": 1100,
                    "height": 60,
                    "rotate": 0,
                    "type": "text",
                    "textType": "content",
                    "content": '<p style="margin:0;font-size:30px;color:#D9E3D2;">要点一</p>',
                },
            ],
        },
    }


def quiz_content(scene_id: str) -> Dict[str, Any]:
    return {
        "type": "quiz",
        "questions": [
            {
                "id": f"{scene_id}-q1",
                "type": "single",
                "question": "下列哪一项属于监督学习？",
                "options": [
                    {"label": "聚类", "value": "A"},
                    {"label": "分类", "value": "B"},
                ],
                "answer": ["B"],
                "analysis": "分类需要标签，属于监督学习。",
                "hasAnswer": True,
                "points": 1,
            }
        ],
    }


def pbl_content(scene_id: str) -> Dict[str, Any]:
    return {
        "type": "pbl",
        "project": {
            "title": "搭建一个图像分类小项目",
            "description": "从数据到上线的完整实践",
            "learningObjective": "掌握数据标注与训练流程",
            "finalDeliverable": "可运行模型",
            "milestones": [
                {
                    "id": f"{scene_id}-m1",
                    "title": "阶段一：准备数据",
                    "description": None,
                    "deliverable": "数据集说明",
                    "microtasks": [
                        {
                            "id": f"{scene_id}-m1-t1",
                            "title": "收集 200 张图片",
                            "description": None,
                            "completionCriteria": "至少 200 张",
                            "hints": [],
                            "status": "todo",
                        }
                    ],
                }
            ],
        },
    }


async def seed_classroom(owner_id: str, course_id: str) -> Tuple[str, List[str]]:
    async with AsyncSessionLocal() as db:
        stage = CourseStage(
            owner_id=UUID(owner_id),
            course_id=UUID(course_id),
            version=1,
            name="导出课堂",
            description="用于导出测试的课堂",
            style="",
            language_directive="用简体中文教学。",
            scene_count=3,
        )
        db.add(stage)
        await db.flush()
        specs = [
            ("slide", "第 1 页标题", slide_content("scene-1", 1)),
            ("quiz", "随堂练习", quiz_content("scene-2")),
            ("pbl", "项目实践", pbl_content("scene-3")),
        ]
        scene_ids: List[str] = []
        for index, (scene_type, title, content) in enumerate(specs, start=1):
            scene = CourseScene(
                owner_id=UUID(owner_id),
                stage_id=stage.id,
                scene_type=scene_type,
                title=title,
                order_index=index,
                content=content,
                actions=[
                    {"id": f"scene-{index}-speech", "type": "speech", "text": f"讲解 {index}"}
                ],
            )
            db.add(scene)
            await db.flush()
            scene_ids.append(str(scene.id))
        await db.commit()
        return str(stage.id), scene_ids


async def job_state(client: AsyncClient, headers: Dict[str, str], job_id: str) -> dict:
    response = await client.get(f"{API}/generation/jobs/{job_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def run_export(
    client: AsyncClient, headers: Dict[str, str], course_id: str, payload: Dict[str, Any]
) -> dict:
    created = await client.post(
        f"{API}/courses/{course_id}/classroom/export",
        headers=headers,
        json=payload,
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["kind"] == "export"
    finished = await job_state(client, headers, job["id"])
    assert finished["status"] == "completed", finished
    return finished


async def test_export_pptx_and_html_and_reuse(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client)
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id)

    pptx_job = await run_export(client, headers, course_id, {"format": "pptx"})
    assert pptx_job["scene_count"] == 3

    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    assert listing.status_code == 200, listing.text
    exports = listing.json()["exports"]
    assert [item["format"] for item in exports] == ["pptx"]
    pptx = exports[0]
    assert pptx["filename"].endswith(".pptx")
    assert pptx["mime_type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml"
    )
    assert pptx["size_bytes"] > 1000
    assert pptx["scene_count"] == 3
    assert pptx["content_url"] == f"{API}/media/{pptx['media_id']}/content"

    content = await client.get(
        f"{API}/media/{pptx['media_id']}/content", headers=headers
    )
    assert content.status_code == 200, content.text
    assert content.content[:2] == b"PK"
    disposition = content.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert ".pptx" in disposition

    await run_export(client, headers, course_id, {"format": "html"})
    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    formats = {item["format"]: item for item in listing.json()["exports"]}
    assert set(formats) == {"pptx", "html"}
    html_export = formats["html"]
    assert html_export["filename"].endswith(".html")
    html_content = await client.get(
        f"{API}/media/{html_export['media_id']}/content", headers=headers
    )
    assert html_content.status_code == 200, html_content.text
    text = html_content.content.decode("utf-8")
    assert text.lstrip().startswith("<!doctype html>")
    assert "第 1 页标题" in text
    assert "下列哪一项属于监督学习" in text

    await run_export(client, headers, course_id, {"format": "pptx"})
    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    refreshed = {item["format"]: item for item in listing.json()["exports"]}
    assert set(refreshed) == {"pptx", "html"}
    assert refreshed["pptx"]["media_id"] == pptx["media_id"]


async def test_export_requires_classroom(client: AsyncClient) -> None:
    headers, _ = await register_user(client, "export-empty@example.com")
    course_id = await create_course(client, headers)

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/export",
        headers=headers,
        json={"format": "pptx"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "CLASSROOM_NOT_FOUND"

    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    assert listing.status_code == 404


async def test_export_is_scoped_to_owner(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-owner@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id)
    await run_export(client, headers, course_id, {"format": "html"})

    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    media_id = listing.json()["exports"][0]["media_id"]

    intruder_headers, _ = await register_user(client, "export-intruder@example.com")
    blocked_listing = await client.get(
        f"{API}/courses/{course_id}/classroom/exports", headers=intruder_headers
    )
    assert blocked_listing.status_code == 404
    blocked_content = await client.get(
        f"{API}/media/{media_id}/content", headers=intruder_headers
    )
    assert blocked_content.status_code == 404


async def test_export_rejects_unknown_format(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-format@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id)

    response = await client.post(
        f"{API}/courses/{course_id}/classroom/export",
        headers=headers,
        json={"format": "pdf"},
    )
    assert response.status_code == 422, response.text


async def test_html_blocks_extracts_paragraph_text() -> None:
    blocks = html_blocks('<p style="margin:0">甲</p><p>乙<br>丙</p>')
    assert blocks == ["甲", "乙", "丙"]


def test_render_export_rejects_unknown_format() -> None:
    from app.core.errors import AppError
    from app.services.exporters import ExportDocument

    document = ExportDocument(
        title="t",
        description="",
        stage_name="s",
        version=1,
        language_directive="",
        dsl_version="0.13.0",
        generated_at=datetime(2026, 1, 1),
        scenes=[],
    )
    with pytest.raises(AppError) as captured:
        render_export(document, "pdf", settings)
    assert captured.value.code == "EXPORT_FORMAT_UNSUPPORTED"

def webp_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), (22, 54, 43)).save(buffer, format="WEBP")
    return buffer.getvalue()


async def attach_illustration(course_id: str, owner_id: str, stage_id: str, scene_id: str) -> str:
    media_id = uuid4()
    storage = LocalMediaStorage(settings)
    saved = await storage.save_bytes(
        UUID(owner_id), media_id, "illustration", "webp", webp_bytes()
    )
    async with AsyncSessionLocal() as db:
        scene = await db.get(CourseScene, UUID(scene_id))
        assert scene is not None
        db.add(
            MediaAsset(
                id=media_id,
                owner_id=UUID(owner_id),
                course_id=UUID(course_id),
                stage_id=UUID(stage_id),
                scene_id=UUID(scene_id),
                action_id="illustration",
                kind="image",
                provider_id="qwen",
                model_name="qwen-image-3.0",
                format="webp",
                mime_type="image/webp",
                size_bytes=saved.size_bytes,
                sha256=saved.sha256,
                content_hash=saved.sha256,
                storage_key=saved.storage_key,
                media_metadata={"width": 320, "height": 180},
            )
        )
        content = dict(scene.content)
        content["illustration"] = {
            "mediaId": str(media_id),
            "format": "webp",
            "mimeType": "image/webp",
            "width": 320,
            "height": 180,
            "providerId": "qwen",
            "model": "qwen-image-3.0",
            "alt": scene.title,
        }
        scene.content = content
        await db.commit()
    return str(media_id)


async def download_export(
    client: AsyncClient, headers: Dict[str, str], media_id: str
) -> bytes:
    response = await client.get(f"{API}/media/{media_id}/content", headers=headers)
    assert response.status_code == 200, response.text
    return response.content


async def test_pptx_export_mirrors_canvas_and_answers(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-pptx@example.com")
    course_id = await create_course(client, headers)
    stage_id, scene_ids = await seed_classroom(owner_id, course_id)
    await attach_illustration(course_id, owner_id, stage_id, scene_ids[0])
    await run_export(client, headers, course_id, {"format": "pptx"})

    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    media_id = listing.json()["exports"][0]["media_id"]
    data = await download_export(client, headers, media_id)

    presentation = Presentation(io.BytesIO(data))
    assert len(presentation.slides) == 5
    assert presentation.slide_width == 1280 * 9525
    assert presentation.slide_height == 720 * 9525

    slide = presentation.slides[1]
    assert any(
        shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes
    )
    title_shape = next(
        shape
        for shape in slide.shapes
        if shape.has_text_frame and "第 1 页标题" in shape.text_frame.text
    )
    assert title_shape.left == 90 * 9525
    assert title_shape.top == 112 * 9525
    assert title_shape.width == 1100 * 9525
    run = title_shape.text_frame.paragraphs[0].runs[0]
    assert run.font.size is not None
    assert run.font.size.pt == pytest.approx(40.5)
    assert run.font.bold is True
    assert str(run.font.color.rgb) == "F1ECDD"
    assert slide.notes_slide.notes_text_frame.text.startswith("讲解 1")

    appendix = presentation.slides[-1]
    appendix_text = " ".join(
        shape.text_frame.text for shape in appendix.shapes if shape.has_text_frame
    )
    assert "参考答案" in appendix_text
    assert "随堂练习" in appendix_text
    assert "答案：B" in appendix_text

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        media_files = [name for name in archive.namelist() if name.startswith("ppt/media/")]
        assert any(name.endswith((".jpeg", ".jpg")) for name in media_files)


def test_parse_rich_text_keeps_inline_styles() -> None:
    from app.services.export_pptx import parse_rich_text

    paragraphs = parse_rich_text(
        '<p style="margin:0;font-size:54px;font-weight:600;text-align:center;'
        'line-height:1.08;letter-spacing:-0.03em;color:#F1ECDD;">'
        "标题<b>加粗</b><span style=\"color:#D9583D\">强调</span></p>"
    )
    assert len(paragraphs) == 1
    paragraph = paragraphs[0]
    assert paragraph.size_px == 54
    assert paragraph.color == "F1ECDD"
    assert paragraph.bold is True
    assert paragraph.align == "center"
    assert paragraph.line_spacing == pytest.approx(1.08)
    assert paragraph.letter_spacing_em == pytest.approx(-0.03)
    assert [run.text for run in paragraph.runs] == ["标题", "加粗", "强调"]
    assert paragraph.runs[1].bold is None
    assert paragraph.runs[2].color == "D9583D"


def test_parse_rich_text_handles_line_breaks() -> None:
    from app.services.export_pptx import parse_rich_text

    paragraphs = parse_rich_text("<p>甲<br/>乙</p>")
    assert len(paragraphs) == 1
    assert [run.text for run in paragraphs[0].runs] == ["甲", "\n", "乙"]


def mp3_bytes() -> bytes:
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 480


async def attach_narration(course_id: str, owner_id: str, scene_id: str) -> str:
    media_id = uuid4()
    storage = LocalMediaStorage(settings)
    saved = await storage.save_bytes(
        UUID(owner_id), media_id, "narration", "mp3", mp3_bytes()
    )
    async with AsyncSessionLocal() as db:
        scene = await db.get(CourseScene, UUID(scene_id))
        assert scene is not None
        db.add(
            MediaAsset(
                id=media_id,
                owner_id=UUID(owner_id),
                course_id=UUID(course_id),
                stage_id=scene.stage_id,
                scene_id=UUID(scene_id),
                action_id="speech",
                kind="audio",
                provider_id="qwen",
                model_name="qwen3-tts-flash",
                format="mp3",
                mime_type="audio/mpeg",
                size_bytes=saved.size_bytes,
                sha256=saved.sha256,
                content_hash=saved.sha256,
                storage_key=saved.storage_key,
                duration_ms=1500,
            )
        )
        actions = []
        for action in scene.actions or []:
            if isinstance(action, dict) and action.get("type") == "speech":
                action = dict(action)
                action["audioId"] = str(media_id)
            actions.append(action)
        scene.actions = actions
        await db.commit()
    return str(media_id)


async def fetch_html(client: AsyncClient, headers: Dict[str, str], course_id: str) -> str:
    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    assert listing.status_code == 200, listing.text
    media_id = {item["format"]: item for item in listing.json()["exports"]}["html"]["media_id"]
    data = await download_export(client, headers, media_id)
    return data.decode("utf-8")


async def test_html_export_inlines_media_and_navigation(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-html@example.com")
    course_id = await create_course(client, headers)
    stage_id, scene_ids = await seed_classroom(owner_id, course_id)
    await attach_illustration(course_id, owner_id, stage_id, scene_ids[0])
    await attach_narration(course_id, owner_id, scene_ids[0])
    await run_export(client, headers, course_id, {"format": "html"})
    text = await fetch_html(client, headers, course_id)

    assert "data:image/webp;base64," in text
    assert "data:audio/mpeg;base64," in text
    assert '<audio controls' in text
    assert 'id="progress"' in text
    assert 'id="counter"' in text
    assert 'class="scene-item"' in text
    assert '<section class="scene" id="scene-1"' in text
    assert text.count('<section class="scene" id="scene-') == 3
    assert "<details><summary>" in text
    assert 'aria-hidden="false"' in text
    assert "讲解 1" in text


async def test_html_export_skips_audio_when_disabled(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-html-noaudio@example.com")
    course_id = await create_course(client, headers)
    stage_id, scene_ids = await seed_classroom(owner_id, course_id)
    await attach_narration(course_id, owner_id, scene_ids[0])
    await run_export(
        client, headers, course_id, {"format": "html", "include_audio": False}
    )
    text = await fetch_html(client, headers, course_id)

    assert "data:audio/" not in text
    assert "讲解 1" in text
    assert "data:image/" not in text


async def test_html_export_skips_images_when_disabled(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-html-noimage@example.com")
    course_id = await create_course(client, headers)
    stage_id, scene_ids = await seed_classroom(owner_id, course_id)
    await attach_illustration(course_id, owner_id, stage_id, scene_ids[0])
    await run_export(
        client, headers, course_id, {"format": "html", "include_images": False}
    )
    text = await fetch_html(client, headers, course_id)

    assert "data:image/" not in text
    assert 'class="canvas"' in text


def test_sanitize_fragment_drops_active_content() -> None:
    from app.services.export_html import safe_style, sanitize_fragment

    dirty = (
        '<p onclick="steal()">正文</p>'
        "<script>alert('xss')</script>"
        '<iframe src="https://example.com"></iframe>'
        '<a href="javascript:alert(1)">链接</a>'
        '<span style="background:url(javascript:alert(1))">样式</span>'
    )
    clean = sanitize_fragment(dirty)

    assert "onclick" not in clean
    assert "xss" not in clean
    assert "<script" not in clean
    assert "<iframe" not in clean
    assert "javascript:" not in clean
    assert "url(" not in clean
    assert "正文" in clean
    assert "链接" in clean
    assert "样式" in clean
    assert safe_style("color:#D9583D;position:fixed") == "color:#D9583D"
    assert safe_style("background-image:url(x.png)") == ""


def test_render_html_omits_audio_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.export_document import (
        ExportAudio,
        ExportDocument,
        ExportScene,
    )
    from app.services.export_html import render_html

    scene = ExportScene(
        id="scene-1",
        order=1,
        scene_type="slide",
        title="Scene",
        content={"type": "slide", "canvas": {"elements": []}},
        narration="line",
        audio=ExportAudio(data=b"x" * 400, extension="mp3", mime_type="audio/mpeg"),
    )
    document = ExportDocument(
        title="t",
        description="",
        stage_name="s",
        version=1,
        language_directive="",
        dsl_version="0.13.0",
        generated_at=datetime(2026, 1, 1),
        scenes=[scene],
    )
    monkeypatch.setattr(settings, "export_max_bytes", 512)

    rendered = render_html(document, settings)
    text = rendered.data.decode("utf-8")

    assert rendered.warnings
    assert "data:audio/" not in text


def test_render_html_embeds_audio_within_budget() -> None:
    from app.services.export_document import (
        ExportAudio,
        ExportDocument,
        ExportScene,
    )
    from app.services.export_html import render_html

    scene = ExportScene(
        id="scene-1",
        order=1,
        scene_type="slide",
        title="Scene",
        content={"type": "slide", "canvas": {"elements": []}},
        narration="line",
        audio=ExportAudio(data=b"abc", extension="mp3", mime_type="audio/mpeg"),
    )
    document = ExportDocument(
        title="t",
        description="",
        stage_name="s",
        version=1,
        language_directive="",
        dsl_version="0.13.0",
        generated_at=datetime(2026, 1, 1),
        scenes=[scene],
    )

    rendered = render_html(document, settings)

    assert rendered.warnings == []
    assert "data:audio/mpeg;base64," in rendered.data.decode("utf-8")


def test_option_text_orders_key_before_label() -> None:
    from app.services.export_document import answer_display, option_text

    assert option_text({"label": "用标注样本训练分类器", "value": "B"}) == (
        "B. 用标注样本训练分类器"
    )
    assert option_text({"label": "无键的长文本选项说明"}) == "无键的长文本选项说明"
    assert option_text({"value": "C"}) == "C"
    assert (
        answer_display(
            {
                "answer": ["B"],
                "options": [
                    {"label": "聚类", "value": "A"},
                    {"label": "分类", "value": "B"},
                ],
            }
        )
        == "B（分类）"
    )
    assert answer_display({"answer": []}) == "未提供"


async def test_html_export_orders_quiz_options(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-quiz-order@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id)
    await run_export(client, headers, course_id, {"format": "html"})
    text = await fetch_html(client, headers, course_id)

    assert "<li>A. 聚类</li>" in text
    assert "<li>B. 分类</li>" in text
    assert "聚类. A" not in text
    assert "答案：B（分类）" in text


async def test_pptx_export_keeps_options_with_question(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-quiz-ppt@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id)
    await run_export(client, headers, course_id, {"format": "pptx"})

    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    media_id = listing.json()["exports"][0]["media_id"]
    data = await download_export(client, headers, media_id)
    presentation = Presentation(io.BytesIO(data))

    quiz_slide = next(
        slide
        for slide in presentation.slides
        if any(
            shape.has_text_frame and "下列哪一项属于监督学习" in shape.text_frame.text
            for shape in slide.shapes
        )
    )
    quiz_text = " ".join(
        shape.text_frame.text for shape in quiz_slide.shapes if shape.has_text_frame
    )
    assert "A. 聚类" in quiz_text
    assert "B. 分类" in quiz_text
    assert "聚类. A" not in quiz_text


def test_pptx_quiz_pagination_never_splits_a_question() -> None:
    from app.services.export_document import ExportDocument, ExportScene
    from app.services.exporters import render_export

    questions = []
    for index in range(1, 4):
        questions.append(
            {
                "id": f"q{index}",
                "type": "single",
                "question": f"第 {index} 题：下面关于过拟合与正则化的说法，哪一项最准确？",
                "options": [
                    {"label": f"选项{index}-甲：正则化会限制模型复杂度", "value": "A"},
                    {"label": f"选项{index}-乙：过拟合只发生在训练集", "value": "B"},
                    {"label": f"选项{index}-丙：正则化只影响推理速度", "value": "C"},
                    {"label": f"选项{index}-丁：过拟合无法被观测", "value": "D"},
                ],
                "answer": ["A"],
                "analysis": "正则化限制模型复杂度，从而降低过拟合。",
                "hasAnswer": True,
                "points": 1,
            }
        )
    scene = ExportScene(
        id="scene-quiz",
        order=1,
        scene_type="quiz",
        title="随堂练习",
        content={"type": "quiz", "questions": questions},
        narration="",
    )
    document = ExportDocument(
        title="t",
        description="",
        stage_name="s",
        version=1,
        language_directive="",
        dsl_version="0.13.0",
        generated_at=datetime(2026, 1, 1),
        scenes=[scene],
    )

    rendered = render_export(document, "pptx", settings)
    presentation = Presentation(io.BytesIO(rendered.data))
    slide_texts = [
        " ".join(
            shape.text_frame.text for shape in slide.shapes if shape.has_text_frame
        )
        for slide in presentation.slides
    ]

    for index in range(1, 4):
        hosts = [text for text in slide_texts if f"第 {index} 题" in text]
        assert hosts, f"question {index} missing"
        assert any(f"选项{index}-丁" in text for text in hosts), (
            f"question {index} lost its last option on the same slide"
        )


async def test_pptx_without_audio_omits_narration_notes(client: AsyncClient) -> None:
    headers, owner_id = await register_user(client, "export-no-narration@example.com")
    course_id = await create_course(client, headers)
    await seed_classroom(owner_id, course_id)
    await run_export(
        client,
        headers,
        course_id,
        {"format": "pptx", "include_audio": False},
    )

    listing = await client.get(f"{API}/courses/{course_id}/classroom/exports", headers=headers)
    media_id = listing.json()["exports"][0]["media_id"]
    data = await download_export(client, headers, media_id)
    presentation = Presentation(io.BytesIO(data))

    assert presentation.slides[1].has_notes_slide is False
    noted_slides = [slide for slide in presentation.slides if slide.has_notes_slide]
    assert len(noted_slides) == 1


def test_pptx_reveal_actions_animate_each_bullet() -> None:
    from app.services.export_document import ExportDocument, ExportScene
    from app.services.exporters import render_export

    scene_id = "scene-reveal"
    elements = [
        {
            "id": f"{scene_id}-title",
            "left": 90,
            "top": 112,
            "width": 1100,
            "height": 150,
            "type": "text",
            "content": '<p style="font-size:54px;">Reveal demo</p>',
        },
        {
            "id": f"{scene_id}-bullet-1",
            "left": 90,
            "top": 300,
            "width": 1100,
            "height": 60,
            "type": "text",
            "content": '<p style="font-size:30px;">First point</p>',
        },
        {
            "id": f"{scene_id}-bullet-2",
            "left": 90,
            "top": 380,
            "width": 1100,
            "height": 60,
            "type": "text",
            "content": '<p style="font-size:30px;">Second point</p>',
        },
    ]
    scene = ExportScene(
        id=scene_id,
        order=1,
        scene_type="slide",
        title="Reveal demo",
        content={
            "type": "slide",
            "canvas": {
                "id": scene_id,
                "theme": {"backgroundColor": "#0B1714", "fontColor": "#EDF4E9"},
                "background": {"type": "solid", "color": "#0B1714"},
                "elements": elements,
            },
        },
        narration="",
        actions=[
            {
                "id": f"{scene_id}-reveal-1",
                "type": "reveal",
                "elementId": f"{scene_id}-bullet-1",
            },
            {
                "id": f"{scene_id}-reveal-2",
                "type": "reveal",
                "elementId": f"{scene_id}-bullet-2",
            },
        ],
    )
    document = ExportDocument(
        title="t",
        description="",
        stage_name="s",
        version=1,
        language_directive="",
        dsl_version="0.13.0",
        generated_at=datetime(2026, 1, 1),
        scenes=[scene],
    )

    rendered = render_export(document, "pptx", settings)
    presentation = Presentation(io.BytesIO(rendered.data))
    slide = presentation.slides[1]
    timing = slide.element.find(qn("p:timing"))
    assert timing is not None
    assert len(timing.xpath('.//p:cTn[@nodeType="clickEffect"]')) == 2

    bullet_shapes = [
        shape
        for shape in slide.shapes
        if shape.has_text_frame
        and (
            "First point" in shape.text_frame.text
            or "Second point" in shape.text_frame.text
        )
    ]
    assert len(bullet_shapes) == 2
    targets = {int(value) for value in timing.xpath(".//p:spTgt/@spid")}
    assert targets == {shape.shape_id for shape in bullet_shapes}

    title_shape = next(
        shape
        for shape in slide.shapes
        if shape.has_text_frame and "Reveal demo" in shape.text_frame.text
    )
    assert title_shape.shape_id not in targets


def test_render_html_embeds_interactive_widget_sandbox() -> None:
    from app.services.export_document import ExportDocument, ExportScene
    from app.services.export_html import render_html

    widget = (
        "<!doctype html><html><head><title>w</title></head>"
        '<body><button onclick="alert(1)">开始</button></body></html>'
    )
    scene = ExportScene(
        id="scene-lab",
        order=1,
        scene_type="interactive",
        title="交互实验",
        content={
            "type": "interactive",
            "widgetType": "simulation",
            "prompt": "拖动滑块观察曲线",
            "html": widget,
        },
        narration="",
    )
    document = ExportDocument(
        title="t",
        description="",
        stage_name="s",
        version=1,
        language_directive="",
        dsl_version="0.13.0",
        generated_at=datetime(2026, 1, 1),
        scenes=[scene],
    )

    text = render_html(document, settings).data.decode("utf-8")

    assert 'sandbox="allow-scripts"' in text
    assert "srcdoc=" in text
    assert "Content-Security-Policy" in text
    assert "&lt;button" in text
    assert "<button onclick" not in text

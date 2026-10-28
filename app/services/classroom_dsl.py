import html
from typing import Any, Dict, List
from uuid import UUID, uuid4

from app.services.classroom_schema import ClassroomPlan

DSL_VERSION = "0.12.0"
SLIDE_WIDTH = 1280
SLIDE_HEIGHT = 720
SLIDE_RATIO = SLIDE_HEIGHT / SLIDE_WIDTH
SLIDE_BACKGROUND = "#0B1714"
SLIDE_TEXT = "#EDF4E9"
SLIDE_MUTED = "#D9E3D2"


def _text_element(
    *,
    element_id: str,
    left: int,
    top: int,
    width: int,
    height: int,
    content: str,
    text_type: str,
    color: str = SLIDE_TEXT,
) -> Dict[str, Any]:
    return {
        "id": element_id,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "rotate": 0,
        "type": "text",
        "content": content,
        "defaultFontName": "Microsoft YaHei",
        "defaultColor": color,
        "textType": text_type,
        "lineHeight": 1.4,
    }


def _slide_canvas(scene_id: str, title: str, bullets: List[str], order: int) -> Dict[str, Any]:
    kicker = f"LESSON / {order:02d}"
    bullet_html = "".join(
        f'<p style="margin:0 0 16px 0;font-size:28px;line-height:1.5;color:{SLIDE_MUTED};">• {html.escape(item)}</p>'
        for item in bullets
    )
    elements = [
        _text_element(
            element_id=f"{scene_id}-kicker",
            left=90,
            top=64,
            width=600,
            height=32,
            content=f'<p style="margin:0;font-size:15px;letter-spacing:0.18em;color:#A9BDA8;">{kicker}</p>',
            text_type="header",
            color="#A9BDA8",
        ),
        _text_element(
            element_id=f"{scene_id}-title",
            left=90,
            top=112,
            width=1100,
            height=150,
            content=(
                '<p style="margin:0;font-size:54px;font-weight:600;'
                'line-height:1.08;letter-spacing:-0.03em;color:#F1ECDD;">'
                f"{html.escape(title)}</p>"
            ),
            text_type="title",
            color="#F1ECDD",
        ),
        _text_element(
            element_id=f"{scene_id}-body",
            left=90,
            top=300,
            width=1100,
            height=300,
            content=bullet_html,
            text_type="content",
        ),
        _text_element(
            element_id=f"{scene_id}-footer",
            left=90,
            top=650,
            width=800,
            height=28,
            content='<p style="margin:0;font-size:14px;letter-spacing:0.12em;color:#6F8179;">TeachAI Classroom</p>',
            text_type="footer",
            color="#6F8179",
        ),
    ]
    return {
        "id": scene_id,
        "viewportSize": SLIDE_WIDTH,
        "viewportRatio": SLIDE_RATIO,
        "theme": {
            "backgroundColor": SLIDE_BACKGROUND,
            "themeColors": ["#D9583D", "#A9BDA8", "#C99A3D", "#EDF4E9"],
            "fontColor": SLIDE_TEXT,
            "fontName": "Microsoft YaHei",
        },
        "background": {"type": "solid", "color": SLIDE_BACKGROUND},
        "elements": elements,
        "turningMode": "fade",
        "type": "content",
        "script": " ".join(bullets),
    }


def _speech_actions(scene_id: str, narration: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"{scene_id}-speech",
            "type": "speech",
            "text": narration,
            "speed": 1.0,
        }
    ]


def compile_scenes(plan: ClassroomPlan, stage_id: UUID, language_directive: str) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []
    for order, scene in enumerate(plan.scenes, start=1):
        scene_id = str(uuid4())
        title = str(scene["title"])
        narration = str(scene.get("narration") or title)
        if scene["type"] == "quiz":
            questions = []
            for index, question in enumerate(scene.get("questions", []), start=1):
                questions.append(
                    {
                        "id": f"{scene_id}-q{index}",
                        "type": question["type"],
                        "question": question["question"],
                        "options": question.get("options", []),
                        "answer": question.get("answer", []),
                        "analysis": question.get("analysis"),
                        "hasAnswer": bool(question.get("hasAnswer")),
                        "points": 1,
                    }
                )
            content: Dict[str, Any] = {"type": "quiz", "questions": questions}
        else:
            content = {
                "type": "slide",
                "schemaVersion": 1,
                "canvas": _slide_canvas(scene_id, title, list(scene.get("bullets", [])), order),
            }
        scenes.append(
            {
                "id": scene_id,
                "stage_id": str(stage_id),
                "type": scene["type"],
                "title": title,
                "order": order,
                "language_directive": language_directive,
                "content": content,
                "actions": _speech_actions(scene_id, narration),
            }
        )
    return scenes

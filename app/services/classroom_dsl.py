import html
from typing import Any, Dict, List
from uuid import UUID, uuid4

from app.services.classroom_schema import ClassroomPlan

DSL_VERSION = "0.13.0"
SLIDE_WIDTH = 1280
SLIDE_HEIGHT = 720
SLIDE_RATIO = SLIDE_HEIGHT / SLIDE_WIDTH
SLIDE_BACKGROUND = "#0B1714"
SLIDE_TEXT = "#EDF4E9"
SLIDE_MUTED = "#D9E3D2"
BULLET_AREA_TOP = 300
BULLET_AREA_HEIGHT = 320


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
    points = bullets or [title]
    slot = max(52, min(96, BULLET_AREA_HEIGHT // max(1, len(points))))
    font_size = 30 if len(points) <= 3 else 26 if len(points) <= 5 else 22
    elements: List[Dict[str, Any]] = [
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
    ]
    for index, item in enumerate(points):
        elements.append(
            _text_element(
                element_id=f"{scene_id}-bullet-{index + 1}",
                left=90,
                top=BULLET_AREA_TOP + index * slot,
                width=1100,
                height=slot - 8,
                content=(
                    f'<p style="margin:0;font-size:{font_size}px;line-height:1.45;'
                    f'color:{SLIDE_MUTED};">• {html.escape(item)}</p>'
                ),
                text_type="content",
            )
        )
    elements.append(
        _text_element(
            element_id=f"{scene_id}-footer",
            left=90,
            top=650,
            width=800,
            height=28,
            content='<p style="margin:0;font-size:14px;letter-spacing:0.12em;color:#6F8179;">TeachAI Classroom</p>',
            text_type="footer",
            color="#6F8179",
        )
    )
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
        "script": " ".join(points),
    }


def _speech_action(scene_id: str, narration: str) -> Dict[str, Any]:
    return {
        "id": f"{scene_id}-speech",
        "type": "speech",
        "text": narration,
        "speed": 1.0,
    }


def _reveal_actions(scene_id: str, count: int) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"{scene_id}-reveal-{index}",
            "type": "reveal",
            "elementId": f"{scene_id}-bullet-{index}",
            "delay": 600,
        }
        for index in range(1, count + 1)
    ]


def _focus_action(scene_id: str) -> Dict[str, Any]:
    return {
        "id": f"{scene_id}-focus",
        "type": "highlight",
        "elementId": f"{scene_id}-stage",
        "duration": 1800,
    }


def _quiz_content(scene_id: str, scene: Dict[str, Any]) -> Dict[str, Any]:
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
    return {"type": "quiz", "questions": questions}


def _interactive_content(scene: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "interactive",
        "schemaVersion": 1,
        "widgetType": scene.get("widget_type") or "simulation",
        "prompt": scene.get("prompt"),
        "html": scene["html"],
        "frame": {"viewportWidth": SLIDE_WIDTH, "viewportHeight": SLIDE_HEIGHT},
    }


def _pbl_content(scene_id: str, project: Dict[str, Any]) -> Dict[str, Any]:
    milestones: List[Dict[str, Any]] = []
    for milestone_index, milestone in enumerate(project.get("milestones", []), start=1):
        microtasks: List[Dict[str, Any]] = []
        for task_index, task in enumerate(milestone.get("microtasks", []), start=1):
            microtasks.append(
                {
                    "id": f"{scene_id}-m{milestone_index}-t{task_index}",
                    "title": task["title"],
                    "description": task.get("description"),
                    "completionCriteria": task.get("completionCriteria"),
                    "hints": task.get("hints", []),
                    "status": "todo",
                }
            )
        milestones.append(
            {
                "id": f"{scene_id}-m{milestone_index}",
                "title": milestone["title"],
                "description": milestone.get("description"),
                "deliverable": milestone.get("deliverable"),
                "microtasks": microtasks,
            }
        )
    return {
        "type": "pbl",
        "schemaVersion": 1,
        "project": {
            "title": project.get("title") or "",
            "description": project.get("description") or "",
            "learningObjective": project.get("learningObjective"),
            "finalDeliverable": project.get("finalDeliverable"),
            "milestones": milestones,
        },
    }


def compile_scenes(plan: ClassroomPlan, stage_id: UUID, language_directive: str) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []
    for order, scene in enumerate(plan.scenes, start=1):
        scene_id = str(uuid4())
        title = str(scene["title"])
        narration = str(scene.get("narration") or title)
        actions = [_speech_action(scene_id, narration)]
        if scene["type"] == "quiz":
            content: Dict[str, Any] = _quiz_content(scene_id, scene)
        elif scene["type"] == "interactive":
            content = _interactive_content(scene)
            actions.append(_focus_action(scene_id))
        elif scene["type"] == "pbl":
            content = _pbl_content(scene_id, scene["project"])
        else:
            bullets = list(scene.get("bullets") or [title])
            content = {
                "type": "slide",
                "schemaVersion": 1,
                "canvas": _slide_canvas(scene_id, title, bullets, order),
            }
            actions.extend(_reveal_actions(scene_id, len(bullets)))
        scenes.append(
            {
                "id": scene_id,
                "stage_id": str(stage_id),
                "type": scene["type"],
                "title": title,
                "order": order,
                "language_directive": language_directive,
                "content": content,
                "actions": actions,
            }
        )
    return scenes

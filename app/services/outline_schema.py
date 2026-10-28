import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.schemas.outline import OutlineSceneInput

ALLOWED_SCENE_TYPES = {"slide", "quiz", "interactive", "pbl"}
MAX_SCENES = 100
MAX_OUTLINE_BYTES = 512_000
DEFAULT_LANGUAGE_DIRECTIVE = (
    "Use the language of the course title and reference materials consistently. "
    "Keep established technical terms in English when translation would be unclear."
)


class OutlineParseError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedOutline:
    course_title: str
    language_directive: str
    scenes: List[Dict[str, Any]]


def _clean_text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = "".join(
        character
        for character in value
        if ord(character) >= 32 or character in "\n\t"
    ).strip()
    if not normalized:
        return None
    return normalized[:limit]


def _text_list(value: Any, *, limit: int, item_limit: int = 500) -> List[str]:
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    cleaned: List[str] = []
    seen = set()
    for item in values:
        text = _clean_text(item, item_limit)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _optional_json(value: Any, max_bytes: int) -> Optional[Any]:
    if not isinstance(value, (dict, list)):
        return None
    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    if len(serialized.encode()) > max_bytes:
        return None
    return value


def _first(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _extract_json(raw_text: str) -> Any:
    text = raw_text.lstrip("\ufeff").strip()
    if not text:
        raise OutlineParseError("Model returned an empty response")

    candidates: List[str] = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    object_start, object_end = text.find("{"), text.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(text[object_start : object_end + 1])
    array_start, array_end = text.find("["), text.rfind("]")
    if array_start >= 0 and array_end > array_start:
        candidates.append(text[array_start : array_end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    raise OutlineParseError("Model response is not valid JSON")


def _duration(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = int(float(value.strip()))
        except ValueError:
            return None
    if isinstance(value, float):
        value = int(value)
    if not isinstance(value, int):
        return None
    return max(1, min(value, 240))


def _scene_type(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ALLOWED_SCENE_TYPES:
            return normalized
    return "slide"


def _normalize_scene(raw: Any, index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    description = _clean_text(_first(raw, "description"), 2000) or ""
    title = _clean_text(_first(raw, "title"), 200) or _clean_text(description, 120)
    if not title:
        return None
    scene: Dict[str, Any] = {
        "id": f"scene-{index + 1}",
        "type": _scene_type(_first(raw, "type")),
        "title": title,
        "description": description,
        "key_points": _text_list(
            _first(raw, "keyPoints", "key_points"), limit=8, item_limit=500
        ),
        "order": index + 1,
    }

    optional_text = {
        "teaching_objective": _clean_text(
            _first(raw, "teachingObjective", "teaching_objective"), 1000
        ),
        "language_note": _clean_text(
            _first(raw, "languageNote", "language_note"), 500
        ),
        "widget_type": _clean_text(
            _first(raw, "widgetType", "widget_type"), 80
        ),
    }
    for key, value in optional_text.items():
        if value:
            scene[key] = value

    duration = _duration(_first(raw, "estimatedDuration", "estimated_duration"))
    if duration is not None:
        scene["estimated_duration"] = duration

    optional_json = {
        "widget_outline": _optional_json(
            _first(raw, "widgetOutline", "widget_outline"), 16_000
        ),
        "quiz_config": _optional_json(
            _first(raw, "quizConfig", "quiz_config"), 16_000
        ),
        "pbl_config": _optional_json(_first(raw, "pblConfig", "pbl_config"), 16_000),
        "interactive_config": _optional_json(
            _first(raw, "interactiveConfig", "interactive_config"), 16_000
        ),
        "media_generations": _optional_json(
            _first(raw, "mediaGenerations", "media_generations"), 24_000
        ),
    }
    for key, value in optional_json.items():
        if value is not None:
            scene[key] = value

    image_ids = _text_list(
        _first(raw, "suggestedImageIds", "suggested_image_ids"),
        limit=50,
        item_limit=100,
    )
    if image_ids:
        scene["suggested_image_ids"] = image_ids
    return scene


def normalize_generated_outline(
    raw_text: str, fallback_title: str
) -> NormalizedOutline:
    payload = _extract_json(raw_text)
    if isinstance(payload, list):
        raw_outlines = payload
        language_directive = None
        course_title = None
    elif isinstance(payload, dict):
        raw_outlines = _first(payload, "outlines", "scenes")
        language_directive = _clean_text(
            _first(payload, "languageDirective", "language_directive"), 2000
        )
        course_title = _clean_text(
            _first(payload, "courseTitle", "course_title"), 80
        )
    else:
        raise OutlineParseError("Model response must be a JSON object or array")

    if not isinstance(raw_outlines, list) or not raw_outlines:
        raise OutlineParseError("outlines must be a non-empty array")

    scenes: List[Dict[str, Any]] = []
    for index, raw_scene in enumerate(raw_outlines[:MAX_SCENES]):
        scene = _normalize_scene(raw_scene, index)
        if scene is not None:
            scenes.append(scene)
    if not scenes:
        raise OutlineParseError("No usable scene outlines were returned")

    normalized_title = (
        course_title or _clean_text(fallback_title, 80) or "Untitled course"
    )
    normalized = NormalizedOutline(
        course_title=normalized_title,
        language_directive=language_directive or DEFAULT_LANGUAGE_DIRECTIVE,
        scenes=scenes,
    )
    _ensure_size(normalized.scenes)
    return normalized


def normalize_manual_outline(
    course_title: str,
    language_directive: str,
    scenes: Sequence[OutlineSceneInput],
) -> NormalizedOutline:
    normalized_scenes: List[Dict[str, Any]] = []
    seen_ids = set()
    for index, scene in enumerate(scenes[:MAX_SCENES]):
        fallback_id = f"scene-{index + 1}"
        candidate_id = (scene.id or "").strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", candidate_id)
            or candidate_id in seen_ids
        ):
            candidate_id = fallback_id
        seen_ids.add(candidate_id)
        normalized: Dict[str, Any] = {
            "id": candidate_id,
            "type": scene.type,
            "title": scene.title,
            "description": scene.description,
            "key_points": scene.key_points,
            "order": index + 1,
        }
        optional_text = {
            "teaching_objective": scene.teaching_objective,
            "language_note": scene.language_note,
            "widget_type": scene.widget_type,
        }
        for key, value in optional_text.items():
            if value:
                normalized[key] = value
        if scene.estimated_duration is not None:
            normalized["estimated_duration"] = scene.estimated_duration
        optional_json = {
            "widget_outline": _optional_json(scene.widget_outline, 16_000),
            "quiz_config": _optional_json(scene.quiz_config, 16_000),
            "pbl_config": _optional_json(scene.pbl_config, 16_000),
            "interactive_config": _optional_json(scene.interactive_config, 16_000),
            "media_generations": _optional_json(scene.media_generations, 24_000),
        }
        for key, value in optional_json.items():
            if value is not None:
                normalized[key] = value
        if scene.suggested_image_ids:
            normalized["suggested_image_ids"] = scene.suggested_image_ids[:50]
        normalized_scenes.append(normalized)

    if not normalized_scenes:
        raise OutlineParseError("At least one scene is required")
    _ensure_size(normalized_scenes)
    return NormalizedOutline(
        course_title=_clean_text(course_title, 80) or "Untitled course",
        language_directive=(
            _clean_text(language_directive, 2000) or DEFAULT_LANGUAGE_DIRECTIVE
        ),
        scenes=normalized_scenes,
    )


def _ensure_size(scenes: List[Dict[str, Any]]) -> None:
    serialized = json.dumps(scenes, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode()) > MAX_OUTLINE_BYTES:
        raise OutlineParseError("Outline JSON exceeds the 512 KB limit")

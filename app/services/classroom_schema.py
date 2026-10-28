import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

MAX_SCENES = 24
MAX_RESPONSE_BYTES = 1_000_000


class ClassroomParseError(ValueError):
    pass


@dataclass(frozen=True)
class ClassroomPlan:
    name: str
    description: str
    style: str
    scenes: List[Dict[str, Any]]


def _clean_text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = ''.join(
        character
        for character in value
        if ord(character) >= 32 or character in '\n\t'
    ).strip()
    if not normalized:
        return None
    return normalized[:limit]


def _single_line(value: Any, limit: int) -> Optional[str]:
    text = _clean_text(value, limit)
    if text is None:
        return None
    return ' '.join(text.split())[:limit]


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
        text = _single_line(item, item_limit)
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


def _extract_json(raw_text: str) -> Any:
    text = raw_text.lstrip('\ufeff').strip()
    if not text:
        raise ClassroomParseError('Model returned an empty response')
    if len(text.encode('utf-8')) > MAX_RESPONSE_BYTES:
        raise ClassroomParseError('Model response is too large')

    candidates: List[str] = [text]
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    object_start, object_end = text.find('{'), text.rfind('}')
    if object_start >= 0 and object_end > object_start:
        candidates.append(text[object_start : object_end + 1])
    array_start, array_end = text.find('['), text.rfind(']')
    if array_start >= 0 and array_end > array_start:
        candidates.append(text[array_start : array_end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    raise ClassroomParseError('Model response is not valid JSON')


def _letter(index: int) -> str:
    return chr(65 + index) if index < 26 else str(index + 1)


def _normalize_options(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: List[Dict[str, str]] = []
    seen = set()
    for index, item in enumerate(value[:6]):
        default_value = _letter(index)
        if isinstance(item, dict):
            label = _single_line(
                item.get('label') or item.get('text') or item.get('content'),
                500,
            )
            option_value = _single_line(item.get('value') or item.get('key'), 16)
        else:
            label = _single_line(item, 500)
            option_value = None
        if not label:
            continue
        normalized_value = option_value or default_value
        if normalized_value in seen:
            continue
        seen.add(normalized_value)
        options.append({'label': label, 'value': normalized_value})
    return options


def _normalize_questions(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    questions: List[Dict[str, Any]] = []
    for raw in value[:3]:
        if not isinstance(raw, dict):
            continue
        question = _clean_text(raw.get('question') or raw.get('prompt'), 2000)
        if not question:
            continue
        options = _normalize_options(raw.get('options'))
        option_values = {option['value'] for option in options}
        label_to_value = {
            option['label'].casefold(): option['value'] for option in options
        }
        answers: List[str] = []
        for answer in _text_list(
            raw.get('answer') or raw.get('answers'), limit=6, item_limit=60
        ):
            candidate = label_to_value.get(answer.casefold(), answer)
            if candidate in option_values and candidate not in answers:
                answers.append(candidate)

        if not options:
            question_type = 'short_answer'
        elif len(answers) > 1:
            question_type = 'multiple'
        else:
            question_type = 'single'
        questions.append(
            {
                'type': question_type,
                'question': question,
                'options': options,
                'answer': answers,
                'analysis': _clean_text(raw.get('analysis'), 2000),
                'hasAnswer': bool(answers),
                'points': 1,
            }
        )
    return questions


def _slide_scene(raw: Dict[str, Any], title: str) -> Dict[str, Any]:
    bullets = _text_list(
        raw.get('bullets') or raw.get('keyPoints') or raw.get('key_points'),
        limit=6,
        item_limit=300,
    )
    if not bullets:
        bullets = _text_list(raw.get('description'), limit=2, item_limit=300)
    if not bullets:
        bullets = [title]
    narration = _clean_text(
        raw.get('narration') or raw.get('speech') or raw.get('description'),
        6000,
    )
    if not narration:
        narration = '；'.join(bullets)
    return {
        'type': 'slide',
        'title': title,
        'bullets': bullets,
        'narration': narration,
    }


def normalize_classroom_plan(raw_text: str, fallback_title: str) -> ClassroomPlan:
    payload = _extract_json(raw_text)
    if isinstance(payload, list):
        container: Dict[str, Any] = {'scenes': payload}
    elif isinstance(payload, dict):
        container = payload
    else:
        raise ClassroomParseError('Model response must be a JSON object')

    raw_scenes = container.get('scenes')
    if not isinstance(raw_scenes, list) or not raw_scenes:
        nested = container.get('classroom')
        raw_scenes = nested.get('scenes') if isinstance(nested, dict) else None
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ClassroomParseError('Model response does not contain scenes')

    name = (
        _single_line(container.get('name') or container.get('courseTitle'), 200)
        or fallback_title
    )
    description = _clean_text(container.get('description'), 2000) or ''
    style = _clean_text(container.get('style'), 2000) or (
        'Deep ink classroom with warm chalk accents and focused typography.'
    )

    scenes: List[Dict[str, Any]] = []
    for raw in raw_scenes[:MAX_SCENES]:
        if not isinstance(raw, dict):
            continue
        title = _single_line(raw.get('title') or raw.get('name'), 200)
        if not title:
            continue
        scene_type = (_single_line(raw.get('type') or raw.get('kind'), 20) or 'slide').lower()
        if scene_type == 'quiz':
            questions = _normalize_questions(raw.get('questions'))
            if not questions:
                scenes.append(_slide_scene(raw, title))
                continue
            narration = _clean_text(
                raw.get('narration') or raw.get('speech') or raw.get('description'),
                6000,
            ) or title
            scenes.append(
                {
                    'type': 'quiz',
                    'title': title,
                    'narration': narration,
                    'questions': questions,
                }
            )
        else:
            scenes.append(_slide_scene(raw, title))
        if len(scenes) >= MAX_SCENES:
            break

    if not scenes:
        raise ClassroomParseError('Model response did not contain usable scenes')
    return ClassroomPlan(
        name=name,
        description=description,
        style=style,
        scenes=scenes,
    )

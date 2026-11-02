import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
HTML_MIME = "text/html; charset=utf-8"
SUPPORTED_FORMATS = ("pptx", "html")
HEX_PATTERN = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
OPTION_KEY_PATTERN = re.compile(r"^[0-9A-Za-z]{1,2}$")
RGB_PATTERN = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", re.IGNORECASE
)
NAMED_COLORS = {
    "white": "FFFFFF",
    "black": "000000",
    "red": "FF0000",
    "transparent": None,
}
DEFAULT_BACKGROUND = "0B1714"
DEFAULT_TEXT = "EDF4E9"
DEFAULT_MUTED = "D9E3D2"
DEFAULT_TITLE = "F1ECDD"
DEFAULT_ACCENT = "D9583D"
DEFAULT_FONT = "Microsoft YaHei"
BLOCK_TAGS = {
    "p",
    "div",
    "li",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "tr",
}


@dataclass(frozen=True)
class ExportImage:
    data: bytes
    extension: str
    mime_type: str
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class ExportAudio:
    data: bytes
    extension: str
    mime_type: str
    duration_ms: Optional[int] = None


@dataclass(frozen=True)
class ExportScene:
    id: str
    order: int
    scene_type: str
    title: str
    content: Dict[str, Any]
    narration: str
    actions: List[Dict[str, Any]] = field(default_factory=list)
    image: Optional[ExportImage] = None
    audio: Optional[ExportAudio] = None


@dataclass(frozen=True)
class ExportDocument:
    title: str
    description: str
    stage_name: str
    version: int
    language_directive: str
    dsl_version: str
    generated_at: datetime
    scenes: List[ExportScene]
    include_audio: bool = True


@dataclass(frozen=True)
class RenderedExport:
    data: bytes
    extension: str
    mime_type: str
    warnings: List[str] = field(default_factory=list)


class _BlockCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[str] = []
        self._buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        self._buffer.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buffer).split())
        if text:
            self.blocks.append(text)
        self._buffer = []

    def close(self) -> None:
        super().close()
        self._flush()


def html_blocks(fragment: str) -> List[str]:
    collector = _BlockCollector()
    collector.feed(str(fragment or ""))
    collector.close()
    return collector.blocks


def scene_content(scene: ExportScene) -> Dict[str, Any]:
    return scene.content if isinstance(scene.content, dict) else {}


def option_text(option: Dict[str, Any]) -> str:
    label = str(option.get("label") or option.get("text") or "").strip()
    value = str(option.get("value") or option.get("key") or "").strip()
    if not label:
        return value
    if not value or value == label:
        return label
    label_is_key = bool(OPTION_KEY_PATTERN.match(label))
    value_is_key = bool(OPTION_KEY_PATTERN.match(value))
    if value_is_key and not label_is_key:
        return f"{value}. {label}"
    if label_is_key and not value_is_key:
        return f"{label}. {value}"
    return label


def answer_display(question: Dict[str, Any]) -> str:
    raw = question.get("answer")
    keys = [str(item) for item in raw] if isinstance(raw, list) else []
    if not keys:
        return "未提供"
    by_value: Dict[str, str] = {}
    by_label: Dict[str, str] = {}
    raw_options = question.get("options")
    if isinstance(raw_options, list):
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            value = str(option.get("value") or "").strip()
            label = str(option.get("label") or "").strip()
            if value and label:
                by_value[value] = label
                by_label[label] = value
    parts: List[str] = []
    for key in keys:
        if key in by_value:
            parts.append(f"{key}（{by_value[key]}）")
        elif key in by_label:
            parts.append(f"{key}（{by_label[key]}）")
        else:
            parts.append(key)
    return "、".join(parts)


def scene_lines(scene: ExportScene) -> List[str]:
    content = scene_content(scene)
    content_type = str(content.get("type") or scene.scene_type)
    if content_type == "slide":
        elements: List[Any] = []
        canvas = content.get("canvas")
        if isinstance(canvas, dict):
            candidate = canvas.get("elements")
            if isinstance(candidate, list):
                elements = candidate
        lines: List[str] = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            lines.extend(html_blocks(str(element.get("content") or "")))
        return lines or [scene.title]
    if content_type == "quiz":
        lines = []
        questions = content.get("questions")
        if isinstance(questions, list):
            for question in questions:
                if not isinstance(question, dict):
                    continue
                text = str(question.get("question") or "").strip()
                if text:
                    lines.append(text)
        return lines or [scene.title]
    if content_type == "pbl":
        lines = []
        project = content.get("project")
        if isinstance(project, dict):
            heading = str(project.get("title") or "").strip()
            if heading:
                lines.append(heading)
            milestones = project.get("milestones")
            if isinstance(milestones, list):
                for milestone in milestones:
                    if not isinstance(milestone, dict):
                        continue
                    title = str(milestone.get("title") or "").strip()
                    if title:
                        lines.append(title)
        return lines or [scene.title]
    prompt = str(content.get("prompt") or "").strip()
    return [prompt or scene.title]

@dataclass(frozen=True)
class Theme:
    background: str
    text: str
    muted: str
    title: str
    accent: str
    font_name: str


def as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def parse_color(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in NAMED_COLORS:
        return NAMED_COLORS[lowered]
    match = RGB_PATTERN.match(text)
    if match:
        channels = [min(255, max(0, int(part))) for part in match.groups()]
        return "".join(f"{channel:02X}" for channel in channels)
    match = HEX_PATTERN.match(text)
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)
    return digits.upper()


def canvas_theme(scene: ExportScene) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    canvas = as_dict(scene_content(scene).get("canvas"))
    return canvas, as_dict(canvas.get("theme"))


def document_theme(document: ExportDocument) -> Theme:
    for scene in document.scenes:
        canvas = as_dict(scene_content(scene).get("canvas"))
        if not canvas:
            continue
        theme = as_dict(canvas.get("theme"))
        raw_colors = theme.get("themeColors")
        colors = raw_colors if isinstance(raw_colors, list) else []
        accent = parse_color(colors[0]) if colors else None
        return Theme(
            background=parse_color(theme.get("backgroundColor")) or DEFAULT_BACKGROUND,
            text=parse_color(theme.get("fontColor")) or DEFAULT_TEXT,
            muted=DEFAULT_MUTED,
            title=DEFAULT_TITLE,
            accent=accent or DEFAULT_ACCENT,
            font_name=str(theme.get("fontName") or DEFAULT_FONT),
        )
    return Theme(
        background=DEFAULT_BACKGROUND,
        text=DEFAULT_TEXT,
        muted=DEFAULT_MUTED,
        title=DEFAULT_TITLE,
        accent=DEFAULT_ACCENT,
        font_name=DEFAULT_FONT,
    )

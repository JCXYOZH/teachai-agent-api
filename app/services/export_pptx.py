import io
import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import (
    MSO_AUTO_SIZE,
    MSO_VERTICAL_ANCHOR,
    PP_PARAGRAPH_ALIGNMENT,
)
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls, qn
from pptx.util import Emu, Pt

from app.services.export_document import (
    PPTX_MIME,
    ExportDocument,
    ExportImage,
    ExportScene,
    RenderedExport,
    Theme,
    answer_display,
    as_dict,
    document_theme,
    option_text,
    parse_color,
    scene_content,
)

PX_EMU = 9525
SLIDE_W = 1280
SLIDE_H = 720
FOOTER_COLOR = "6F8179"
CONTENT_LEFT = 90
CONTENT_WIDTH = 1100
BODY_TOP = 214
BODY_HEIGHT = 430
PAGE_LINE_CAPACITY = 12
STYLE_TAGS = {
    "p",
    "div",
    "li",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "td",
    "th",
    "tr",
}
ALIGNMENTS = {
    "left": PP_PARAGRAPH_ALIGNMENT.LEFT,
    "start": PP_PARAGRAPH_ALIGNMENT.LEFT,
    "center": PP_PARAGRAPH_ALIGNMENT.CENTER,
    "right": PP_PARAGRAPH_ALIGNMENT.RIGHT,
    "end": PP_PARAGRAPH_ALIGNMENT.RIGHT,
    "justify": PP_PARAGRAPH_ALIGNMENT.JUSTIFY,
}

@dataclass
class RunModel:
    text: str
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    color: Optional[str] = None
    size_px: Optional[float] = None


@dataclass
class ParagraphModel:
    runs: List[RunModel] = field(default_factory=list)
    align: Optional[str] = None
    line_spacing: Optional[float] = None
    line_spacing_px: Optional[float] = None
    space_before_px: float = 0.0
    space_after_px: float = 0.0
    letter_spacing_em: Optional[float] = None
    size_px: Optional[float] = None
    color: Optional[str] = None
    bold: Optional[bool] = None


def parse_style(style: str) -> Dict[str, str]:
    declarations: Dict[str, str] = {}
    for chunk in str(style or "").split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            declarations[key] = value
    return declarations


def px_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(px|pt|em|rem|%)?\s*$", str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    if unit == "pt":
        return number * 4 / 3
    if unit in {"em", "rem"}:
        return number * 16
    if unit == "%":
        return None
    return number


def em_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(em|rem|px)?\s*$", str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "em").lower()
    if unit == "px":
        return number / 16
    return number


def normalize_bold(value: Any) -> Optional[bool]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"bold", "bolder", "600", "700", "800", "900"}:
        return True
    if text in {"normal", "lighter", "400", "300", "200", "100"}:
        return False
    return None


def normalize_italic(value: Any) -> Optional[bool]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"italic", "oblique"}:
        return True
    if text == "normal":
        return False
    return None

class _RichTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[Tuple[str, Dict[str, str]]] = []
        self.paragraphs: List[ParagraphModel] = []
        self.paragraph = ParagraphModel()

    def _merged(self) -> Dict[str, str]:
        merged: Dict[str, str] = {}
        for _tag, style in self.stack:
            merged.update(style)
        return merged

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        values = {str(key): str(value or "") for key, value in attrs}
        style = parse_style(values.get("style") or "")
        if tag in STYLE_TAGS:
            self._flush()
            self.stack.append((tag, style))
            self.paragraph = ParagraphModel(
                align=style.get("text-align"),
                line_spacing=_line_spacing_ratio(style.get("line-height")),
                line_spacing_px=_line_spacing_px(style.get("line-height")),
                space_before_px=px_value(style.get("margin-top")) or 0.0,
                space_after_px=px_value(style.get("margin-bottom")) or 0.0,
                letter_spacing_em=em_value(style.get("letter-spacing")),
                size_px=px_value(style.get("font-size")),
                color=parse_color(style.get("color")),
                bold=normalize_bold(style.get("font-weight")),
            )
            return
        if tag == "br":
            self.paragraph.runs.append(RunModel(text="\n"))
            return
        if tag in {"b", "strong"}:
            self.stack.append((tag, {"font-weight": "bold"}))
            return
        if tag in {"i", "em"}:
            self.stack.append((tag, {"font-style": "italic"}))
            return
        if tag == "font":
            font_style: Dict[str, str] = {}
            if values.get("color"):
                font_style["color"] = values["color"]
            self.stack.append((tag, font_style))
            return
        if style:
            self.stack.append((tag, style))

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag == "br":
            self.paragraph.runs.append(RunModel(text="\n"))

    def handle_endtag(self, tag: str) -> None:
        if tag in STYLE_TAGS:
            self._flush()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index]
                break

    def handle_data(self, data: str) -> None:
        if not data:
            return
        merged = self._merged()
        color = parse_color(merged.get("color"))
        size_px = px_value(merged.get("font-size"))
        bold = normalize_bold(merged.get("font-weight"))
        italic = normalize_italic(merged.get("font-style"))
        self.paragraph.runs.append(
            RunModel(
                text=data,
                bold=bold if bold != self.paragraph.bold else None,
                italic=italic,
                color=color if color and color != self.paragraph.color else None,
                size_px=size_px if size_px and size_px != self.paragraph.size_px else None,
            )
        )

    def _flush(self) -> None:
        runs = [run for run in self.paragraph.runs if run.text]
        if runs:
            trimmed = _trim_runs(runs)
            if trimmed:
                self.paragraph.runs = trimmed
                self.paragraphs.append(self.paragraph)
        self.paragraph = ParagraphModel()

    def close(self) -> None:
        super().close()
        self._flush()


def _line_spacing_ratio(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(("px", "pt", "em", "rem", "%")):
        return None
    try:
        ratio = float(text)
    except ValueError:
        return None
    if 0.5 <= ratio <= 4:
        return ratio
    return None


def _line_spacing_px(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text.endswith(("px", "pt")):
        return None
    return px_value(text)


def _trim_runs(runs: Sequence[RunModel]) -> List[RunModel]:
    cleaned: List[RunModel] = []
    for run in runs:
        text = run.text.replace("\r", "")
        if text:
            cleaned.append(
                RunModel(
                    text=text,
                    bold=run.bold,
                    italic=run.italic,
                    color=run.color,
                    size_px=run.size_px,
                )
            )
    while cleaned and not cleaned[0].text.strip() and cleaned[0].text != "\n":
        cleaned.pop(0)
    while cleaned and not cleaned[-1].text.strip() and cleaned[-1].text != "\n":
        cleaned.pop()
    return cleaned


def parse_rich_text(fragment: str) -> List[ParagraphModel]:
    parser = _RichTextParser()
    parser.feed(str(fragment or ""))
    parser.close()
    return parser.paragraphs

def render_pptx(document: ExportDocument) -> RenderedExport:
    presentation = Presentation()
    presentation.slide_width = Emu(SLIDE_W * PX_EMU)
    presentation.slide_height = Emu(SLIDE_H * PX_EMU)
    layout = presentation.slide_layouts[6]
    theme = document_theme(document)
    warnings: List[str] = []

    _add_cover(presentation, layout, document, theme)
    for scene in document.scenes:
        content = scene_content(scene)
        canvas = content.get("canvas")
        if scene.scene_type == "slide" and isinstance(canvas, dict):
            _add_slide_scene(
                presentation,
                layout,
                scene,
                canvas,
                theme,
                warnings,
                include_audio=document.include_audio,
            )
        elif scene.scene_type == "quiz":
            _add_quiz_scene(
                presentation,
                layout,
                scene,
                theme,
                warnings,
                include_audio=document.include_audio,
            )
        elif scene.scene_type == "pbl":
            _add_pbl_scene(
                presentation,
                layout,
                scene,
                theme,
                warnings,
                include_audio=document.include_audio,
            )
        else:
            _add_interactive_scene(
                presentation,
                layout,
                scene,
                theme,
                warnings,
                include_audio=document.include_audio,
            )
    _add_answer_appendix(presentation, layout, document, theme)

    buffer = io.BytesIO()
    presentation.save(buffer)
    return RenderedExport(
        data=buffer.getvalue(),
        extension="pptx",
        mime_type=PPTX_MIME,
        warnings=warnings,
    )


def _emu(px: float) -> Emu:
    return Emu(int(round(px * PX_EMU)))


def _set_background(slide: Any, color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor.from_string(color)


def _set_notes(slide: Any, text: str) -> None:
    if not text:
        return
    slide.notes_slide.notes_text_frame.text = text


def _prepare_image(image: ExportImage) -> Tuple[io.BytesIO, int, int]:
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(image.data)) as source:
        converted = source.convert("RGB")
        width, height = converted.size
        if width > 1920:
            scaled = 1920 / width
            converted = converted.resize((1920, int(round(height * scaled))))
            width, height = converted.size
        buffer = io.BytesIO()
        converted.save(buffer, format="JPEG", quality=88, optimize=True)
    buffer.seek(0)
    return buffer, width, height


def _add_picture(slide: Any, image: ExportImage, warnings: List[str], index: int) -> bool:
    try:
        prepared, width, height = _prepare_image(image)
    except Exception:
        warnings.append(f"Illustration for scene {index} could not be embedded")
        return False
    target_ratio = SLIDE_W / SLIDE_H
    actual_ratio = (width / height) if height else target_ratio
    picture = slide.shapes.add_picture(
        prepared, _emu(0), _emu(0), _emu(SLIDE_W), _emu(SLIDE_H)
    )
    if actual_ratio > target_ratio:
        excess = 1 - target_ratio / actual_ratio
        picture.crop_left = excess / 2
        picture.crop_right = excess / 2
    elif actual_ratio < target_ratio:
        excess = 1 - actual_ratio / target_ratio
        picture.crop_top = excess / 2
        picture.crop_bottom = excess / 2
    return True


def _gradient_overlay(
    slide: Any, stops: Sequence[Tuple[float, str, float]], angle: float
) -> None:
    shape = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE, _emu(0), _emu(0), _emu(SLIDE_W), _emu(SLIDE_H)
    )
    shape.line.fill.background()
    fill = shape.fill
    fill.gradient()
    fill.gradient_angle = angle
    grad_fill = fill._xPr.find(qn("a:gradFill"))
    if grad_fill is None:
        return
    stop_list = grad_fill.find(qn("a:gsLst"))
    if stop_list is None:
        return
    for existing in list(stop_list):
        stop_list.remove(existing)
    for position, color, alpha in stops:
        stop = etree.SubElement(stop_list, qn("a:gs"))
        stop.set("pos", str(int(round(position * 100000))))
        color_element = etree.SubElement(stop, qn("a:srgbClr"))
        color_element.set("val", color)
        alpha_element = etree.SubElement(color_element, qn("a:alpha"))
        alpha_element.set("val", str(int(round(alpha * 100000))))

def _add_text(
    slide: Any,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    text: str,
    size_px: float,
    color: str,
    font_name: str,
    bold: bool = False,
    line_spacing: Optional[float] = None,
    letter_spacing_em: Optional[float] = None,
) -> Any:
    box = slide.shapes.add_textbox(_emu(left), _emu(top), _emu(width), _emu(height))
    frame = box.text_frame
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = MSO_VERTICAL_ANCHOR.TOP
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    paragraph = frame.paragraphs[0]
    if line_spacing:
        paragraph.line_spacing = line_spacing
    run = paragraph.add_run()
    run.text = text
    _style_run(
        run,
        font_name=font_name,
        color=color,
        size_px=size_px,
        bold=bold,
        italic=None,
        letter_spacing_em=letter_spacing_em,
    )
    return box


def _style_run(
    run: Any,
    *,
    font_name: str,
    color: str,
    size_px: float,
    bold: Optional[bool],
    italic: Optional[bool],
    letter_spacing_em: Optional[float],
) -> None:
    font = run.font
    font.size = Pt(size_px * 0.75)
    if bold is not None:
        font.bold = bold
    if italic is not None:
        font.italic = italic
    rgb = parse_color(color)
    if rgb:
        font.color.rgb = RGBColor.from_string(rgb)
    font.name = font_name
    _set_east_asian_typeface(run, font_name)
    if letter_spacing_em:
        spacing = letter_spacing_em * size_px * 0.75 * 100
        run.font._rPr.set("spc", str(int(round(spacing))))


def _set_east_asian_typeface(run: Any, font_name: str) -> None:
    r_pr = run.font._rPr
    element = r_pr.find(qn("a:ea"))
    if element is None:
        element = etree.SubElement(r_pr, qn("a:ea"))
    element.set("typeface", font_name)


def _add_footer(slide: Any, theme: Theme, label: str = "TeachAI Classroom") -> None:
    _add_text(
        slide,
        left=CONTENT_LEFT,
        top=656,
        width=800,
        height=26,
        text=label,
        size_px=14,
        color=FOOTER_COLOR,
        font_name=theme.font_name,
        letter_spacing_em=0.12,
    )


def _add_kicker(slide: Any, text: str, theme: Theme) -> None:
    _add_text(
        slide,
        left=CONTENT_LEFT,
        top=64,
        width=600,
        height=30,
        text=text,
        size_px=15,
        color=theme.muted,
        font_name=theme.font_name,
        letter_spacing_em=0.18,
    )


def _add_heading(
    slide: Any, text: str, theme: Theme, *, top: float = 108, size_px: float = 44
) -> None:
    _add_text(
        slide,
        left=CONTENT_LEFT,
        top=top,
        width=CONTENT_WIDTH,
        height=96,
        text=text,
        size_px=size_px,
        color=theme.title,
        font_name=theme.font_name,
        bold=True,
        line_spacing=1.1,
    )

def _add_cover(
    presentation: Any, layout: Any, document: ExportDocument, theme: Theme
) -> None:
    slide = presentation.slides.add_slide(layout)
    _set_background(slide, theme.background)
    _add_text(
        slide,
        left=CONTENT_LEFT,
        top=120,
        width=CONTENT_WIDTH,
        height=40,
        text="TEACHAI / CLASSROOM",
        size_px=16,
        color=theme.muted,
        font_name=theme.font_name,
        letter_spacing_em=0.18,
    )
    _add_text(
        slide,
        left=CONTENT_LEFT,
        top=190,
        width=CONTENT_WIDTH,
        height=200,
        text=document.title,
        size_px=54,
        color=theme.title,
        font_name=theme.font_name,
        bold=True,
        line_spacing=1.08,
    )
    _add_text(
        slide,
        left=CONTENT_LEFT,
        top=410,
        width=CONTENT_WIDTH,
        height=60,
        text=f"{document.stage_name} · v{document.version}",
        size_px=24,
        color=theme.muted,
        font_name=theme.font_name,
    )
    meta = (
        f"{len(document.scenes)} scenes · DSL {document.dsl_version} · "
        f"{document.generated_at:%Y-%m-%d}"
    )
    _add_text(
        slide,
        left=CONTENT_LEFT,
        top=470,
        width=CONTENT_WIDTH,
        height=40,
        text=meta,
        size_px=16,
        color=theme.accent,
        font_name=theme.font_name,
        letter_spacing_em=0.08,
    )
    if document.description:
        _add_text(
            slide,
            left=CONTENT_LEFT,
            top=540,
            width=CONTENT_WIDTH,
            height=80,
            text=document.description,
            size_px=18,
            color=theme.muted,
            font_name=theme.font_name,
            line_spacing=1.5,
        )
    _add_footer(slide, theme)


def _add_slide_scene(
    presentation: Any,
    layout: Any,
    scene: ExportScene,
    canvas: Dict[str, Any],
    theme: Theme,
    warnings: List[str],
    *,
    include_audio: bool = True,
) -> None:
    slide = presentation.slides.add_slide(layout)
    raw_background = as_dict(canvas.get("background"))
    background = parse_color(raw_background.get("color"))
    canvas_theme = as_dict(canvas.get("theme"))
    background = (
        background or parse_color(canvas_theme.get("backgroundColor")) or theme.background
    )
    _set_background(slide, background)
    if scene.image is not None and _add_picture(slide, scene.image, warnings, scene.order):
        _gradient_overlay(
            slide,
            stops=[
                (0.0, background, 0.99),
                (0.32, background, 0.95),
                (0.62, background, 0.66),
                (1.0, background, 0.24),
            ],
            angle=0.0,
        )
        _gradient_overlay(
            slide,
            stops=[
                (0.0, background, 0.4),
                (0.28, background, 0.0),
                (0.68, background, 0.0),
                (1.0, background, 0.72),
            ],
            angle=90.0,
        )
    font_name = str(canvas_theme.get("fontName") or theme.font_name)
    font_color = parse_color(canvas_theme.get("fontColor")) or theme.text
    elements = canvas.get("elements")
    element_shapes: Dict[str, int] = {}
    if isinstance(elements, list):
        for element in elements:
            if isinstance(element, dict):
                shape = _add_element_text(slide, element, font_name, font_color)
                element_id = element.get("id")
                if shape is not None and isinstance(element_id, str) and element_id:
                    element_shapes[element_id] = shape.shape_id
    _add_reveal_animations(slide, _reveal_shape_ids(scene, element_shapes))
    _set_notes(slide, scene.narration if include_audio else "")


def _add_element_text(
    slide: Any, element: Dict[str, Any], font_name: str, default_color: str
) -> Optional[Any]:
    left = px_value(element.get("left")) or 0.0
    top = px_value(element.get("top")) or 0.0
    width = px_value(element.get("width")) or 0.0
    height = px_value(element.get("height")) or 0.0
    if width <= 1 or height <= 1:
        return None
    box = slide.shapes.add_textbox(_emu(left), _emu(top), _emu(width), _emu(height))
    frame = box.text_frame
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = MSO_VERTICAL_ANCHOR.TOP
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    paragraphs = parse_rich_text(str(element.get("content") or ""))
    element_color = parse_color(element.get("defaultColor")) or default_color
    element_font = str(element.get("defaultFontName") or font_name)
    _fill_text_frame(frame, paragraphs, element_font, element_color, 20.0)
    return box


def _reveal_shape_ids(
    scene: ExportScene, element_shapes: Dict[str, int]
) -> List[int]:
    shape_ids: List[int] = []
    for action in scene.actions or []:
        if not isinstance(action, dict) or action.get("type") != "reveal":
            continue
        element_id = action.get("elementId")
        if not isinstance(element_id, str):
            continue
        shape_id = element_shapes.get(element_id)
        if shape_id is not None and shape_id not in shape_ids:
            shape_ids.append(shape_id)
    return shape_ids


def _add_reveal_animations(slide: Any, shape_ids: Sequence[int]) -> None:
    if not shape_ids:
        return
    effects: List[str] = []
    next_id = 3
    for shape_id in shape_ids:
        outer_id, inner_id, effect_id, set_id = (
            next_id,
            next_id + 1,
            next_id + 2,
            next_id + 3,
        )
        next_id += 4
        effects.append(
            f'<p:par><p:cTn id="{outer_id}" fill="hold">'
            '<p:stCondLst><p:cond delay="indefinite"/></p:stCondLst>'
            f'<p:childTnLst><p:par><p:cTn id="{inner_id}" fill="hold">'
            '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
            f'<p:childTnLst><p:par><p:cTn id="{effect_id}" presetID="1" '
            'presetClass="entr" presetSubtype="0" fill="hold" grpId="0" '
            'nodeType="clickEffect">'
            '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
            '<p:childTnLst><p:set><p:cBhvr>'
            f'<p:cTn id="{set_id}" dur="1" fill="hold">'
            '<p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>'
            f'<p:tgtEl><p:spTgt spid="{shape_id}"/></p:tgtEl>'
            '<p:attrNameLst><p:attrName>style.visibility</p:attrName>'
            '</p:attrNameLst></p:cBhvr>'
            '<p:to><p:strVal val="visible"/></p:to></p:set>'
            '</p:childTnLst></p:cTn></p:par></p:childTnLst>'
            '</p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
        )
    build_list = "".join(
        f'<p:bldP spid="{shape_id}" grpId="0"/>' for shape_id in shape_ids
    )
    timing_xml = (
        f'<p:timing {nsdecls("p", "a")}><p:tnLst><p:par>'
        '<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">'
        '<p:childTnLst><p:seq concurrent="1" nextAc="seek">'
        '<p:cTn id="2" dur="indefinite" nodeType="mainSeq">'
        f'<p:childTnLst>{"".join(effects)}</p:childTnLst>'
        '</p:cTn>'
        '<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl>'
        '<p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        '<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl>'
        '<p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>'
        '</p:seq></p:childTnLst></p:cTn></p:par></p:tnLst>'
        f'<p:bldLst>{build_list}</p:bldLst></p:timing>'
    )
    timing = parse_xml(timing_xml)
    existing = slide.element.find(qn("p:timing"))
    if existing is not None:
        slide.element.remove(existing)
    ext = slide.element.find(qn("p:extLst"))
    if ext is not None:
        ext.addprevious(timing)
    else:
        slide.element.append(timing)


def _fill_text_frame(
    frame: Any,
    paragraphs: Sequence[ParagraphModel],
    font_name: str,
    color: str,
    default_size_px: float,
) -> None:
    first = True
    for paragraph in paragraphs:
        target = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        _apply_paragraph(target, paragraph, font_name, color, default_size_px)
    if first:
        frame.paragraphs[0].add_run().text = " "


def _apply_paragraph(
    paragraph: Any,
    model: ParagraphModel,
    font_name: str,
    color: str,
    default_size_px: float,
) -> None:
    if model.align:
        paragraph.alignment = ALIGNMENTS.get(
            model.align.strip().lower(), PP_PARAGRAPH_ALIGNMENT.LEFT
        )
    if model.line_spacing is not None:
        paragraph.line_spacing = model.line_spacing
    elif model.line_spacing_px is not None:
        paragraph.line_spacing = Pt(model.line_spacing_px * 0.75)
    if model.space_before_px:
        paragraph.space_before = Pt(model.space_before_px * 0.75)
    if model.space_after_px:
        paragraph.space_after = Pt(model.space_after_px * 0.75)
    base_size = model.size_px or default_size_px
    base_color = model.color or color
    for run_model in model.runs:
        if run_model.text == "\n":
            paragraph.add_line_break()
            continue
        run = paragraph.add_run()
        run.text = run_model.text
        _style_run(
            run,
            font_name=font_name,
            color=run_model.color or base_color,
            size_px=run_model.size_px or base_size,
            bold=run_model.bold if run_model.bold is not None else model.bold,
            italic=run_model.italic,
            letter_spacing_em=model.letter_spacing_em,
        )

FlowBlock = List[Tuple[str, float, bool, str]]


def _block_cost(block: FlowBlock) -> int:
    total = 0
    for text, size_px, _bold, _color in block:
        columns = max(8, int(CONTENT_WIDTH / max(10.0, size_px * 0.95)))
        total += max(1, math.ceil(len(text) / columns))
    return max(1, total)


def _paginate(
    blocks: Sequence[FlowBlock], capacity: int = PAGE_LINE_CAPACITY
) -> List[List[FlowBlock]]:
    pages: List[List[FlowBlock]] = []
    current: List[FlowBlock] = []
    used = 0
    for block in blocks:
        cost = _block_cost(block)
        if current and used + cost > capacity:
            pages.append(current)
            current = []
            used = 0
        current.append(block)
        used += cost
    if current:
        pages.append(current)
    return pages or [[]]


def _render_flow_pages(
    presentation: Any,
    layout: Any,
    *,
    kicker: str,
    title: str,
    pages: Sequence[List[FlowBlock]],
    theme: Theme,
    narration: str,
    footer: str = "TeachAI Classroom",
) -> List[Any]:
    slides: List[Any] = []
    for index, page in enumerate(pages):
        slide = presentation.slides.add_slide(layout)
        slides.append(slide)
        _set_background(slide, theme.background)
        _add_kicker(slide, kicker, theme)
        heading = title if index == 0 else f"{title}（续）"
        _add_heading(slide, heading, theme)
        box = slide.shapes.add_textbox(
            _emu(CONTENT_LEFT), _emu(BODY_TOP), _emu(CONTENT_WIDTH), _emu(BODY_HEIGHT)
        )
        frame = box.text_frame
        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.vertical_anchor = MSO_VERTICAL_ANCHOR.TOP
        frame.margin_left = 0
        frame.margin_right = 0
        frame.margin_top = 0
        frame.margin_bottom = 0
        first_paragraph = True
        for block_index, block in enumerate(page):
            for paragraph_index, (text, size_px, bold, color) in enumerate(block):
                paragraph = (
                    frame.paragraphs[0] if first_paragraph else frame.add_paragraph()
                )
                first_paragraph = False
                paragraph.line_spacing = 1.45
                paragraph.space_after = Pt(size_px * 0.3)
                if paragraph_index == 0 and block_index > 0:
                    paragraph.space_before = Pt(size_px * 0.5)
                run = paragraph.add_run()
                run.text = text
                _style_run(
                    run,
                    font_name=theme.font_name,
                    color=color,
                    size_px=size_px,
                    bold=bold,
                    italic=None,
                    letter_spacing_em=None,
                )
        if first_paragraph:
            frame.paragraphs[0].add_run().text = " "
        _add_footer(slide, theme, footer)
        if index == 0:
            _set_notes(slide, narration)
    return slides


def _answer_lines(questions: Sequence[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for index, question in enumerate(questions, start=1):
        answer = answer_display(question)
        analysis = str(question.get("analysis") or "").strip()
        line = f"{index}. 答案：{answer}"
        if analysis:
            line += f"　解析：{analysis}"
        lines.append(line)
    return lines


def _add_quiz_scene(
    presentation: Any,
    layout: Any,
    scene: ExportScene,
    theme: Theme,
    warnings: List[str],
    *,
    include_audio: bool = True,
) -> None:
    content = scene_content(scene)
    raw_questions = content.get("questions")
    questions = (
        [item for item in raw_questions if isinstance(item, dict)]
        if isinstance(raw_questions, list)
        else []
    )
    if not questions:
        warnings.append(f"Quiz scene {scene.order} had no questions to export")
    blocks: List[FlowBlock] = []
    for index, question in enumerate(questions, start=1):
        stem = str(question.get("question") or "").strip()
        if not stem:
            continue
        item: FlowBlock = [(f"{index}. {stem}", 26.0, True, theme.title)]
        options = question.get("options")
        if isinstance(options, list):
            for option in options:
                if not isinstance(option, dict):
                    continue
                option_label = option_text(option)
                if not option_label:
                    continue
                item.append((option_label, 22.0, False, theme.muted))
        blocks.append(item)
    pages = _paginate(blocks)
    slides = _render_flow_pages(
        presentation,
        layout,
        kicker=f"CHECK / {scene.order:02d}",
        title=scene.title,
        pages=pages,
        theme=theme,
        narration=scene.narration if include_audio else "",
        footer="TeachAI Classroom · Check",
    )
    answers = _answer_lines(questions)
    if answers and slides:
        narration = scene.narration if include_audio else ""
        _set_notes(slides[0], "\n".join([narration, "", "参考答案：", *answers]).strip())

def _add_pbl_scene(
    presentation: Any,
    layout: Any,
    scene: ExportScene,
    theme: Theme,
    warnings: List[str],
    *,
    include_audio: bool = True,
) -> None:
    content = scene_content(scene)
    project = as_dict(content.get("project"))
    blocks: List[FlowBlock] = []
    description = str(project.get("description") or "").strip()
    if description:
        blocks.append([(description, 22.0, False, theme.muted)])
    objective = str(project.get("learningObjective") or "").strip()
    if objective:
        blocks.append([(f"学习目标：{objective}", 20.0, False, theme.text)])
    deliverable = str(project.get("finalDeliverable") or "").strip()
    if deliverable:
        blocks.append([(f"最终交付物：{deliverable}", 20.0, False, theme.text)])
    milestones = project.get("milestones")
    if isinstance(milestones, list):
        for index, milestone in enumerate(milestones, start=1):
            if not isinstance(milestone, dict):
                continue
            heading = str(milestone.get("title") or "").strip()
            if heading:
                blocks.append([(f"{index}. {heading}", 24.0, True, theme.title)])
            milestone_description = str(milestone.get("description") or "").strip()
            if milestone_description:
                blocks.append([(milestone_description, 20.0, False, theme.muted)])
            microtasks = milestone.get("microtasks")
            if isinstance(microtasks, list):
                for task in microtasks:
                    if not isinstance(task, dict):
                        continue
                    task_title = str(task.get("title") or "").strip()
                    if task_title:
                        blocks.append([(f"· {task_title}", 20.0, False, theme.muted)])
    if not blocks:
        blocks = [[(scene.title, 24.0, True, theme.title)]]
        warnings.append(f"Project scene {scene.order} had no project content")
    pages = _paginate(blocks)
    _render_flow_pages(
        presentation,
        layout,
        kicker=f"PROJECT / {scene.order:02d}",
        title=scene.title,
        pages=pages,
        theme=theme,
        narration=scene.narration if include_audio else "",
        footer="TeachAI Classroom · Project",
    )


def _add_interactive_scene(
    presentation: Any,
    layout: Any,
    scene: ExportScene,
    theme: Theme,
    warnings: List[str],
    *,
    include_audio: bool = True,
) -> None:
    content = scene_content(scene)
    widget = str(content.get("widgetType") or "simulation")
    prompt = str(content.get("prompt") or "").strip()
    blocks: List[FlowBlock] = []
    if prompt:
        blocks.append([(prompt, 24.0, False, theme.text)])
    blocks.append(
        [
            (
                "本场景是交互式实验，PPTX 中仅保留说明页；请在课堂网页中打开以操作完整交互。",
                20.0,
                False,
                theme.muted,
            )
        ]
    )
    pages = _paginate(blocks)
    _render_flow_pages(
        presentation,
        layout,
        kicker=f"LAB / {scene.order:02d}",
        title=f"{scene.title} · {widget}",
        pages=pages,
        theme=theme,
        narration=scene.narration if include_audio else "",
        footer="TeachAI Classroom · Lab",
    )


def _add_answer_appendix(
    presentation: Any, layout: Any, document: ExportDocument, theme: Theme
) -> None:
    blocks: List[FlowBlock] = []
    for scene in document.scenes:
        if scene.scene_type != "quiz":
            continue
        content = scene_content(scene)
        raw_questions = content.get("questions")
        questions = (
            [item for item in raw_questions if isinstance(item, dict)]
            if isinstance(raw_questions, list)
            else []
        )
        lines = _answer_lines(questions)
        if not lines:
            continue
        blocks.append([(scene.title, 24.0, True, theme.title)])
        for line in lines:
            blocks.append([(line, 20.0, False, theme.muted)])
    if not blocks:
        return
    pages = _paginate(blocks)
    _render_flow_pages(
        presentation,
        layout,
        kicker="ANSWER KEY",
        title="参考答案",
        pages=pages,
        theme=theme,
        narration="",
        footer="TeachAI Classroom · Answer key",
    )

import base64
import html
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from app.core.config import Settings
from app.services.export_document import (
    HTML_MIME,
    ExportDocument,
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

SLIDE_W = 1280
SLIDE_H = 720
TYPE_LABELS = {
    "slide": "SLIDE",
    "quiz": "CHECK",
    "interactive": "LAB",
    "pbl": "PROJECT",
}
ALLOWED_TAGS = {
    "p",
    "div",
    "span",
    "b",
    "strong",
    "i",
    "em",
    "u",
    "s",
    "br",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
}
VOID_TAGS = {"br"}
DROP_SUBTREE_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "template"}
ALLOWED_STYLES = {
    "font-size",
    "font-weight",
    "font-style",
    "font-family",
    "color",
    "background-color",
    "text-align",
    "text-decoration",
    "line-height",
    "letter-spacing",
    "margin",
    "margin-top",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "padding",
    "padding-left",
    "padding-top",
    "padding-bottom",
    "padding-right",
    "white-space",
}
UNSAFE_STYLE_MARKERS = ("expression(", "javascript:", "url(", "\\", "@import", "behavior:")
INTERACTIVE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "img-src data:; font-src data:; media-src data:; connect-src 'none'; "
    "form-action 'none'; base-uri 'none'"
)
INTERACTIVE_BASE_STYLES = (
    "html,body{margin:0;min-height:100%;background:#0B1714;color:#EDF4E9;}"
    'body{font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;}'
    "*,*::before,*::after{box-sizing:border-box;}"
)
HEAD_PATTERN = re.compile(r"<head[^>]*>", re.IGNORECASE)
HTML_PATTERN = re.compile(r"<html[^>]*>", re.IGNORECASE)

STYLES = """
:root{color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0}
body{background:radial-gradient(circle at 50% -12%,rgba(169,189,168,.1),transparent 55%),#07110e;color:#EDF4E9;
  font-family:"Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.app{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}
.rail{position:sticky;top:0;height:100vh;overflow:auto;padding:22px 18px 28px;
  border-right:1px solid rgba(237,244,233,.08)}
.kicker{margin:0;font-size:.6rem;letter-spacing:.2em;text-transform:uppercase;color:rgba(237,244,233,.35)}
.rail-title{margin:.6rem 0 0;font-size:1.12rem;line-height:1.35;font-weight:600;color:#EDF4E9}
.rail-meta{margin:.55rem 0 0;font-size:.64rem;letter-spacing:.08em;color:rgba(237,244,233,.3)}
.scenes{display:flex;flex-direction:column;gap:6px;margin-top:22px}
.scene-item{display:flex;gap:10px;align-items:flex-start;padding:8px 10px;border:1px solid transparent;
  border-radius:2px;background:transparent;color:rgba(237,244,233,.5);font:inherit;font-size:.78rem;
  line-height:1.45;text-align:left;cursor:pointer}
.scene-item:hover{border-color:rgba(237,244,233,.14);color:#EDF4E9}
.scene-item[aria-current="true"]{border-color:rgba(217,88,61,.45);background:rgba(217,88,61,.08);color:#EDF4E9}
.scene-index{min-width:1.5rem;padding-top:.1rem;font-size:.6rem;color:rgba(237,244,233,.3)}
.scene-type{display:block;margin-top:.2rem;font-size:.53rem;letter-spacing:.14em;
  text-transform:uppercase;color:rgba(237,244,233,.25)}
.stage{min-width:0;padding:20px clamp(14px,3vw,32px) 108px}
.scene{display:none}
.scene[aria-hidden="false"]{display:block}
.frame{padding:8px;border:1px solid rgba(237,244,233,.1);background:#07110e;
  box-shadow:0 40px 120px -60px rgba(0,0,0,.9)}
.frame-head{display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between;padding:2px 6px 8px;
  border-bottom:1px solid rgba(237,244,233,.08);font-size:.56rem;letter-spacing:.16em;
  text-transform:uppercase;color:rgba(237,244,233,.35)}
.canvas-wrap{position:relative;display:flex;align-items:center;justify-content:center;width:100%;
  aspect-ratio:16/9;margin-top:8px;overflow:hidden}
.canvas{position:relative;flex:none;transform-origin:center;overflow:hidden}
.artwork{position:absolute;inset:0}
.artwork img{display:block;width:100%;height:100%;object-fit:cover}
.artwork .veil{position:absolute;inset:0;
  background:linear-gradient(90deg,rgba(11,23,20,.99) 0%,rgba(11,23,20,.95) 32%,rgba(11,23,20,.66) 62%,
  rgba(11,23,20,.24) 100%),linear-gradient(180deg,rgba(11,23,20,.4) 0%,rgba(11,23,20,0) 28%,
  rgba(11,23,20,0) 68%,rgba(11,23,20,.72) 100%)}
.el{position:absolute;overflow:hidden}
.el p{margin:0}
.notes{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;
  margin-top:14px;padding:14px;border:1px solid rgba(237,244,233,.08);background:rgba(237,244,233,.02)}
.notes-text{flex:1 1 320px;margin:0;font-size:.84rem;line-height:1.8;color:rgba(237,244,233,.65)}
.notes-text.empty{color:rgba(237,244,233,.3)}
audio{flex:0 1 320px;width:100%;max-width:320px;height:34px}
.doc{padding:clamp(20px,4vw,52px);border:1px solid rgba(237,244,233,.1);background:#0B1714}
.lab{margin-top:14px}
.lab iframe{display:block;width:100%;aspect-ratio:16/9;min-height:320px;border:1px solid rgba(237,244,233,.12);
  background:#07110e}
.doc-title{margin:10px 0 0;font-size:clamp(1.5rem,3vw,2.3rem);line-height:1.24;color:#F1ECDD}
.doc-body{display:flex;flex-direction:column;gap:16px;margin-top:22px;font-size:1rem;
  line-height:1.8;color:#D9E3D2}
.quiz-item{margin:0;padding-left:14px;border-left:2px solid rgba(217,88,61,.6)}
.quiz-question{margin:0;font-weight:600;color:#F1ECDD}
.quiz-options{margin:8px 0 0;padding-left:1.15rem}
.milestone{margin:0;padding-left:14px;border-left:2px solid rgba(169,189,168,.35)}
.milestone h3{margin:0;font-size:1.02rem;color:#F1ECDD}
.milestone ul{margin:6px 0 0;padding-left:1.15rem;font-size:.94rem}
details{margin-top:8px;font-size:.88rem;color:rgba(237,244,233,.55)}
summary{cursor:pointer;color:rgba(169,189,168,.85)}
.hint{margin:16px 0 0;font-size:.82rem;color:rgba(237,244,233,.4)}
.controls{position:fixed;right:0;bottom:0;left:0;z-index:5;display:flex;flex-wrap:wrap;align-items:center;
  gap:14px;padding:12px clamp(14px,3vw,32px);border-top:1px solid rgba(237,244,233,.08);
  background:rgba(7,17,14,.92)}
.controls button{padding:7px 14px;border:1px solid rgba(237,244,233,.16);background:transparent;
  color:rgba(237,244,233,.7);font:inherit;font-size:.72rem;letter-spacing:.08em;cursor:pointer;
  white-space:nowrap}
.controls button:hover{border-color:rgba(237,244,233,.4);color:#EDF4E9}
.controls input[type="range"]{flex:1;accent-color:#D9583D}
.counter{font-size:.66rem;letter-spacing:.14em;color:rgba(237,244,233,.4);white-space:nowrap}
@media (max-width:900px){.app{grid-template-columns:1fr}
  .rail{position:static;height:auto;border-right:0;border-bottom:1px solid rgba(237,244,233,.08)}
  .scenes{flex-direction:row;overflow:auto}.scene-item{flex:none;max-width:220px}.stage{padding-bottom:96px}}
@media print{@page{size:A4 landscape;margin:8mm}
  body{background:#fff}.rail,.controls,.notes audio{display:none!important}.app{display:block}
  .stage{padding:0}.scene{display:block!important;page-break-after:always;break-after:page}
  .frame{border:0;box-shadow:none;padding:0}.canvas-wrap{height:520px;aspect-ratio:auto}
  .canvas{transform:scale(.7)!important;transform-origin:top left}.notes{border:0}}
"""


def safe_style(value: str) -> str:
    declarations: List[str] = []
    for chunk in str(value or "").split(";"):
        if ":" not in chunk:
            continue
        name, raw = chunk.split(":", 1)
        name = name.strip().lower()
        raw = raw.strip()
        if name not in ALLOWED_STYLES or not raw:
            continue
        compact = raw.lower().replace(" ", "")
        if any(marker in compact for marker in UNSAFE_STYLE_MARKERS):
            continue
        declarations.append(f"{name}:{raw}")
    return ";".join(declarations)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.stack: List[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        tag = tag.lower()
        if tag in DROP_SUBTREE_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.parts.append("<br />")
            return
        style = dict(attrs).get("style")
        style_text = safe_style(style) if style else ""
        attribute = f' style="{html.escape(style_text, quote=True)}"' if style_text else ""
        self.parts.append(f"<{tag}{attribute}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        tag = tag.lower()
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.parts.append("<br />")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_SUBTREE_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.drop_depth or not data:
            return
        self.parts.append(html.escape(data, quote=False))

    def close(self) -> None:
        super().close()
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")


def sanitize_fragment(fragment: str) -> str:
    sanitizer = _Sanitizer()
    sanitizer.feed(str(fragment or ""))
    sanitizer.close()
    return "".join(sanitizer.parts)


def interactive_document(source: str) -> str:
    head = (
        f'<meta http-equiv="Content-Security-Policy" content="{INTERACTIVE_CSP}">'
        '<meta name="referrer" content="no-referrer">'
        f"<style>{INTERACTIVE_BASE_STYLES}</style>"
    )
    text = str(source or "").strip()
    if not text:
        return f"<!doctype html><html><head>{head}</head><body></body></html>"
    match = HEAD_PATTERN.search(text)
    if match:
        return f"{text[: match.end()]}{head}{text[match.end() :]}"
    match = HTML_PATTERN.search(text)
    if match:
        return f"{text[: match.end()]}<head>{head}</head>{text[match.end() :]}"
    return f"<!doctype html><html><head>{head}</head><body>{text}</body></html>"


def data_uri(mime_type: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"

def _number(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _nav_item(scene: ExportScene, index: int) -> str:
    label = TYPE_LABELS.get(scene.scene_type, "SCENE")
    current = "true" if index == 0 else "false"
    return (
        f'<button class="scene-item" type="button" data-index="{index}" '
        f'aria-current="{current}">'
        f'<span class="scene-index">{index + 1:02d}</span>'
        f"<span>{html.escape(scene.title)}"
        f'<span class="scene-type">{label}</span></span></button>'
    )


def _notes_panel(scene: ExportScene, audio_uri: Optional[str]) -> str:
    narration = scene.narration.strip()
    css = "notes-text" if narration else "notes-text empty"
    text = html.escape(narration) if narration else "本页没有旁白文本。"
    audio = ""
    if audio_uri:
        audio = f'<audio controls preload="metadata" src="{audio_uri}"></audio>'
    return f'<div class="notes"><p class="{css}">{text}</p>{audio}</div>'


def _frame_head(index: int, total: int, label: str, meta: str) -> str:
    return (
        f'<div class="frame-head"><span>Scene {index + 1:02d} / {total:02d}</span>'
        f"<span>{label} · {meta}</span></div>"
    )


def _element_markup(element: Dict[str, Any], default_color: str) -> str:
    left = _number(element.get("left"), 0.0)
    top = _number(element.get("top"), 0.0)
    width = _number(element.get("width"), 0.0)
    height = _number(element.get("height"), 0.0)
    if width <= 1 or height <= 1:
        return ""
    color = parse_color(element.get("defaultColor")) or default_color
    line_height = _number(element.get("lineHeight"), 1.4)
    rotate = _number(element.get("rotate"), 0.0)
    style = (
        f"left:{left:.0f}px;top:{top:.0f}px;width:{width:.0f}px;"
        f"height:{height:.0f}px;color:#{color};line-height:{line_height};"
    )
    if rotate:
        style += f"transform:rotate({rotate}deg);"
    font_name = element.get("defaultFontName")
    if isinstance(font_name, str) and font_name.strip():
        style += f'font-family:"{html.escape(font_name.strip(), quote=True)}",sans-serif;'
    content = sanitize_fragment(str(element.get("content") or ""))
    return f'<div class="el" style="{style}">{content}</div>'

def _slide_body(
    scene: ExportScene, index: int, total: int, theme: Theme
) -> str:
    content = scene_content(scene)
    canvas = as_dict(content.get("canvas"))
    canvas_theme = as_dict(canvas.get("theme"))
    raw_background = as_dict(canvas.get("background"))
    background = (
        parse_color(raw_background.get("color"))
        or parse_color(canvas_theme.get("backgroundColor"))
        or theme.background
    )
    font_color = parse_color(canvas_theme.get("fontColor")) or theme.text
    font_name = str(canvas_theme.get("fontName") or theme.font_name)
    parts: List[str] = [
        '<div class="frame">',
        _frame_head(index, total, "SLIDE", f"{SLIDE_W} × {SLIDE_H}"),
        '<div class="canvas-wrap">',
        f'<div class="canvas" style="width:{SLIDE_W}px;height:{SLIDE_H}px;'
        f"background:#{background};color:#{font_color};"
        f'font-family:\'{html.escape(font_name, quote=True)}\',sans-serif;">',
    ]
    if scene.image is not None:
        source = data_uri(scene.image.mime_type or "image/webp", scene.image.data)
        alt = html.escape(scene.title, quote=True)
        parts.append(
            f'<div class="artwork"><img src="{source}" alt="{alt}" /><div class="veil"></div></div>'
        )
    elements = canvas.get("elements")
    if isinstance(elements, list):
        for element in elements:
            if isinstance(element, dict):
                parts.append(_element_markup(element, font_color))
    parts.extend(["</div>", "</div>", "</div>"])
    return "".join(parts)


def _questions_of(scene: ExportScene) -> List[Dict[str, Any]]:
    content = scene_content(scene)
    raw = content.get("questions")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _answer_summary(question: Dict[str, Any]) -> Optional[str]:
    answer = answer_display(question)
    analysis = str(question.get("analysis") or "").strip()
    if answer == "未提供" and not analysis:
        return None
    parts = [f"答案：{answer}"]
    if analysis:
        parts.append(f"解析：{analysis}")
    return "".join(f"<p>{html.escape(part)}</p>" for part in parts)


def _quiz_body(scene: ExportScene, index: int, total: int, theme: Theme) -> str:
    items: List[str] = []
    for number, question in enumerate(_questions_of(scene), start=1):
        stem = str(question.get("question") or "").strip()
        if not stem:
            continue
        options: List[str] = []
        raw_options = question.get("options")
        if isinstance(raw_options, list):
            for option in raw_options:
                if not isinstance(option, dict):
                    continue
                text = option_text(option)
                if not text:
                    continue
                options.append(f"<li>{html.escape(text)}</li>")
        option_html = f'<ul class="quiz-options">{"".join(options)}</ul>' if options else ""
        summary = _answer_summary(question)
        details = (
            f"<details><summary>查看参考答案</summary>{summary}</details>"
            if summary
            else ""
        )
        items.append(
            f'<article class="quiz-item"><p class="quiz-question">'
            f"{number}. {html.escape(stem)}</p>{option_html}{details}</article>"
        )
    body = "".join(items) or '<p class="hint">这一页没有题目内容。</p>'
    return (
        '<div class="frame">'
        + _frame_head(index, total, "CHECK", "Assessment")
        + '<div class="doc"><p class="kicker">CHECK</p>'
        + f'<h2 class="doc-title">{html.escape(scene.title)}</h2>'
        + f'<div class="doc-body">{body}</div></div></div>'
    )


def _pbl_body(scene: ExportScene, index: int, total: int, theme: Theme) -> str:
    content = scene_content(scene)
    project = as_dict(content.get("project"))
    parts: List[str] = []
    description = str(project.get("description") or "").strip()
    if description:
        parts.append(f"<p>{html.escape(description)}</p>")
    objective = str(project.get("learningObjective") or "").strip()
    if objective:
        parts.append(f"<p>学习目标：{html.escape(objective)}</p>")
    deliverable = str(project.get("finalDeliverable") or "").strip()
    if deliverable:
        parts.append(f"<p>最终交付物：{html.escape(deliverable)}</p>")
    milestones = project.get("milestones")
    if isinstance(milestones, list):
        for number, milestone in enumerate(milestones, start=1):
            if not isinstance(milestone, dict):
                continue
            heading = str(milestone.get("title") or "").strip()
            tasks: List[str] = []
            raw_tasks = milestone.get("microtasks")
            if isinstance(raw_tasks, list):
                for task in raw_tasks:
                    if not isinstance(task, dict):
                        continue
                    task_title = str(task.get("title") or "").strip()
                    if task_title:
                        tasks.append(f"<li>{html.escape(task_title)}</li>")
            task_html = f"<ul>{''.join(tasks)}</ul>" if tasks else ""
            milestone_description = str(milestone.get("description") or "").strip()
            body = (
                f"<p>{html.escape(milestone_description)}</p>"
                if milestone_description
                else ""
            )
            parts.append(
                f'<section class="milestone"><h3>{number}. '
                f"{html.escape(heading)}</h3>{body}{task_html}</section>"
            )
    body = "".join(parts) or '<p class="hint">这一页没有项目内容。</p>'
    return (
        '<div class="frame">'
        + _frame_head(index, total, "PROJECT", "Project brief")
        + '<div class="doc"><p class="kicker">PROJECT</p>'
        + f'<h2 class="doc-title">{html.escape(scene.title)}</h2>'
        + f'<div class="doc-body">{body}</div></div></div>'
    )


def _interactive_body(scene: ExportScene, index: int, total: int, theme: Theme) -> str:
    content = scene_content(scene)
    widget = str(content.get("widgetType") or "simulation")
    prompt = str(content.get("prompt") or "").strip()
    raw_html = content.get("html")
    widget_html = raw_html.strip() if isinstance(raw_html, str) else ""
    parts: List[str] = []
    if prompt:
        parts.append(f"<p>{html.escape(prompt)}</p>")
    if widget_html:
        document = html.escape(interactive_document(widget_html), quote=True)
        title = html.escape(f"互动组件：{scene.title}", quote=True)
        parts.append(
            f'<div class="lab"><iframe title="{title}" sandbox="allow-scripts" '
            f'referrerpolicy="no-referrer" loading="lazy" srcdoc="{document}"></iframe></div>'
            '<p class="hint">组件在隔离沙箱中离线运行，无法访问网络或本站数据；'
            "如需重新开始，刷新页面即可。</p>"
        )
    else:
        parts.append(
            '<p class="hint">本场景是交互式实验，导出文件中没有可离线运行的组件；'
            "请在课堂网页中打开以操作完整交互。</p>"
        )
    return (
        '<div class="frame">'
        + _frame_head(index, total, "LAB", widget)
        + '<div class="doc"><p class="kicker">LAB / '
        + html.escape(widget)
        + "</p>"
        + f'<h2 class="doc-title">{html.escape(scene.title)}</h2>'
        + f'<div class="doc-body">{"".join(parts)}</div></div></div>'
    )

SCRIPT = """
(function () {
  var scenes = Array.prototype.slice.call(document.querySelectorAll('.scene'));
  var buttons = Array.prototype.slice.call(document.querySelectorAll('.scene-item'));
  var range = document.getElementById('progress');
  var counter = document.getElementById('counter');
  var prev = document.getElementById('prev');
  var next = document.getElementById('next');
  if (!scenes.length) { return; }
  var current = 0;

  function scale() {
    scenes.forEach(function (scene) {
      var wrap = scene.querySelector('.canvas-wrap');
      var canvas = scene.querySelector('.canvas');
      if (!wrap || !canvas) { return; }
      var width = wrap.clientWidth;
      var height = wrap.clientHeight;
      if (!width || !height) { return; }
      var factor = Math.min(width / 1280, height / 720);
      canvas.style.transform = 'scale(' + factor.toFixed(4) + ')';
    });
  }

  function pauseAudio(exceptIndex) {
    scenes.forEach(function (scene, index) {
      if (index === exceptIndex) { return; }
      Array.prototype.forEach.call(scene.querySelectorAll('audio'), function (audio) {
        try { audio.pause(); } catch (error) {}
      });
    });
  }

  function show(index) {
    if (index < 0 || index >= scenes.length) { return; }
    current = index;
    scenes.forEach(function (scene, position) {
      scene.setAttribute('aria-hidden', position === index ? 'false' : 'true');
    });
    buttons.forEach(function (button, position) {
      button.setAttribute('aria-current', position === index ? 'true' : 'false');
    });
    if (range) { range.value = String(index); }
    if (counter) {
      counter.textContent =
        String(index + 1).padStart(2, '0') + ' / ' + String(scenes.length).padStart(2, '0');
    }
    pauseAudio(index);
    scale();
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, '', '#scene-' + (index + 1));
    }
  }

  buttons.forEach(function (button) {
    button.addEventListener('click', function () {
      show(Number(button.getAttribute('data-index')));
    });
  });
  if (range) {
    range.max = String(Math.max(0, scenes.length - 1));
    range.addEventListener('input', function () { show(Number(range.value)); });
  }
  if (prev) { prev.addEventListener('click', function () { show(current - 1); }); }
  if (next) { next.addEventListener('click', function () { show(current + 1); }); }
  document.addEventListener('keydown', function (event) {
    var target = event.target;
    if (target && /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName)) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') { return; }
    }
    if (event.key === 'ArrowRight') { show(current + 1); }
    else if (event.key === 'ArrowLeft') { show(current - 1); }
    else if (event.key === 'Home') { show(0); }
    else if (event.key === 'End') { show(scenes.length - 1); }
  });
  window.addEventListener('resize', scale);
  window.addEventListener('beforeprint', function () {
    scenes.forEach(function (scene) { scene.setAttribute('aria-hidden', 'false'); });
  });
  window.addEventListener('afterprint', function () { show(current); });

  var hash = parseInt(String(window.location.hash || '').replace('#scene-', ''), 10);
  show(isNaN(hash) ? 0 : Math.min(Math.max(hash - 1, 0), scenes.length - 1));
})();
"""


def render_html(document: ExportDocument, settings: Settings) -> RenderedExport:
    theme = document_theme(document)
    total = len(document.scenes)
    warnings: List[str] = []
    audio_total = sum(len(scene.audio.data) for scene in document.scenes if scene.audio)
    allow_audio = True
    if audio_total > int(settings.export_max_bytes * 0.6):
        allow_audio = False
        if audio_total:
            warnings.append(
                "Narration audio was omitted because the export would exceed the size limit"
            )

    nav: List[str] = []
    sections: List[str] = []
    for index, scene in enumerate(document.scenes):
        audio_uri = None
        if scene.audio is not None and allow_audio:
            audio_uri = data_uri(scene.audio.mime_type, scene.audio.data)
        content = scene_content(scene)
        canvas = content.get("canvas")
        if scene.scene_type == "slide" and isinstance(canvas, dict):
            body = _slide_body(scene, index, total, theme)
        elif scene.scene_type == "quiz":
            body = _quiz_body(scene, index, total, theme)
        elif scene.scene_type == "pbl":
            body = _pbl_body(scene, index, total, theme)
        else:
            body = _interactive_body(scene, index, total, theme)
        nav.append(_nav_item(scene, index))
        sections.append(
            f'<section class="scene" id="scene-{index + 1}" data-index="{index}" '
            f'aria-hidden="{"false" if index == 0 else "true"}">{body}'
            f"{_notes_panel(scene, audio_uri)}</section>"
        )

    description = (
        f'<p class="rail-meta">{html.escape(document.description)}</p>'
        if document.description
        else ""
    )
    page = "".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8" />',
            '<meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"<title>{html.escape(document.title)} · TeachAI Classroom</title>",
            "<style>",
            STYLES,
            "</style>",
            "</head>",
            "<body>",
            '<div class="app">',
            '<aside class="rail">',
            '<p class="kicker">TEACHAI / CLASSROOM</p>',
            f'<h1 class="rail-title">{html.escape(document.title)}</h1>',
            f'<p class="rail-meta">{total} scenes · v{document.version} · '
            f"DSL {html.escape(document.dsl_version)}</p>",
            description,
            f'<nav class="scenes" aria-label="场景列表">{"".join(nav)}</nav>',
            "</aside>",
            '<main class="stage">',
            "".join(sections),
            "</main>",
            "</div>",
            '<div class="controls">',
            '<button type="button" id="prev">上一步</button>',
            '<input type="range" id="progress" min="0" max="1" step="1" value="0" aria-label="场景进度" />',
            '<button type="button" id="next">下一步</button>',
            f'<span class="counter" id="counter">01 / {total:02d}</span>',
            "</div>",
            "<script>",
            SCRIPT,
            "</script>",
            "</body>",
            "</html>",
        ]
    )
    return RenderedExport(
        data=page.encode("utf-8"),
        extension="html",
        mime_type=HTML_MIME,
        warnings=warnings,
    )
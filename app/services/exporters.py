from typing import List

from app.core.config import Settings
from app.core.errors import AppError
from app.services.export_document import (
    HTML_MIME,
    PPTX_MIME,
    SUPPORTED_FORMATS,
    ExportAudio,
    ExportDocument,
    ExportImage,
    ExportScene,
    RenderedExport,
    html_blocks,
    scene_content,
    scene_lines,
)
from app.services.export_html import render_html
from app.services.export_pptx import render_pptx

__all__: List[str] = [
    "HTML_MIME",
    "PPTX_MIME",
    "SUPPORTED_FORMATS",
    "ExportAudio",
    "ExportDocument",
    "ExportImage",
    "ExportScene",
    "RenderedExport",
    "html_blocks",
    "render_export",
    "scene_content",
    "scene_lines",
]


def render_export(
    document: ExportDocument, export_format: str, settings: Settings
) -> RenderedExport:
    if export_format == "pptx":
        return render_pptx(document)
    if export_format == "html":
        return render_html(document, settings)
    raise AppError(
        422,
        "EXPORT_FORMAT_UNSUPPORTED",
        f"Unsupported export format: {export_format}",
    )
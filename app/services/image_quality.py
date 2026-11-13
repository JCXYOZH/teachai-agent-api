"""Heuristic quality scoring for generated classroom illustrations.

Scoring runs on the normalized WebP bytes that are actually stored, so the
score always matches what the learner sees. It is deliberately cheap (Pillow
only, no model calls) and every deduction has an explainable reason code.
"""

import io
import logging
from dataclasses import dataclass
from typing import Dict, List

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

logger = logging.getLogger(__name__)

MIN_WIDTH = 480
MIN_HEIGHT = 270
MIN_EDGE_VARIANCE = 25.0
MIN_LUMINANCE = 25.0
MAX_LUMINANCE = 232.0
MAX_CLIPPED_RATIO = 0.5
MAX_DOMINANT_SHARE = 0.85
MIN_COLOR_COUNT = 6
SAMPLE_SIZE = 32


@dataclass(frozen=True)
class QualityReport:
    score: int
    status: str
    reasons: List[str]
    metrics: Dict[str, float]


def assess_image(data: bytes, *, min_score: int = 60) -> QualityReport:
    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return QualityReport(
            score=0,
            status="low",
            reasons=["unreadable"],
            metrics={},
        )

    rgb = opened.convert("RGB")
    gray = rgb.convert("L")
    width, height = rgb.size

    mean_luminance = float(ImageStat.Stat(gray).mean[0])
    edge_variance = float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0])

    histogram = gray.histogram()
    total_pixels = width * height or 1
    clipped_pixels = sum(histogram[:4]) + sum(histogram[252:])
    clipped_ratio = clipped_pixels / total_pixels

    sample = rgb.resize((SAMPLE_SIZE, SAMPLE_SIZE))
    palette = sample.quantize(colors=16)
    colors = palette.getcolors(SAMPLE_SIZE * SAMPLE_SIZE) or []
    color_count = len(colors)
    dominant_share = (
        max(count for count, _ in colors) / (SAMPLE_SIZE * SAMPLE_SIZE)
        if colors
        else 1.0
    )

    score = 100
    reasons: List[str] = []
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        score = min(score - 40, 40)
        reasons.append("low_resolution")
    if edge_variance < MIN_EDGE_VARIANCE:
        score -= 25
        reasons.append("blurry_or_flat")
    if not MIN_LUMINANCE <= mean_luminance <= MAX_LUMINANCE:
        score -= 20
        reasons.append("exposure")
    if clipped_ratio > MAX_CLIPPED_RATIO:
        score -= 15
        reasons.append("clipped")
    if dominant_share > MAX_DOMINANT_SHARE:
        score -= 30
        reasons.append("near_solid")
    if color_count < MIN_COLOR_COUNT:
        score -= 15
        reasons.append("monochrome")

    score = max(0, min(100, score))
    metrics = {
        "width": float(width),
        "height": float(height),
        "mean_luminance": round(mean_luminance, 1),
        "edge_variance": round(edge_variance, 1),
        "clipped_ratio": round(clipped_ratio, 3),
        "dominant_share": round(dominant_share, 3),
        "color_count": float(color_count),
    }
    return QualityReport(
        score=score,
        status="ok" if score >= min_score else "low",
        reasons=reasons,
        metrics=metrics,
    )


def quality_metadata(report: QualityReport) -> Dict[str, object]:
    return {
        "score": report.score,
        "status": report.status,
        "reasons": list(report.reasons),
        "metrics": dict(report.metrics),
    }

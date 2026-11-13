import io

from PIL import Image, ImageDraw

from app.services.image_quality import assess_image, quality_metadata


def _encode(image: Image.Image, fmt: str = "WEBP") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=90)
    return buffer.getvalue()


def _detailed_scene(width: int = 512, height: int = 288) -> Image.Image:
    image = Image.new("RGB", (width, height), (238, 232, 220))
    draw = ImageDraw.Draw(image)
    for step in range(0, width, 8):
        shade = 30 + int(180 * step / width)
        draw.rectangle([step, 0, step + 8, height], fill=(shade, 90, 120 - shade // 3))
    for index in range(40):
        left = (index * 37) % (width - 60)
        top = (index * 19) % (height - 40)
        color = (20 + index * 5, 60 + (index * 11) % 120, 200 - index * 3)
        draw.ellipse([left, top, left + 40, top + 24], fill=color)
    return image


def test_detailed_illustration_scores_ok():
    report = assess_image(_encode(_detailed_scene()))

    assert report.status == "ok"
    assert report.score >= 60
    assert report.reasons == []
    assert report.metrics["width"] == 512


def test_flat_fill_is_rejected_as_low_quality():
    image = Image.new("RGB", (512, 288), (200, 200, 200))

    report = assess_image(_encode(image))

    assert report.status == "low"
    assert "near_solid" in report.reasons
    assert "monochrome" in report.reasons


def test_tiny_image_is_rejected_for_resolution():
    report = assess_image(_encode(_detailed_scene(120, 68)))

    assert report.status == "low"
    assert "low_resolution" in report.reasons


def test_dark_image_is_flagged_for_exposure():
    image = Image.new("RGB", (512, 288), (4, 4, 6))

    report = assess_image(_encode(image))

    assert "exposure" in report.reasons


def test_unreadable_payload_scores_zero():
    report = assess_image(b"not-an-image")

    assert report.score == 0
    assert report.status == "low"
    assert report.reasons == ["unreadable"]


def test_quality_metadata_is_json_ready():
    payload = quality_metadata(assess_image(_encode(_detailed_scene())))

    assert set(payload) == {"score", "status", "reasons", "metrics"}
    assert isinstance(payload["score"], int)

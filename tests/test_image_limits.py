import base64
import io
import struct
import zlib
from typing import Any, Dict

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.errors import AppError
from app.services.images import generate_qwen_image, normalize_image
from app.services.provider_registry import get_provider_spec
from app.services.providers import ResolvedProvider


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _header_only_png(width: int, height: int) -> bytes:
    """A PNG whose IHDR advertises the size but carries no image data.

    The pixel limit must trip from the header alone, before Pillow tries to
    decode (and allocate) the raster.
    """
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")


def _solid_image(fmt: str, width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 120)).save(output, format=fmt, quality=90)
    return output.getvalue()


async def test_oversized_image_is_rejected_from_header_before_decode(monkeypatch: Any) -> None:
    def _no_load(self: Any, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("oversized images must be rejected before decoding")

    monkeypatch.setattr(Image.Image, "load", _no_load)

    with pytest.raises(AppError) as exc_info:
        await normalize_image(
            _header_only_png(9000, 9000), max_width=1600, max_height=900, quality=82
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "IMAGE_TOO_LARGE"


async def test_pixel_limit_is_configurable_and_boundary_is_inclusive() -> None:
    with pytest.raises(AppError) as exc_info:
        await normalize_image(
            _solid_image("PNG", 40, 30), max_width=1600, max_height=900, quality=82, max_pixels=1000
        )
    assert exc_info.value.code == "IMAGE_TOO_LARGE"

    data, width, height = await normalize_image(
        _solid_image("PNG", 40, 30), max_width=1600, max_height=900, quality=82, max_pixels=1200
    )
    assert (width, height) == (40, 30)
    assert data[:4] == b"RIFF"


async def test_decompression_bomb_is_reported_as_invalid_response(monkeypatch: Any) -> None:
    # Pillow raises DecompressionBombError (not OSError/ValueError) once an image
    # exceeds twice MAX_IMAGE_PIXELS; it must surface as a 502, not a 500.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(AppError) as exc_info:
        await normalize_image(
            _solid_image("PNG", 20, 20), max_width=1600, max_height=900, quality=82
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "IMAGE_INVALID_RESPONSE"


async def test_large_jpeg_still_normalizes_through_draft_path() -> None:
    data, width, height = await normalize_image(
        _solid_image("JPEG", 2400, 1200), max_width=1600, max_height=900, quality=82
    )

    assert (width, height) == (1600, 800)
    assert data[:4] == b"RIFF"


def test_settings_image_max_pixels_floor() -> None:
    with pytest.raises(ValueError):
        Settings(image_max_pixels=999_999)
    assert Settings(image_max_pixels=1_000_000).image_max_pixels == 1_000_000


async def test_qwen_generation_applies_pixel_limit(monkeypatch: Any) -> None:
    import httpx

    oversized = _header_only_png(9000, 9000)
    payload: Dict[str, Any] = {
        "output": {
            "choices": [
                {
                    "message": {
                        "content": [
                            {
                                "type": "image",
                                "image": "data:image/png;base64,"
                                + base64.b64encode(oversized).decode("ascii"),
                            }
                        ]
                    }
                }
            ]
        }
    }

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def post(self, url: str, headers: Any = None, json: Any = None) -> Any:
            return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    spec = get_provider_spec("qwen")
    assert spec is not None
    resolved = ResolvedProvider(
        spec=spec,
        config=None,
        api_key=None,
        api_key_source=None,
        base_url="https://dashscope.example.test/api/v1",
        default_model="qwen-image-3.0",
    )

    with pytest.raises(AppError) as exc_info:
        await generate_qwen_image(
            resolved,
            prompt="a lighthouse",
            model="qwen-image-3.0",
            timeout=5,
            max_bytes=1024 * 1024,
            max_width=1600,
            max_height=900,
            webp_quality=82,
            max_pixels=1_000_000,
        )

    assert exc_info.value.code == "IMAGE_TOO_LARGE"

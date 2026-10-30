import asyncio
import base64
import logging
import struct
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlsplit

import edge_tts
import httpx
from edge_tts.exceptions import EdgeTTSException

from app.core.errors import AppError
from app.services.providers import ResolvedProvider

logger = logging.getLogger(__name__)

MIME_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
}

MP3_BITRATES = {
    (1, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (2, 1): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}

MP3_SAMPLE_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    format: str
    mime_type: str
    duration_ms: Optional[int]


async def synthesize_speech(
    resolved: ResolvedProvider,
    text: str,
    *,
    speed: float,
    timeout: float,
    max_bytes: int,
) -> SynthesizedAudio:
    if resolved.spec.id == "edge-tts":
        return await _synthesize_edge_tts(
            voice=resolved.tts_voice or "",
            text=text,
            speed=speed,
            timeout=timeout,
            max_bytes=max_bytes,
        )
    model = resolved.tts_model or resolved.default_model
    voice = resolved.tts_voice or ""
    base_url = (resolved.tts_base_url or resolved.base_url).rstrip("/")
    if resolved.spec.id == "qwen" and _is_dashscope_native(base_url):
        return await _synthesize_dashscope(
            base_url=base_url,
            api_key=resolved.api_key,
            model=model,
            voice=voice,
            text=text,
            speed=speed,
            timeout=timeout,
            max_bytes=max_bytes,
        )
    return await _synthesize_openai_compatible(
        base_url=base_url,
        api_key=resolved.api_key,
        model=model,
        voice=voice,
        text=text,
        speed=speed,
        response_format="wav" if resolved.spec.id == "glm" else "mp3",
        timeout=timeout,
        max_bytes=max_bytes,
    )


def _edge_tts_rate(speed: float) -> str:
    percent = max(-50, min(100, int(round((speed - 1.0) * 100))))
    return f"{percent:+d}%"


async def _synthesize_edge_tts(
    *,
    voice: str,
    text: str,
    speed: float,
    timeout: float,
    max_bytes: int,
) -> SynthesizedAudio:
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=_edge_tts_rate(speed),
        connect_timeout=max(1, min(10, int(timeout))),
        receive_timeout=max(1, min(60, int(timeout))),
    )

    async def collect() -> bytes:
        buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") != "audio":
                continue
            buffer.extend(chunk.get("data", b""))
            if len(buffer) > max_bytes:
                raise AppError(
                    502,
                    "TTS_AUDIO_TOO_LARGE",
                    "Generated audio exceeds the size limit",
                )
        return bytes(buffer)

    try:
        data = await asyncio.wait_for(collect(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise AppError(504, "TTS_TIMEOUT", "Speech synthesis timed out") from exc
    except AppError:
        raise
    except EdgeTTSException as exc:
        logger.warning("edge_tts_failed %s", type(exc).__name__)
        raise AppError(
            502,
            "TTS_PROVIDER_ERROR",
            f"Edge TTS failed: {str(exc)[:200]}",
        ) from exc
    except Exception as exc:
        logger.warning("edge_tts_failed %s", type(exc).__name__)
        raise AppError(
            502, "TTS_PROVIDER_ERROR", "Edge TTS could not be reached"
        ) from exc
    if not data:
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned no audio"
        )
    return _build_audio(data, "mp3")


def audio_duration_ms(data: bytes, audio_format: str) -> Optional[int]:
    try:
        if audio_format == "wav":
            return _wav_duration_ms(data)
        if audio_format == "mp3":
            return _mp3_duration_ms(data)
    except (IndexError, struct.error, ValueError):
        return None
    return None


def _is_dashscope_native(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")
    if host.endswith("dashscope.aliyuncs.com"):
        return "/compatible-mode" not in path
    return host.endswith("maas.aliyuncs.com") and path.endswith("/api/v1")


def _dashscope_generation_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    path = (urlsplit(base_url).path or "").rstrip("/")
    if path.endswith("/api/v1"):
        return f"{base_url}/services/aigc/multimodal-generation/generation"
    return f"{base_url}/api/v1/services/aigc/multimodal-generation/generation"


async def _synthesize_dashscope(
    *,
    base_url: str,
    api_key: Optional[str],
    model: str,
    voice: str,
    text: str,
    speed: float,
    timeout: float,
    max_bytes: int,
) -> SynthesizedAudio:
    url = _dashscope_generation_url(base_url)
    input_payload = {"text": text, "voice": voice}
    request: dict = {"model": model, "input": input_payload}
    if abs(speed - 1.0) > 0.01:
        request["parameters"] = {
            "rate": max(-100, min(100, int(round((speed - 1.0) * 100))))
        }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = await _post_json(
        url, headers=headers, payload=request, timeout=timeout, provider="qwen"
    )
    output = payload.get("output") if isinstance(payload, dict) else None
    audio = output.get("audio") if isinstance(output, dict) else None
    if not isinstance(audio, dict):
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned no audio"
        )
    audio_url = audio.get("url")
    encoded = audio.get("data") or audio.get("audio_data")
    if isinstance(audio_url, str) and audio_url.strip():
        data, content_type = await _download_audio(
            audio_url.strip(), timeout=timeout, max_bytes=max_bytes
        )
        audio_format = _detect_format(content_type, audio_url, default="mp3")
    elif isinstance(encoded, str) and encoded.strip():
        raw = encoded.split(",", 1)[1] if encoded.startswith("data:") else encoded
        try:
            data = base64.b64decode(raw, validate=False)
        except (ValueError, TypeError) as exc:
            raise AppError(
                502, "TTS_INVALID_RESPONSE", "Speech provider returned invalid audio"
            ) from exc
        audio_format = _detect_format("", "", default="wav")
    else:
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned no audio"
        )
    if len(data) > max_bytes:
        raise AppError(
            502, "TTS_AUDIO_TOO_LARGE", "Generated audio exceeds the size limit"
        )
    return _build_audio(data, audio_format)


async def _synthesize_openai_compatible(
    *,
    base_url: str,
    api_key: Optional[str],
    model: str,
    voice: str,
    text: str,
    speed: float,
    response_format: str,
    timeout: float,
    max_bytes: int,
) -> SynthesizedAudio:
    request: dict = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    if abs(speed - 1.0) > 0.01:
        request["speed"] = round(speed, 2)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base_url}/audio/speech"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=request)
    except httpx.TimeoutException as exc:
        raise AppError(
            504, "TTS_TIMEOUT", "Speech synthesis timed out"
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502, "TTS_PROVIDER_ERROR", "Speech provider could not be reached"
        ) from exc
    if response.status_code != 200:
        if response.status_code in {404, 405}:
            raise AppError(
                502,
                "TTS_ENDPOINT_NOT_SUPPORTED",
                "The configured TTS endpoint does not expose /audio/speech; "
                "set a TTS base URL that supports speech synthesis",
            )
        if response.status_code in {401, 403}:
            raise AppError(
                502,
                "TTS_AUTH_FAILED",
                "Speech provider rejected the API key; use a key that can access the TTS model",
            )
        raise AppError(
            502,
            "TTS_PROVIDER_ERROR",
            _response_error(response),
        )
    content_type = response.headers.get("content-type", "")
    data = response.content
    if not data:
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned no audio"
        )
    if len(data) > max_bytes:
        raise AppError(
            502, "TTS_AUDIO_TOO_LARGE", "Generated audio exceeds the size limit"
        )
    audio_format = _detect_format(content_type, "", default=response_format)
    return _build_audio(data, audio_format)


async def _post_json(
    url: str,
    *,
    headers: dict,
    payload: dict,
    timeout: float,
    provider: str,
) -> dict:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise AppError(
            504, "TTS_TIMEOUT", "Speech synthesis timed out"
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502, "TTS_PROVIDER_ERROR", "Speech provider could not be reached"
        ) from exc
    if response.status_code != 200:
        raise AppError(502, "TTS_PROVIDER_ERROR", _response_error(response))
    try:
        body = response.json()
    except ValueError as exc:
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned invalid JSON"
        ) from exc
    if not isinstance(body, dict):
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned invalid JSON"
        )
    logger.info(
        "tts_request_completed provider=%s status=%s", provider, response.status_code
    )
    return body


async def _download_audio(
    url: str, *, timeout: float, max_bytes: int
) -> Tuple[bytes, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned an invalid audio URL"
        )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise AppError(
                        502,
                        "TTS_AUDIO_DOWNLOAD_FAILED",
                        "Generated audio could not be downloaded",
                    )
                content_type = response.headers.get("content-type", "")
                buffer = bytearray()
                async for chunk in response.aiter_bytes(64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        raise AppError(
                            502,
                            "TTS_AUDIO_TOO_LARGE",
                            "Generated audio exceeds the size limit",
                        )
    except httpx.TimeoutException as exc:
        raise AppError(
            504, "TTS_TIMEOUT", "Generated audio download timed out"
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502,
            "TTS_AUDIO_DOWNLOAD_FAILED",
            "Generated audio could not be downloaded",
        ) from exc
    if not buffer:
        raise AppError(
            502, "TTS_INVALID_RESPONSE", "Speech provider returned no audio"
        )
    return bytes(buffer), content_type


def _build_audio(data: bytes, audio_format: str) -> SynthesizedAudio:
    return SynthesizedAudio(
        data=data,
        format=audio_format,
        mime_type=MIME_TYPES.get(audio_format, "application/octet-stream"),
        duration_ms=audio_duration_ms(data, audio_format),
    )


def _detect_format(content_type: str, url: str, *, default: str) -> str:
    value = (content_type or "").lower()
    path = (urlsplit(url).path or "").lower()
    for suffix, audio_format in ((".wav", "wav"), (".mp3", "mp3"), (".ogg", "ogg")):
        if path.endswith(suffix):
            return audio_format
    if "wav" in value or "wave" in value:
        return "wav"
    if "mpeg" in value or "mp3" in value:
        return "mp3"
    if "ogg" in value:
        return "ogg"
    return default if default in MIME_TYPES else "mp3"


def _response_error(response: httpx.Response) -> str:
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or "")
            if not message:
                message = str(payload.get("message") or payload.get("detail") or "")
    except ValueError:
        message = response.text
    normalized = " ".join(message.split())
    return (normalized[:300] or f"Speech provider returned HTTP {response.status_code}")


def _wav_duration_ms(data: bytes) -> Optional[int]:
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset = 12
    byte_rate = 0
    data_size = 0
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        body = offset + 8
        if chunk_id == b"fmt " and chunk_size >= 16 and body + 16 <= len(data):
            byte_rate = struct.unpack_from("<I", data, body + 8)[0]
        elif chunk_id == b"data":
            data_size = min(chunk_size, max(0, len(data) - body))
            break
        offset = body + chunk_size + (chunk_size % 2)
    if byte_rate <= 0 or data_size <= 0:
        return None
    return int(round(data_size * 1000 / byte_rate))


def _mp3_duration_ms(data: bytes) -> Optional[int]:
    offset = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (
            (data[6] & 0x7F) << 21
            | (data[7] & 0x7F) << 14
            | (data[8] & 0x7F) << 7
            | (data[9] & 0x7F)
        )
        offset = 10 + size
    start = offset
    search_limit = min(len(data) - 4, offset + 64 * 1024)
    while start < search_limit:
        if data[start] == 0xFF and (data[start + 1] & 0xE0) == 0xE0:
            break
        start += 1
    else:
        return None
    header = data[start : start + 4]
    version_bits = (header[1] >> 3) & 0x03
    layer_bits = (header[1] >> 1) & 0x03
    bitrate_index = (header[2] >> 4) & 0x0F
    sample_rate_index = (header[2] >> 2) & 0x03
    padding = (header[2] >> 1) & 0x01
    if version_bits == 1 or layer_bits != 1 or sample_rate_index == 3:
        return None
    sample_rates = MP3_SAMPLE_RATES.get(version_bits)
    bitrates = MP3_BITRATES.get((1 if version_bits == 3 else 2, 1))
    if not sample_rates or not bitrates or bitrate_index in {0, 15}:
        return None
    sample_rate = sample_rates[sample_rate_index]
    bitrate = bitrates[bitrate_index] * 1000
    if bitrate <= 0:
        return None
    samples_per_frame = 1152 if version_bits == 3 else 576
    frame_length = int(144 * bitrate / sample_rate + padding)
    if frame_length <= 4 or start + frame_length > len(data):
        return None
    mono = ((header[3] >> 6) & 0x03) == 3
    side_info = (17 if mono else 32) if version_bits == 3 else (9 if mono else 17)
    marker = data[start + 4 + side_info : start + 8 + side_info]
    if marker in {b"Xing", b"Info"}:
        flags = struct.unpack_from(">I", data, start + 8 + side_info)[0]
        if flags & 0x0001:
            frames = struct.unpack_from(">I", data, start + 12 + side_info)[0]
            if frames > 0:
                return int(round(frames * samples_per_frame * 1000 / sample_rate))
    audio_bytes = len(data) - start
    return int(round(audio_bytes * 8 * 1000 / bitrate))

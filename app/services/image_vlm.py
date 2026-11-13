"""Optional vision-model scoring for generated classroom illustrations.

The heuristic scorer in :mod:`app.services.image_quality` is cheap but blind to
semantics: it cannot tell whether the artwork matches the lesson. When
``IMAGE_QUALITY_VLM_ENABLED`` is on, a vision model from the owner's own
provider configuration judges relevance and artifacts. Any failure here is
logged and skipped so illustration generation never blocks on it.
"""

import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.user import User
from app.services.providers import ProviderConfigService

logger = logging.getLogger(__name__)

MAX_ISSUES = 5
MAX_ISSUE_CHARS = 120
SCORE_PATTERN = re.compile(r"\b(\d{1,3})\b")
JSON_PATTERN = re.compile(r"\{.*\}", re.S)

SYSTEM_PROMPT = (
    "You review illustrations that will be shown as the main visual of a "
    "classroom slide. Decide whether the image is usable for the given lesson "
    "topic: it must be relevant to that topic, must not contain readable text, "
    "letters, watermarks or frames, and must be compositionally complete "
    "without obvious distortion. Answer with JSON only and no explanation: "
    '{"score": <integer 0-100>, "issues": ["<short problem>"]}. '
    "Write the issues in the same language as the lesson topic. "
    "A score below 60 means the image should not be used as is."
)


@dataclass(frozen=True)
class VlmScore:
    score: int
    issues: List[str]
    model: str


def parse_score(text: str, *, model: str = "unknown") -> Optional[VlmScore]:
    candidate = text.strip()
    if not candidate:
        return None
    match = JSON_PATTERN.search(candidate)
    if match:
        try:
            payload = json.loads(match.group(0))
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            raw_score = payload.get("score")
            score = None
            if isinstance(raw_score, (int, float, str)):
                try:
                    score = int(float(raw_score))
                except ValueError:
                    score = None
            if score is not None:
                raw_issues = payload.get("issues")
                issues: List[str] = []
                if isinstance(raw_issues, list):
                    issues = [
                        " ".join(str(item).split())[:MAX_ISSUE_CHARS]
                        for item in raw_issues
                        if str(item).strip()
                    ][:MAX_ISSUES]
                return VlmScore(
                    score=max(0, min(100, score)), issues=issues, model=model
                )
    numeric = SCORE_PATTERN.search(candidate)
    if numeric:
        return VlmScore(
            score=max(0, min(100, int(numeric.group(1)))), issues=[], model=model
        )
    return None


def _content_text(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("text")
        ]
        return "".join(parts)
    return ""


def _mime_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG"):
        return "image/png"
    if image.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    return "image/webp"


async def score_illustration(
    *,
    db: AsyncSession,
    settings: Settings,
    owner: User,
    image: bytes,
    prompt: str,
) -> Optional[VlmScore]:
    if not settings.image_quality_vlm_enabled:
        return None
    provider_id = settings.image_quality_vlm_provider_id.strip().lower()
    try:
        resolved = await ProviderConfigService(db, settings).resolve_for_generation(
            owner, provider_id
        )
    except Exception:
        logger.info(
            "image_quality_vlm_skipped provider=%s reason=not_configured", provider_id
        )
        return None
    if resolved.spec.id != provider_id or not resolved.api_key:
        logger.info(
            "image_quality_vlm_skipped provider=%s reason=no_credentials", provider_id
        )
        return None
    model = settings.image_quality_vlm_model.strip() or resolved.default_model
    encoded = base64.b64encode(image).decode("ascii")
    mime = _mime_type(image)
    request: Dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Lesson topic and requirements:\n" + prompt[:1500],
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:" + mime + ";base64," + encoded},
                    },
                ],
            },
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + resolved.api_key,
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.image_quality_vlm_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                resolved.base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json=request,
            )
    except httpx.HTTPError:
        logger.warning(
            "image_quality_vlm_failed provider=%s model=%s reason=transport",
            provider_id,
            model,
        )
        return None
    if response.status_code != 200:
        logger.warning(
            "image_quality_vlm_failed provider=%s model=%s status=%s",
            provider_id,
            model,
            response.status_code,
        )
        return None
    try:
        payload = response.json()
    except ValueError:
        logger.warning(
            "image_quality_vlm_failed provider=%s reason=invalid_json", provider_id
        )
        return None
    text = _content_text(payload) if isinstance(payload, dict) else ""
    parsed = parse_score(text, model=model)
    if parsed is None:
        logger.warning(
            "image_quality_vlm_failed provider=%s model=%s reason=unparsed",
            provider_id,
            model,
        )
    return parsed

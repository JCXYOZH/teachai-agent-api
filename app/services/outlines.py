import asyncio
import logging
from dataclasses import dataclass
from typing import Any, List, Optional
from uuid import UUID

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.asset import Asset, AssetExtraction
from app.models.course import Course
from app.models.outline import CourseOutline
from app.models.user import User
from app.schemas.outline import (
    CourseOutlineResponse,
    OutlineGenerateRequest,
    OutlineSaveRequest,
    OutlineScene,
)
from app.services.outline_prompt import SYSTEM_PROMPT, build_outline_user_prompt
from app.services.outline_schema import (
    NormalizedOutline,
    OutlineParseError,
    normalize_generated_outline,
    normalize_manual_outline,
)
from app.services.providers import ProviderConfigService, ResolvedProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaterialBundle:
    text: str
    asset_count: int
    char_count: int
    truncated: bool


class OutlineService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def generate(
        self,
        owner: User,
        course_id: UUID,
        payload: OutlineGenerateRequest,
    ) -> CourseOutlineResponse:
        course = await self._get_course(owner.id, course_id)
        materials = await self._load_materials(owner.id, course.id)
        resolved = await ProviderConfigService(
            self.db, self.settings
        ).resolve_for_generation(owner, payload.provider_id)
        if payload.model:
            resolved.default_model = payload.model

        course_title = course.title
        description = course.description
        await self.db.commit()

        normalized = await self._generate_normalized_outline(
            resolved=resolved,
            course_title=course_title,
            description=description,
            instructions=payload.instructions,
            target_scene_count=payload.scene_count,
            materials=materials,
        )
        return await self._create_version(
            owner=owner,
            course_id=course.id,
            source="generated",
            provider_id=resolved.spec.id,
            model_name=resolved.default_model,
            normalized=normalized,
            material_asset_count=materials.asset_count,
            material_char_count=materials.char_count,
            materials_truncated=materials.truncated,
        )

    async def get_latest(
        self, owner: User, course_id: UUID
    ) -> CourseOutlineResponse:
        await self._get_course(owner.id, course_id)
        record = await self.db.scalar(
            select(CourseOutline)
            .where(
                CourseOutline.owner_id == owner.id,
                CourseOutline.course_id == course_id,
            )
            .order_by(CourseOutline.version.desc())
            .limit(1)
        )
        if record is None:
            raise AppError(404, "OUTLINE_NOT_FOUND", "Course outline not found")
        return self._to_response(record)

    async def save_manual(
        self,
        owner: User,
        course_id: UUID,
        payload: OutlineSaveRequest,
    ) -> CourseOutlineResponse:
        course = await self._get_course(owner.id, course_id)
        latest = await self.db.scalar(
            select(CourseOutline)
            .where(
                CourseOutline.owner_id == owner.id,
                CourseOutline.course_id == course_id,
            )
            .order_by(CourseOutline.version.desc())
            .limit(1)
        )
        try:
            normalized = normalize_manual_outline(
                payload.course_title or (latest.course_title if latest else course.title),
                payload.language_directive
                or (latest.language_directive if latest else ""),
                payload.scenes,
            )
        except OutlineParseError as exc:
            raise AppError(422, "OUTLINE_INVALID", str(exc)) from exc
        return await self._create_version(
            owner=owner,
            course_id=course.id,
            source="manual",
            provider_id=None,
            model_name=None,
            normalized=normalized,
            material_asset_count=0,
            material_char_count=0,
            materials_truncated=False,
        )

    async def _generate_normalized_outline(
        self,
        *,
        resolved: ResolvedProvider,
        course_title: str,
        description: Optional[str],
        instructions: Optional[str],
        target_scene_count: Optional[int],
        materials: MaterialBundle,
    ) -> NormalizedOutline:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.outline_generation_timeout_seconds
        last_error = ""
        for attempt in range(2):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AppError(
                    504,
                    "OUTLINE_GENERATION_TIMEOUT",
                    "Outline generation timed out",
                )
            user_prompt = build_outline_user_prompt(
                course_title=course_title,
                description=description,
                instructions=instructions,
                target_scene_count=target_scene_count,
                materials=materials.text,
                materials_truncated=materials.truncated,
            )
            if attempt:
                user_prompt += (
                    "\n\nYour previous response could not be used: "
                    f"{last_error}. Return a corrected JSON object only."
                )
            try:
                raw_text = await asyncio.wait_for(
                    self._call_provider(resolved, SYSTEM_PROMPT, user_prompt),
                    timeout=remaining,
                )
                return normalize_generated_outline(raw_text, course_title)
            except OutlineParseError as exc:
                last_error = str(exc)[:300]
                logger.warning(
                    "outline_parse_retry provider=%s attempt=%s error=%s",
                    resolved.spec.id,
                    attempt + 1,
                    type(exc).__name__,
                )
            except asyncio.TimeoutError as exc:
                raise AppError(
                    504,
                    "OUTLINE_GENERATION_TIMEOUT",
                    "Outline generation timed out",
                ) from exc
        raise AppError(
            502,
            "OUTLINE_INVALID_RESPONSE",
            "Model did not return a valid course outline",
            details={"reason": last_error or "Unknown parsing error"},
        )

    async def _call_provider(
        self,
        resolved: ResolvedProvider,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if resolved.spec.protocol == "anthropic":
            return await self._call_anthropic(resolved, system_prompt, user_prompt)
        return await self._call_openai_compatible(
            resolved, system_prompt, user_prompt
        )

    async def _call_openai_compatible(
        self,
        resolved: ResolvedProvider,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        timeout = self.settings.outline_generation_timeout_seconds
        async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        )
        try:
            chat = ChatOpenAI(
                model=resolved.default_model,
                api_key=SecretStr(resolved.api_key or "local-no-key"),
                base_url=resolved.base_url,
                temperature=0.4,
                max_tokens=4096,
                timeout=timeout,
                max_retries=0,
                http_async_client=async_client,
            )
            result = await chat.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            return self._message_text(result.content)
        except AppError:
            raise
        except Exception as exc:
            logger.warning(
                "outline_provider_failed provider=%s model=%s error=%s",
                resolved.spec.id,
                resolved.default_model,
                type(exc).__name__,
            )
            if "timeout" in type(exc).__name__.lower():
                raise AppError(
                    504,
                    "OUTLINE_GENERATION_TIMEOUT",
                    "Outline generation timed out",
                ) from exc
            raise AppError(
                502,
                "OUTLINE_PROVIDER_ERROR",
                "Model provider failed to generate the outline",
            ) from exc
        finally:
            await async_client.aclose()

    async def _call_anthropic(
        self,
        resolved: ResolvedProvider,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        headers = {
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if resolved.api_key:
            headers["x-api-key"] = resolved.api_key
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self.settings.outline_generation_timeout_seconds
                ),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{resolved.base_url}/messages",
                    headers=headers,
                    json={
                        "model": resolved.default_model,
                        "max_tokens": 4096,
                        "temperature": 0.4,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                )
        except httpx.TimeoutException as exc:
            raise AppError(
                504,
                "OUTLINE_GENERATION_TIMEOUT",
                "Outline generation timed out",
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                502,
                "OUTLINE_PROVIDER_ERROR",
                "Model provider could not be reached",
            ) from exc

        if response.status_code != 200:
            if response.status_code in {401, 403}:
                message = "Model provider rejected the configured credentials"
            else:
                message = "Model provider failed to generate the outline"
            logger.warning(
                "outline_anthropic_failed status=%s provider=%s model=%s",
                response.status_code,
                resolved.spec.id,
                resolved.default_model,
            )
            raise AppError(502, "OUTLINE_PROVIDER_ERROR", message)
        try:
            payload = response.json()
            blocks = payload.get("content", []) if isinstance(payload, dict) else []
            text = "".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except (ValueError, TypeError) as exc:
            raise AppError(
                502,
                "OUTLINE_PROVIDER_ERROR",
                "Model provider returned an invalid response",
            ) from exc
        return text

    def _message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        return str(content)

    async def _load_materials(self, owner_id: UUID, course_id: UUID) -> MaterialBundle:
        result = await self.db.execute(
            select(
                Asset.filename,
                AssetExtraction.text_content,
                AssetExtraction.truncated,
            )
            .join(AssetExtraction, AssetExtraction.asset_id == Asset.id)
            .where(
                Asset.owner_id == owner_id,
                Asset.course_id == course_id,
                Asset.deleted_at.is_(None),
                Asset.extraction_status == "ready",
            )
            .order_by(Asset.created_at, Asset.id)
        )
        rows = result.all()
        remaining = self.settings.outline_material_char_limit
        chunks: List[str] = []
        asset_count = 0
        char_count = 0
        truncated = False
        for filename, text_content, extraction_truncated in rows:
            text = (text_content or "").strip()
            if not text:
                continue
            header = f"\n\n[Source: {filename}]\n"
            if remaining <= len(header):
                truncated = True
                break
            available = remaining - len(header)
            content = text[:available]
            if len(content) < len(text):
                truncated = True
            chunks.append(header + content)
            asset_count += 1
            char_count += len(content)
            remaining -= len(header) + len(content)
            if extraction_truncated:
                truncated = True
            if remaining <= 0:
                truncated = True
                break
        return MaterialBundle(
            text="".join(chunks).strip(),
            asset_count=asset_count,
            char_count=char_count,
            truncated=truncated,
        )

    async def _create_version(
        self,
        *,
        owner: User,
        course_id: UUID,
        source: str,
        provider_id: Optional[str],
        model_name: Optional[str],
        normalized: NormalizedOutline,
        material_asset_count: int,
        material_char_count: int,
        materials_truncated: bool,
    ) -> CourseOutlineResponse:
        course = await self.db.scalar(
            select(Course)
            .where(
                Course.id == course_id,
                Course.owner_id == owner.id,
                Course.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if course is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
        latest_version = await self.db.scalar(
            select(func.max(CourseOutline.version)).where(
                CourseOutline.course_id == course.id
            )
        )
        record = CourseOutline(
            owner_id=owner.id,
            course_id=course.id,
            version=int(latest_version or 0) + 1,
            source=source,
            provider_id=provider_id,
            model_name=model_name,
            language_directive=normalized.language_directive,
            course_title=normalized.course_title,
            scenes=normalized.scenes,
            prompt_version="outline-v1",
            material_asset_count=material_asset_count,
            material_char_count=material_char_count,
            materials_truncated=materials_truncated,
        )
        self.db.add(record)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(
                409,
                "OUTLINE_VERSION_CONFLICT",
                "Outline version could not be saved; retry the request",
            ) from exc
        await self.db.refresh(record)
        return self._to_response(record)

    async def _get_course(self, owner_id: UUID, course_id: UUID) -> Course:
        course = await self.db.scalar(
            select(Course).where(
                Course.id == course_id,
                Course.owner_id == owner_id,
                Course.deleted_at.is_(None),
            )
        )
        if course is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
        return course

    def _to_response(self, record: CourseOutline) -> CourseOutlineResponse:
        scenes = [OutlineScene.model_validate(scene) for scene in record.scenes]
        return CourseOutlineResponse(
            id=record.id,
            course_id=record.course_id,
            version=record.version,
            source=record.source,  # type: ignore[arg-type]
            provider_id=record.provider_id,
            model_name=record.model_name,
            language_directive=record.language_directive,
            course_title=record.course_title,
            scenes=scenes,
            scene_count=len(scenes),
            material_asset_count=record.material_asset_count,
            material_char_count=record.material_char_count,
            materials_truncated=record.materials_truncated,
            prompt_version=record.prompt_version,
            created_at=record.created_at,
        )

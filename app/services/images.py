import base64
import hashlib
import html
import io
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError
from app.models.classroom import CourseScene, CourseStage, GenerationJob
from app.models.course import Course
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.classroom import GenerationJobView, IllustrationGenerateRequest
from app.services import generation_jobs
from app.services.media_storage import LocalMediaStorage
from app.services.providers import ProviderConfigService, ResolvedProvider

logger = logging.getLogger(__name__)

ILLUSTRATION_ACTION_ID = "illustration"
QWEN_PROVIDER_ID = "qwen"
POLLINATIONS_PROVIDER_ID = "pollinations"
HUGGINGFACE_PROVIDER_ID = "huggingface"
CLOUDFLARE_PROVIDER_ID = "cloudflare"
SUPPORTED_PROVIDER_IDS = {
    QWEN_PROVIDER_ID,
    POLLINATIONS_PROVIDER_ID,
    HUGGINGFACE_PROVIDER_ID,
    CLOUDFLARE_PROVIDER_ID,
}
IMAGE_STYLE_DIRECTIVE = (
    "16:9 横版教学插画。深墨绿、暖赭红与米白的克制配色，柔和电影光，"
    "细腻编辑手绘质感。将主要视觉集中在画面右侧，左侧保留安静的文字安全区。"
    "不要出现文字、字母、数字、公式、水印、logo、边框或 UI 元素。"
)


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    width: int
    height: int
    provider_id: str
    model: str


class IllustrationService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.storage = LocalMediaStorage(settings)

    async def create_job(
        self,
        owner: User,
        course_id: UUID,
        payload: IllustrationGenerateRequest,
        scene_id: Optional[UUID] = None,
    ) -> GenerationJobView:
        course = await self._get_course(owner.id, course_id)
        stage = await self._latest_stage(owner.id, course.id)
        if stage is None:
            raise AppError(
                404,
                "CLASSROOM_NOT_FOUND",
                "Generate a classroom before adding illustrations",
            )
        if payload.provider_id and payload.provider_id not in SUPPORTED_PROVIDER_IDS:
            raise AppError(
                422,
                "IMAGE_PROVIDER_NOT_SUPPORTED",
                "Illustrations support qwen, pollinations, huggingface or cloudflare",
            )

        slide_scenes = await self._slide_scenes(owner.id, stage.id)
        if scene_id is not None:
            selected = next((scene for scene in slide_scenes if scene.id == scene_id), None)
            if selected is None:
                raise AppError(404, "IMAGE_SCENE_NOT_FOUND", "Slide scene not found")
            if not await self._has_illustration(owner.id, stage.id, selected.id):
                image_count = await self._illustration_count(owner.id, stage.id)
                if image_count >= self.settings.image_max_per_classroom:
                    raise AppError(
                        422,
                        "IMAGE_LIMIT_REACHED",
                        "This classroom already has the maximum number of illustrations",
                    )
            scenes = [selected]
        else:
            scenes = await self._pending_scenes(
                owner.id,
                stage.id,
                slide_scenes,
                regenerate=payload.regenerate,
            )

        selected_provider = payload.provider_id or self.settings.image_provider_id
        selected_model = (
            payload.model
            or (
                self.settings.image_pollinations_model
                if selected_provider == POLLINATIONS_PROVIDER_ID
                else self.settings.image_model
            )
        )
        job = await generation_jobs.create_job(
            self.db,
            owner_id=owner.id,
            course_id=course.id,
            kind=generation_jobs.JOB_KIND_IMAGE,
            stage_id=stage.id,
            provider_id=selected_provider,
            model_name=selected_model,
            payload={
                "stage_id": str(stage.id),
                "scene_ids": [str(scene.id) for scene in scenes],
                "provider_id": payload.provider_id,
                "model": payload.model,
                "prompt": payload.prompt,
                "instructions": payload.instructions,
                "regenerate": payload.regenerate,
            },
            message="Queued illustration generation",
        )
        return generation_jobs.job_view(job)

    async def run_job(self, job_id: UUID) -> None:
        job = await self.db.get(GenerationJob, job_id)
        if job is None or job.kind != generation_jobs.JOB_KIND_IMAGE:
            logger.warning("illustration_job_missing job_id=%s", job_id)
            return
        await generation_jobs.mark_running(
            self.db, job_id, progress=5, message="Preparing illustrations"
        )
        try:
            owner = await self.db.get(User, job.owner_id)
            if owner is None:
                raise AppError(404, "OWNER_NOT_FOUND", "Course owner no longer exists")
            course = await self._get_course(owner.id, job.course_id)
            stage = await self._stage_for_job(owner.id, course.id, job.payload)
            if stage is None:
                raise AppError(404, "CLASSROOM_NOT_FOUND", "Classroom not found")
            scenes = await self._scenes_for_job(owner.id, stage.id, job.payload)
            if not scenes:
                raise AppError(
                    422, "IMAGE_NO_SCENES", "No slide scenes are available"
                )

            total = len(scenes)
            generated = 0
            reused = 0
            actual_provider: Optional[str] = None
            actual_model: Optional[str] = None
            for index, scene in enumerate(scenes, start=1):
                await generation_jobs.mark_progress(
                    self.db,
                    job_id,
                    progress=5 + int(88 * (index - 1) / total),
                    message=f"Illustrating scene {index}/{total}",
                )
                outcome, provider_id, model = await self._illustrate_scene(
                    owner=owner,
                    course=course,
                    stage=stage,
                    scene=scene,
                    payload=job.payload,
                )
                if outcome == "generated":
                    generated += 1
                else:
                    reused += 1
                actual_provider = provider_id
                actual_model = model

            if actual_provider:
                job.provider_id = actual_provider
                job.model_name = actual_model
            produced = generated + reused
            await generation_jobs.complete_job(
                self.db,
                job_id,
                message=f"Illustrations ready for {produced} scene(s)",
                stage_id=stage.id,
                scene_count=produced,
            )
            logger.info(
                "illustration_job_completed job_id=%s generated=%s reused=%s",
                job_id,
                generated,
                reused,
            )
        except AppError as exc:
            await self.db.rollback()
            await generation_jobs.fail_job(self.db, job_id, exc.code, exc.message)
            logger.warning(
                "illustration_generation_failed job_id=%s code=%s", job_id, exc.code
            )
        except Exception:
            await self.db.rollback()
            logger.exception(
                "illustration_generation_failed job_id=%s course_id=%s",
                job_id,
                job.course_id,
            )
            await generation_jobs.fail_job(
                self.db,
                job_id,
                "IMAGE_GENERATION_FAILED",
                "Illustration generation failed",
            )

    async def _illustrate_scene(
        self,
        *,
        owner: User,
        course: Course,
        stage: CourseStage,
        scene: CourseScene,
        payload: Dict[str, Any],
    ) -> Tuple[str, str, str]:
        prompt = _illustration_prompt(scene, payload)
        prompt = prompt[:3500]
        requested_provider = _payload_text(payload, "provider_id")
        qwen_model = _payload_text(payload, "model") or self.settings.image_model
        pollinations_model = (
            _payload_text(payload, "model")
            if requested_provider == POLLINATIONS_PROVIDER_ID
            else self.settings.image_pollinations_model
        )
        known_hashes = tuple(
            _content_hash(
                provider_id=provider_id,
                model=self._provider_model(provider_id, qwen_model, pollinations_model),
                prompt=prompt,
                width=self.settings.image_target_width,
                height=self.settings.image_target_height,
            )
            for provider_id in self._attempt_order(requested_provider)
        )
        regenerate = bool(payload.get("regenerate"))
        asset = await self._scene_asset(owner.id, stage.id, scene.id)
        if (
            asset is not None
            and not regenerate
            and asset.content_hash in known_hashes
            and self.storage.exists(owner.id, asset.id, asset.storage_key)
        ):
            await self._attach_illustration(scene, asset)
            return "reused", asset.provider_id or "qwen", asset.model_name or qwen_model

        cached = None
        if not regenerate:
            cached = await self.db.scalar(
                select(MediaAsset)
                .where(
                    MediaAsset.owner_id == owner.id,
                    MediaAsset.kind == "image",
                    MediaAsset.content_hash.in_(known_hashes),
                )
                .order_by(MediaAsset.created_at.desc())
                .limit(1)
            )
        if cached is not None and (
            asset is None or cached.id != asset.id
        ) and self.storage.exists(owner.id, cached.id, cached.storage_key):
            data = self.storage.resolve(
                owner.id, cached.id, cached.storage_key
            ).read_bytes()
            generated = GeneratedImage(
                data=data,
                width=int(cached.media_metadata.get("width") or 0),
                height=int(cached.media_metadata.get("height") or 0),
                provider_id=cached.provider_id or "qwen",
                model=cached.model_name or qwen_model,
            )
            content_hash = cached.content_hash
        else:
            generated = await self._obtain_image(
                owner=owner,
                prompt=prompt,
                requested_provider=requested_provider,
                qwen_model=qwen_model,
                pollinations_model=pollinations_model,
            )
            content_hash = _content_hash(
                provider_id=generated.provider_id,
                model=generated.model,
                prompt=prompt,
                width=self.settings.image_target_width,
                height=self.settings.image_target_height,
            )

        media_id = asset.id if asset is not None else uuid4()
        saved = await self.storage.save_bytes(
            owner.id, media_id, "illustration", "webp", generated.data
        )
        if asset is None:
            asset = MediaAsset(
                id=media_id,
                owner_id=owner.id,
                course_id=course.id,
                stage_id=stage.id,
                scene_id=scene.id,
                action_id=ILLUSTRATION_ACTION_ID,
                kind="image",
            )
            self.db.add(asset)
        asset.provider_id = generated.provider_id
        asset.model_name = generated.model
        asset.format = "webp"
        asset.mime_type = "image/webp"
        asset.size_bytes = saved.size_bytes
        asset.sha256 = saved.sha256
        asset.content_hash = content_hash
        asset.storage_key = saved.storage_key
        asset.media_metadata = {
            "width": generated.width,
            "height": generated.height,
            "prompt": prompt,
            "source": "generated",
        }
        await self._attach_illustration(scene, asset)
        await self.db.commit()
        return "generated", generated.provider_id, generated.model

    async def _obtain_image(
        self,
        *,
        owner: User,
        prompt: str,
        requested_provider: Optional[str],
        qwen_model: str,
        pollinations_model: Optional[str],
    ) -> GeneratedImage:
        failures: List[str] = []
        for provider_id in self._attempt_order(requested_provider):
            try:
                return await self._generate_with(
                    provider_id,
                    owner=owner,
                    prompt=prompt,
                    qwen_model=qwen_model,
                    pollinations_model=pollinations_model,
                )
            except AppError as exc:
                failures.append(f"{provider_id}:{exc.code}")
                logger.warning(
                    "image_provider_failed provider=%s code=%s message=%s",
                    provider_id,
                    exc.code,
                    exc.message,
                )
            except Exception as exc:
                failures.append(f"{provider_id}:{type(exc).__name__}")
                logger.warning(
                    "image_provider_failed provider=%s error=%s",
                    provider_id,
                    type(exc).__name__,
                )
        raise AppError(
            502,
            "IMAGE_PROVIDER_ERROR",
            "Every image provider failed: " + ", ".join(failures),
        )

    def _provider_model(
        self,
        provider_id: str,
        qwen_model: str,
        pollinations_model: Optional[str],
    ) -> str:
        settings = self.settings
        if provider_id == QWEN_PROVIDER_ID:
            return qwen_model
        if provider_id == POLLINATIONS_PROVIDER_ID:
            return pollinations_model or settings.image_pollinations_model
        if provider_id == HUGGINGFACE_PROVIDER_ID:
            return settings.image_hf_model
        if provider_id == CLOUDFLARE_PROVIDER_ID:
            return settings.image_cloudflare_model
        return provider_id

    def _attempt_order(self, requested_provider: Optional[str]) -> List[str]:
        if requested_provider:
            return [requested_provider]
        primary = self.settings.image_provider_id.strip().lower()
        chain = [
            item.strip().lower()
            for item in self.settings.image_fallback_chain.split(",")
            if item.strip()
        ]
        return [primary] + [item for item in chain if item != primary]

    async def _generate_with(
        self,
        provider_id: str,
        *,
        owner: User,
        prompt: str,
        qwen_model: str,
        pollinations_model: Optional[str],
    ) -> GeneratedImage:
        settings = self.settings
        if provider_id == QWEN_PROVIDER_ID:
            resolved = await ProviderConfigService(
                self.db, settings
            ).resolve_for_generation(owner, QWEN_PROVIDER_ID)
            return await generate_qwen_image(
                resolved,
                prompt=prompt,
                model=qwen_model,
                timeout=settings.image_generation_timeout_seconds,
                max_bytes=settings.image_max_download_bytes,
                max_width=settings.image_max_width,
                max_height=settings.image_max_height,
                webp_quality=settings.image_webp_quality,
            )
        if provider_id == POLLINATIONS_PROVIDER_ID:
            return await generate_pollinations_image(
                prompt=prompt,
                model=pollinations_model or settings.image_pollinations_model,
                base_url=settings.image_pollinations_base_url,
                width=settings.image_target_width,
                height=settings.image_target_height,
                timeout=settings.image_generation_timeout_seconds,
                max_bytes=settings.image_max_download_bytes,
                max_width=settings.image_max_width,
                max_height=settings.image_max_height,
                webp_quality=settings.image_webp_quality,
            )
        if provider_id == HUGGINGFACE_PROVIDER_ID:
            return await generate_huggingface_image(
                endpoint=settings.image_hf_endpoint,
                model=settings.image_hf_model,
                token=settings.image_hf_token,
                prompt=prompt,
                width=settings.image_target_width,
                height=settings.image_target_height,
                timeout=settings.image_generation_timeout_seconds,
                max_bytes=settings.image_max_download_bytes,
                max_width=settings.image_max_width,
                max_height=settings.image_max_height,
                webp_quality=settings.image_webp_quality,
            )
        if provider_id == CLOUDFLARE_PROVIDER_ID:
            return await generate_cloudflare_image(
                account_id=settings.image_cloudflare_account_id,
                token=settings.image_cloudflare_api_token,
                model=settings.image_cloudflare_model,
                prompt=prompt,
                timeout=settings.image_generation_timeout_seconds,
                max_bytes=settings.image_max_download_bytes,
                max_width=settings.image_max_width,
                max_height=settings.image_max_height,
                webp_quality=settings.image_webp_quality,
            )
        raise AppError(
            422,
            "IMAGE_PROVIDER_NOT_SUPPORTED",
            f"Unknown image provider: {provider_id}",
        )

    async def _attach_illustration(
        self, scene: CourseScene, asset: MediaAsset
    ) -> None:
        metadata = asset.media_metadata if isinstance(asset.media_metadata, dict) else {}
        content = dict(scene.content or {})
        content["illustration"] = {
            "mediaId": str(asset.id),
            "format": asset.format,
            "mimeType": asset.mime_type,
            "width": int(metadata.get("width") or 0),
            "height": int(metadata.get("height") or 0),
            "providerId": asset.provider_id,
            "model": asset.model_name,
            "sha256": asset.sha256,
            "updatedAt": asset.updated_at.isoformat() if asset.updated_at else None,
            "alt": scene.title,
        }
        scene.content = content
        await self.db.flush()

    async def _scene_asset(
        self, owner_id: UUID, stage_id: UUID, scene_id: UUID
    ) -> Optional[MediaAsset]:
        return await self.db.scalar(
            select(MediaAsset).where(
                MediaAsset.owner_id == owner_id,
                MediaAsset.stage_id == stage_id,
                MediaAsset.scene_id == scene_id,
                MediaAsset.action_id == ILLUSTRATION_ACTION_ID,
                MediaAsset.kind == "image",
            )
        )

    async def _has_illustration(
        self, owner_id: UUID, stage_id: UUID, scene_id: UUID
    ) -> bool:
        return await self._scene_asset(owner_id, stage_id, scene_id) is not None

    async def _pending_scenes(
        self,
        owner_id: UUID,
        stage_id: UUID,
        slide_scenes: List[CourseScene],
        *,
        regenerate: bool,
    ) -> List[CourseScene]:
        if not slide_scenes:
            raise AppError(
                422,
                "IMAGE_NO_SCENES",
                "No slide scenes are available for illustration",
            )
        limit = self.settings.image_max_per_classroom
        if regenerate:
            return slide_scenes[:limit]
        illustrated = await self._illustrated_scene_ids(owner_id, stage_id)
        remaining = limit - await self._illustration_count(owner_id, stage_id)
        if remaining <= 0:
            raise AppError(
                422,
                "IMAGE_LIMIT_REACHED",
                "This classroom already has the maximum number of illustrations",
            )
        pending = [scene for scene in slide_scenes if scene.id not in illustrated]
        if not pending:
            raise AppError(
                422,
                "IMAGE_NO_SCENES",
                "Every slide already has an illustration",
            )
        return pending[:remaining]

    async def _illustrated_scene_ids(
        self, owner_id: UUID, stage_id: UUID
    ) -> Set[UUID]:
        rows = await self.db.scalars(
            select(MediaAsset.scene_id).where(
                MediaAsset.owner_id == owner_id,
                MediaAsset.stage_id == stage_id,
                MediaAsset.action_id == ILLUSTRATION_ACTION_ID,
                MediaAsset.kind == "image",
                MediaAsset.scene_id.is_not(None),
            )
        )
        return {scene_id for scene_id in rows.all() if scene_id is not None}
    async def _illustration_count(self, owner_id: UUID, stage_id: UUID) -> int:
        count = await self.db.scalar(
            select(func.count(MediaAsset.id)).where(
                MediaAsset.owner_id == owner_id,
                MediaAsset.stage_id == stage_id,
                MediaAsset.action_id == ILLUSTRATION_ACTION_ID,
                MediaAsset.kind == "image",
            )
        )
        return int(count or 0)

    async def _slide_scenes(
        self, owner_id: UUID, stage_id: UUID
    ) -> List[CourseScene]:
        return list(
            (
                await self.db.scalars(
                    select(CourseScene)
                    .where(
                        CourseScene.owner_id == owner_id,
                        CourseScene.stage_id == stage_id,
                        CourseScene.scene_type == "slide",
                    )
                    .order_by(CourseScene.order_index)
                )
            ).all()
        )

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

    async def _latest_stage(
        self, owner_id: UUID, course_id: UUID
    ) -> Optional[CourseStage]:
        return await self.db.scalar(
            select(CourseStage)
            .where(
                CourseStage.owner_id == owner_id,
                CourseStage.course_id == course_id,
            )
            .order_by(CourseStage.version.desc())
            .limit(1)
        )

    async def _stage_for_job(
        self, owner_id: UUID, course_id: UUID, payload: Dict[str, Any]
    ) -> Optional[CourseStage]:
        raw_stage_id = payload.get("stage_id")
        if isinstance(raw_stage_id, str):
            try:
                stage_id = UUID(raw_stage_id)
            except ValueError:
                stage_id = None
            if stage_id is not None:
                stage = await self.db.scalar(
                    select(CourseStage).where(
                        CourseStage.id == stage_id,
                        CourseStage.owner_id == owner_id,
                        CourseStage.course_id == course_id,
                    )
                )
                if stage is not None:
                    return stage
        return await self._latest_stage(owner_id, course_id)

    async def _scenes_for_job(
        self, owner_id: UUID, stage_id: UUID, payload: Dict[str, Any]
    ) -> List[CourseScene]:
        scenes = await self._slide_scenes(owner_id, stage_id)
        wanted = payload.get("scene_ids")
        if isinstance(wanted, list):
            selected = {str(item) for item in wanted}
            scenes = [scene for scene in scenes if str(scene.id) in selected]
        return scenes


async def generate_qwen_image(
    resolved: ResolvedProvider,
    *,
    prompt: str,
    model: str,
    timeout: float,
    max_bytes: int,
    max_width: int,
    max_height: int,
    webp_quality: int,
) -> GeneratedImage:
    url = _qwen_image_generation_url(resolved.base_url)
    headers = {"Content-Type": "application/json"}
    if resolved.api_key:
        headers["Authorization"] = f"Bearer {resolved.api_key}"
    request = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
        },
        "parameters": {"prompt_extend": True},
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=request)
    except httpx.TimeoutException as exc:
        raise AppError(504, "IMAGE_TIMEOUT", "Image generation timed out") from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502, "IMAGE_PROVIDER_ERROR", "Image provider could not be reached"
        ) from exc
    if response.status_code != 200:
        raise AppError(
            502,
            "IMAGE_PROVIDER_ERROR",
            "Image provider returned HTTP "
            f"{response.status_code}: {_response_error(response)}",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            502, "IMAGE_INVALID_RESPONSE", "Image provider returned invalid JSON"
        ) from exc
    source = _qwen_image_source(payload)
    if source.startswith("data:"):
        data = _decode_data_uri(source)
    else:
        data = await _download_image(source, timeout=timeout, max_bytes=max_bytes)
    normalized = await normalize_image(
        data,
        max_width=max_width,
        max_height=max_height,
        quality=webp_quality,
    )
    return GeneratedImage(
        data=normalized[0],
        width=normalized[1],
        height=normalized[2],
        provider_id="qwen",
        model=model,
    )


async def generate_pollinations_image(
    *,
    prompt: str,
    model: str,
    base_url: str,
    width: int,
    height: int,
    timeout: float,
    max_bytes: int,
    max_width: int,
    max_height: int,
    webp_quality: int,
) -> GeneratedImage:
    seed = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8], 16)
    url = _pollinations_url(
        base_url=base_url,
        prompt=prompt,
        model=model,
        width=width,
        height=height,
        seed=seed,
    )
    data = await _download_image(url, timeout=timeout, max_bytes=max_bytes)
    normalized = await normalize_image(
        data,
        max_width=max_width,
        max_height=max_height,
        quality=webp_quality,
    )
    return GeneratedImage(
        data=normalized[0],
        width=normalized[1],
        height=normalized[2],
        provider_id=POLLINATIONS_PROVIDER_ID,
        model=model,
    )


def _provider_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,
    )


async def generate_huggingface_image(
    *,
    endpoint: str,
    model: str,
    token: str,
    prompt: str,
    width: int,
    height: int,
    timeout: float,
    max_bytes: int,
    max_width: int,
    max_height: int,
    webp_quality: int,
) -> GeneratedImage:
    if not token.strip():
        raise AppError(
            422,
            "IMAGE_PROVIDER_NOT_CONFIGURED",
            "IMAGE_HF_TOKEN is required for the huggingface image provider",
        )
    if not model.strip():
        raise AppError(
            422,
            "IMAGE_PROVIDER_NOT_CONFIGURED",
            "IMAGE_HF_MODEL must name a text-to-image model",
        )
    url = f"{endpoint.rstrip('/')}/{model.strip()}"
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "image/*",
    }
    request = {
        "inputs": prompt,
        "parameters": {"width": width, "height": height},
    }
    try:
        async with _provider_client(timeout) as client:
            response = await client.post(url, headers=headers, json=request)
    except httpx.TimeoutException as exc:
        raise AppError(504, "IMAGE_TIMEOUT", "Image generation timed out") from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502, "IMAGE_PROVIDER_ERROR", "Image provider could not be reached"
        ) from exc
    data = _provider_image_bytes(response, max_bytes)
    return await _normalize_generated(
        data,
        provider_id=HUGGINGFACE_PROVIDER_ID,
        model=model.strip(),
        max_width=max_width,
        max_height=max_height,
        webp_quality=webp_quality,
    )


def _cloudflare_image_url(account_id: str, model: str) -> str:
    account = account_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9]+", account):
        raise AppError(
            422,
            "IMAGE_PROVIDER_NOT_CONFIGURED",
            "IMAGE_CLOUDFLARE_ACCOUNT_ID is not a valid account id",
        )
    model_id = model.strip().lstrip("/")
    if not model_id.startswith("@"):
        model_id = f"@cf/{model_id}"
    segments = model_id.split("/")
    if any(segment in {".", ".."} for segment in segments) or not re.fullmatch(
        r"@[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+", model_id
    ):
        raise AppError(
            422,
            "IMAGE_PROVIDER_NOT_CONFIGURED",
            "IMAGE_CLOUDFLARE_MODEL is not a valid model id",
        )
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account}/ai/run/{model_id}"
    )


async def generate_cloudflare_image(
    *,
    account_id: str,
    token: str,
    model: str,
    prompt: str,
    timeout: float,
    max_bytes: int,
    max_width: int,
    max_height: int,
    webp_quality: int,
) -> GeneratedImage:
    if not account_id.strip() or not token.strip():
        raise AppError(
            422,
            "IMAGE_PROVIDER_NOT_CONFIGURED",
            "IMAGE_CLOUDFLARE_ACCOUNT_ID and IMAGE_CLOUDFLARE_API_TOKEN are required",
        )
    url = _cloudflare_image_url(account_id, model)
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "image/*, application/json",
    }
    try:
        async with _provider_client(timeout) as client:
            response = await client.post(url, headers=headers, json={"prompt": prompt})
    except httpx.TimeoutException as exc:
        raise AppError(504, "IMAGE_TIMEOUT", "Image generation timed out") from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502, "IMAGE_PROVIDER_ERROR", "Image provider could not be reached"
        ) from exc
    data = _provider_image_bytes(response, max_bytes)
    return await _normalize_generated(
        data,
        provider_id=CLOUDFLARE_PROVIDER_ID,
        model=model.strip(),
        max_width=max_width,
        max_height=max_height,
        webp_quality=webp_quality,
    )


def _provider_image_bytes(response: httpx.Response, max_bytes: int) -> bytes:
    if response.status_code != 200:
        raise AppError(
            502,
            "IMAGE_PROVIDER_ERROR",
            "Image provider returned HTTP "
            f"{response.status_code}: {_response_error(response)}",
        )
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        data = response.content
    else:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AppError(
                502, "IMAGE_INVALID_RESPONSE", "Image provider returned invalid JSON"
            ) from exc
        data = _json_image_bytes(payload)
    if not data:
        raise AppError(502, "IMAGE_INVALID_RESPONSE", "Generated image is empty")
    if len(data) > max_bytes:
        raise AppError(
            502, "IMAGE_TOO_LARGE", "Generated image exceeds the size limit"
        )
    return data


def _json_image_bytes(payload: Any) -> bytes:
    candidates: List[Any] = []
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            candidates.append(result.get("image"))
            nested = result.get("images")
            if isinstance(nested, list):
                candidates.extend(nested)
        elif isinstance(result, list):
            candidates.extend(result)
        candidates.append(payload.get("image"))
        nested = payload.get("images")
        if isinstance(nested, list):
            candidates.extend(nested)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _image_bytes_from_string(candidate.strip())
    raise AppError(502, "IMAGE_INVALID_RESPONSE", "Image provider returned no image")


def _image_bytes_from_string(value: str) -> bytes:
    if value.startswith("data:"):
        return _decode_data_uri(value)
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise AppError(
            502, "IMAGE_INVALID_RESPONSE", "Image provider returned invalid image data"
        ) from exc
    if not data:
        raise AppError(502, "IMAGE_INVALID_RESPONSE", "Image provider returned no image")
    return data


async def _normalize_generated(
    data: bytes,
    *,
    provider_id: str,
    model: str,
    max_width: int,
    max_height: int,
    webp_quality: int,
) -> GeneratedImage:
    normalized = await normalize_image(
        data,
        max_width=max_width,
        max_height=max_height,
        quality=webp_quality,
    )
    return GeneratedImage(
        data=normalized[0],
        width=normalized[1],
        height=normalized[2],
        provider_id=provider_id,
        model=model,
    )
async def normalize_image(
    data: bytes, *, max_width: int, max_height: int, quality: int
) -> Tuple[bytes, int, int]:
    return await run_in_threadpool(
        _normalize_image_sync, data, max_width, max_height, quality
    )


def _normalize_image_sync(
    data: bytes, max_width: int, max_height: int, quality: int
) -> Tuple[bytes, int, int]:
    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AppError(
            502, "IMAGE_INVALID_RESPONSE", "Image provider returned invalid image data"
        ) from exc
    image: Image.Image = ImageOps.exif_transpose(opened) or opened
    image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    output = io.BytesIO()
    clean.save(
        output,
        format="WEBP",
        quality=max(50, min(95, quality)),
        method=6,
    )
    result = output.getvalue()
    if not result:
        raise AppError(502, "IMAGE_INVALID_RESPONSE", "Image normalization failed")
    return result, clean.width, clean.height


def _qwen_image_generation_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    path = (parsed.path or "").rstrip("/")
    suffix = "/compatible-mode/v1"
    if path.endswith(suffix):
        path = path[: -len(suffix)]
    if path.endswith("/api/v1"):
        root = path
    else:
        root = f"{path}/api/v1" if path else "/api/v1"
    return (
        f"{parsed.scheme}://{parsed.netloc}{root}"
        "/services/aigc/multimodal-generation/generation"
    )


def _pollinations_url(
    *, base_url: str, prompt: str, model: str, width: int, height: int, seed: int
) -> str:
    encoded = quote(prompt, safe="")
    return (
        f"{base_url.rstrip('/')}/{encoded}"
        f"?width={width}&height={height}&model={quote(model, safe='')}"
        f"&seed={seed}&nologo=true"
    )


def _qwen_image_source(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise AppError(
            502, "IMAGE_INVALID_RESPONSE", "Image provider returned no image"
        )
    output = payload.get("output")
    if isinstance(output, dict):
        choices = output.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                message = choice.get("message") if isinstance(choice, dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "image" or item.get("image"):
                            source = item.get("image") or item.get("url")
                            if isinstance(source, str) and source.strip():
                                return source.strip()
        results = output.get("results")
        if isinstance(results, list):
            for item in results:
                source = item.get("url") if isinstance(item, dict) else None
                if isinstance(source, str) and source.strip():
                    return source.strip()
        source = output.get("image") or output.get("url")
        if isinstance(source, str) and source.strip():
            return source.strip()
    raise AppError(502, "IMAGE_INVALID_RESPONSE", "Image provider returned no image")


def _decode_data_uri(value: str) -> bytes:
    header, _, encoded = value.partition(",")
    if not header.startswith("data:image/") or not encoded:
        raise AppError(502, "IMAGE_INVALID_RESPONSE", "Image data URI is invalid")
    try:
        data = base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError) as exc:
        raise AppError(
            502, "IMAGE_INVALID_RESPONSE", "Image data URI is invalid"
        ) from exc
    if not data:
        raise AppError(502, "IMAGE_INVALID_RESPONSE", "Image data URI is empty")
    return data


async def _download_image(url: str, *, timeout: float, max_bytes: int) -> bytes:
    await _validate_download_url(url)
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
                        "IMAGE_DOWNLOAD_FAILED",
                        "Generated image could not be downloaded "
                        f"(HTTP {response.status_code})",
                    )
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise AppError(
                        502,
                        "IMAGE_TOO_LARGE",
                        "Generated image exceeds the size limit",
                    )
                buffer = bytearray()
                async for chunk in response.aiter_bytes(64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        raise AppError(
                            502,
                            "IMAGE_TOO_LARGE",
                            "Generated image exceeds the size limit",
                        )
    except AppError:
        raise
    except httpx.TimeoutException as exc:
        raise AppError(504, "IMAGE_TIMEOUT", "Image download timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            502, "IMAGE_DOWNLOAD_FAILED", "Generated image could not be downloaded"
        ) from exc
    if not buffer:
        raise AppError(502, "IMAGE_INVALID_RESPONSE", "Generated image is empty")
    return bytes(buffer)


async def _validate_download_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise AppError(
            502, "IMAGE_DOWNLOAD_NOT_ALLOWED", "Generated image URL is invalid"
        )
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise AppError(
            502, "IMAGE_DOWNLOAD_NOT_ALLOWED", "Generated image URL is invalid"
        ) from exc
    try:
        addresses = await run_in_threadpool(
            socket.getaddrinfo, parsed.hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise AppError(
            502, "IMAGE_DOWNLOAD_NOT_ALLOWED", "Generated image host is unavailable"
        ) from exc
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address[4][0])
        except ValueError as exc:
            raise AppError(
                502, "IMAGE_DOWNLOAD_NOT_ALLOWED", "Generated image URL is not allowed"
            ) from exc
        if not resolved.is_global:
            raise AppError(
                502,
                "IMAGE_DOWNLOAD_NOT_ALLOWED",
                "Generated image URL resolves to a private or reserved address",
            )


def _illustration_prompt(scene: CourseScene, payload: Dict[str, Any]) -> str:
    explicit = _payload_text(payload, "prompt")
    if explicit:
        subject = explicit
    else:
        content: Dict[str, Any] = (
            scene.content if isinstance(scene.content, dict) else {}
        )
        raw_canvas = content.get("canvas")
        canvas: Dict[str, Any] = raw_canvas if isinstance(raw_canvas, dict) else {}
        raw_elements = canvas.get("elements")
        elements: List[Any] = raw_elements if isinstance(raw_elements, list) else []
        texts: List[str] = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            if element.get("textType") in {"title", "content"}:
                text = _html_text(str(element.get("content") or ""))
                if text:
                    texts.append(text)
        if not texts:
            texts = [scene.title]
        subject = f"主题：{scene.title}。教学内容：{'；'.join(texts[:7])}"
    instructions = _payload_text(payload, "instructions")
    parts = [subject, IMAGE_STYLE_DIRECTIVE]
    if instructions:
        parts.append(f"额外要求：{instructions}")
    return "\n".join(parts)


def _html_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _content_hash(
    *, provider_id: str, model: str, prompt: str, width: int, height: int
) -> str:
    material = f"{provider_id}\n{model}\n{prompt}\n{width}x{height}\nwebp"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_text(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
    return " ".join(message.split())[:240]

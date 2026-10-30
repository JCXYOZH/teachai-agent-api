import hashlib
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.classroom import CourseScene, CourseStage, GenerationJob
from app.models.course import Course
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.classroom import GenerationJobView, NarrationGenerateRequest
from app.services import generation_jobs
from app.services.media_storage import LocalMediaStorage
from app.services.providers import ProviderConfigService, ResolvedProvider
from app.services.tts import SynthesizedAudio, synthesize_speech

logger = logging.getLogger(__name__)


class NarrationService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.storage = LocalMediaStorage(settings)

    async def create_job(
        self,
        owner: User,
        course_id: UUID,
        payload: NarrationGenerateRequest,
        scene_id: Optional[UUID] = None,
    ) -> GenerationJobView:
        course = await self._get_course(owner.id, course_id)
        stage = await self._latest_stage(owner.id, course.id)
        if stage is None:
            raise AppError(
                404,
                "CLASSROOM_NOT_FOUND",
                "Generate a classroom before adding narration",
            )
        if scene_id is not None:
            scene = await self.db.scalar(
                select(CourseScene).where(
                    CourseScene.id == scene_id,
                    CourseScene.owner_id == owner.id,
                    CourseScene.stage_id == stage.id,
                )
            )
            if scene is None:
                raise AppError(404, "SCENE_NOT_FOUND", "Scene not found")
        resolved = await ProviderConfigService(
            self.db, self.settings
        ).resolve_tts_provider(
            owner,
            payload.provider_id,
            payload.model,
            payload.voice,
        )
        job = await generation_jobs.create_job(
            self.db,
            owner_id=owner.id,
            course_id=course.id,
            kind=generation_jobs.JOB_KIND_NARRATION,
            stage_id=stage.id,
            provider_id=resolved.spec.id,
            model_name=resolved.tts_model,
            payload={
                "stage_id": str(stage.id),
                "scene_ids": [str(scene_id)] if scene_id else None,
                "model": resolved.tts_model,
                "voice": resolved.tts_voice,
            },
            message="Queued narration generation",
        )
        return generation_jobs.job_view(job)

    async def run_job(self, job_id: UUID) -> None:
        job = await self.db.get(GenerationJob, job_id)
        if job is None or job.kind != generation_jobs.JOB_KIND_NARRATION:
            logger.warning("narration_job_missing job_id=%s", job_id)
            return
        await generation_jobs.mark_running(
            self.db, job_id, progress=5, message="Preparing narration"
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
                    422,
                    "NARRATION_NO_SCENES",
                    "No scenes are available for narration",
                )
            resolved = await ProviderConfigService(
                self.db, self.settings
            ).resolve_tts_provider(
                owner,
                job.provider_id,
                _payload_text(job.payload, "model"),
                _payload_text(job.payload, "voice"),
            )
            total = len(scenes)
            generated = 0
            reused = 0
            for index, scene in enumerate(scenes, start=1):
                await generation_jobs.mark_progress(
                    self.db,
                    job_id,
                    progress=5 + int(85 * (index - 1) / total),
                    message=f"Synthesizing scene {index}/{total}",
                )
                outcome = await self._narrate_scene(
                    owner=owner,
                    course=course,
                    stage=stage,
                    scene=scene,
                    resolved=resolved,
                )
                if outcome == "generated":
                    generated += 1
                elif outcome == "reused":
                    reused += 1
            produced = generated + reused
            await generation_jobs.complete_job(
                self.db,
                job_id,
                message=f"Narration ready for {produced} scene(s)",
                stage_id=stage.id,
                scene_count=produced,
            )
            logger.info(
                "narration_job_completed job_id=%s generated=%s reused=%s",
                job_id,
                generated,
                reused,
            )
        except AppError as exc:
            await self.db.rollback()
            await generation_jobs.fail_job(self.db, job_id, exc.code, exc.message)
            logger.warning(
                "narration_generation_failed job_id=%s code=%s", job_id, exc.code
            )
        except Exception:
            await self.db.rollback()
            logger.exception(
                "narration_generation_failed job_id=%s course_id=%s",
                job_id,
                job.course_id,
            )
            await generation_jobs.fail_job(
                self.db,
                job_id,
                "NARRATION_GENERATION_FAILED",
                "Narration generation failed",
            )

    async def _narrate_scene(
        self,
        *,
        owner: User,
        course: Course,
        stage: CourseStage,
        scene: CourseScene,
        resolved: ResolvedProvider,
    ) -> str:
        action = _speech_action(scene.actions)
        if action is None:
            return "skipped"
        action_id = str(action.get("id") or f"{scene.id}-speech")
        text = str(action.get("text") or "").strip()
        if not text:
            return "skipped"
        text = text[: self.settings.tts_max_text_chars]
        speed = _normalize_speed(action.get("speed"))
        content_hash = _content_hash(
            provider_id=resolved.spec.id,
            model=resolved.tts_model or "",
            voice=resolved.tts_voice or "",
            speed=speed,
            text=text,
        )
        asset = await self.db.scalar(
            select(MediaAsset).where(
                MediaAsset.owner_id == owner.id,
                MediaAsset.stage_id == stage.id,
                MediaAsset.scene_id == scene.id,
                MediaAsset.action_id == action_id,
                MediaAsset.kind == "audio",
            )
        )
        if (
            asset is not None
            and asset.content_hash == content_hash
            and self.storage.exists(owner.id, asset.id, asset.storage_key)
        ):
            await self._attach_audio(scene, action_id, asset)
            return "reused"

        audio = await self._obtain_audio(
            owner=owner,
            resolved=resolved,
            text=text,
            speed=speed,
            content_hash=content_hash,
            current_id=asset.id if asset is not None else None,
        )
        media_id = asset.id if asset is not None else uuid4()
        saved = await self.storage.save_bytes(
            owner.id, media_id, "audio", audio.format, audio.data
        )
        if asset is None:
            asset = MediaAsset(
                id=media_id,
                owner_id=owner.id,
                course_id=course.id,
                stage_id=stage.id,
                scene_id=scene.id,
                action_id=action_id,
                kind="audio",
            )
            self.db.add(asset)
        asset.provider_id = resolved.spec.id
        asset.model_name = resolved.tts_model
        asset.voice = resolved.tts_voice
        asset.format = audio.format
        asset.mime_type = audio.mime_type
        asset.size_bytes = saved.size_bytes
        asset.duration_ms = audio.duration_ms
        asset.sha256 = saved.sha256
        asset.content_hash = content_hash
        asset.storage_key = saved.storage_key
        asset.media_metadata = {
            "text_chars": len(text),
            "speed": speed,
        }
        await self._attach_audio(scene, action_id, asset)
        await self.db.commit()
        return "generated"

    async def _obtain_audio(
        self,
        *,
        owner: User,
        resolved: ResolvedProvider,
        text: str,
        speed: float,
        content_hash: str,
        current_id: Optional[UUID],
    ) -> SynthesizedAudio:
        cached = await self.db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.owner_id == owner.id,
                MediaAsset.kind == "audio",
                MediaAsset.content_hash == content_hash,
            )
            .order_by(MediaAsset.created_at.desc())
            .limit(1)
        )
        if cached is not None and cached.id != current_id:
            try:
                path = self.storage.resolve(owner.id, cached.id, cached.storage_key)
                data = path.read_bytes()
            except AppError:
                data = b""
            except OSError:
                data = b""
            if data:
                logger.info(
                    "narration_cache_hit owner_id=%s media_id=%s", owner.id, cached.id
                )
                return SynthesizedAudio(
                    data=data,
                    format=cached.format,
                    mime_type=cached.mime_type,
                    duration_ms=cached.duration_ms,
                )
        return await synthesize_speech(
            resolved,
            text,
            speed=speed,
            timeout=self.settings.tts_generation_timeout_seconds,
            max_bytes=self.settings.tts_max_audio_bytes,
        )

    async def _attach_audio(
        self,
        scene: CourseScene,
        action_id: str,
        asset: MediaAsset,
    ) -> None:
        actions: List[Dict[str, Any]] = []
        for item in scene.actions:
            if (
                isinstance(item, dict)
                and item.get("type") == "speech"
                and str(item.get("id") or f"{scene.id}-speech") == action_id
            ):
                updated = dict(item)
                updated["audioId"] = str(asset.id)
                updated["audioFormat"] = asset.format
                if asset.duration_ms:
                    updated["audioDurationMs"] = asset.duration_ms
                actions.append(updated)
            else:
                actions.append(item)
        scene.actions = actions
        await self.db.flush()

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

    async def _latest_stage(self, owner_id: UUID, course_id: UUID) -> Optional[CourseStage]:
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
        scenes = list(
            (
                await self.db.scalars(
                    select(CourseScene)
                    .where(
                        CourseScene.owner_id == owner_id,
                        CourseScene.stage_id == stage_id,
                    )
                    .order_by(CourseScene.order_index)
                )
            ).all()
        )
        wanted = payload.get("scene_ids")
        if isinstance(wanted, list):
            selected = {str(item) for item in wanted}
            scenes = [scene for scene in scenes if str(scene.id) in selected]
        return scenes


def _speech_action(actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in actions:
        if isinstance(item, dict) and item.get("type") == "speech":
            return item
    return None


def _normalize_speed(value: Any) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.5, min(2.0, speed))


def _content_hash(
    *, provider_id: str, model: str, voice: str, speed: float, text: str
) -> str:
    material = f"{provider_id}\n{model}\n{voice}\n{speed:.2f}\n{text}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_text(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None

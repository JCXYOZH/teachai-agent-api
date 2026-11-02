import logging
import re
from typing import Any, Dict, List, Optional, Set
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utcnow
from app.models.classroom import CourseScene, CourseStage, GenerationJob
from app.models.course import Course
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.classroom import (
    ClassroomExportRequest,
    ClassroomExportsView,
    ClassroomExportView,
    GenerationJobView,
)
from app.services import generation_jobs
from app.services.exporters import (
    SUPPORTED_FORMATS,
    ExportAudio,
    ExportDocument,
    ExportImage,
    ExportScene,
    RenderedExport,
    render_export,
)
from app.services.media_storage import LocalMediaStorage

logger = logging.getLogger(__name__)

EXPORT_ACTION_PREFIX = "export:"
AUDIO_BUDGET_RATIO = 0.6
SLUG_PATTERN = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


class ClassroomExportService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.storage = LocalMediaStorage(settings)

    async def create_job(
        self, owner: User, course_id: UUID, payload: ClassroomExportRequest
    ) -> GenerationJobView:
        course = await self._get_course(owner.id, course_id)
        stage = await self._latest_stage(owner.id, course.id)
        if stage is None:
            raise AppError(
                404,
                "CLASSROOM_NOT_FOUND",
                "Generate a classroom before exporting it",
            )
        job = await generation_jobs.create_job(
            self.db,
            owner_id=owner.id,
            course_id=course.id,
            kind=generation_jobs.JOB_KIND_EXPORT,
            stage_id=stage.id,
            payload={
                "stage_id": str(stage.id),
                "format": payload.format,
                "include_images": payload.include_images,
                "include_audio": payload.include_audio,
            },
            message=f"Queued {payload.format.upper()} export",
        )
        return generation_jobs.job_view(job)

    async def run_job(self, job_id: UUID) -> None:
        job = await self.db.get(GenerationJob, job_id)
        if job is None or job.kind != generation_jobs.JOB_KIND_EXPORT:
            logger.warning("export_job_missing job_id=%s", job_id)
            return
        await generation_jobs.mark_running(
            self.db, job_id, progress=10, message="Preparing export"
        )
        try:
            owner = await self.db.get(User, job.owner_id)
            if owner is None:
                raise AppError(404, "OWNER_NOT_FOUND", "Course owner no longer exists")
            course = await self._get_course(owner.id, job.course_id)
            stage = await self._stage(owner.id, course.id, job.stage_id)
            scenes = await self._scenes(owner.id, stage.id)
            if not scenes:
                raise AppError(
                    422,
                    "EXPORT_EMPTY_CLASSROOM",
                    "This classroom has no scenes to export",
                )

            export_format = str(job.payload.get("format") or "pptx").lower()
            if export_format not in SUPPORTED_FORMATS:
                raise AppError(
                    422,
                    "EXPORT_FORMAT_UNSUPPORTED",
                    f"Unsupported export format: {export_format}",
                )
            include_images = bool(job.payload.get("include_images", True))
            include_audio = bool(job.payload.get("include_audio", True))

            await generation_jobs.mark_progress(
                self.db, job_id, progress=30, message="Collecting media"
            )
            warnings: List[str] = []
            export_scenes = await self._export_scenes(
                owner_id=owner.id,
                scenes=scenes,
                include_images=include_images,
                include_audio=include_audio,
                warnings=warnings,
            )
            document = ExportDocument(
                title=stage.name or course.title,
                description=stage.description,
                stage_name=stage.name,
                version=stage.version,
                language_directive=stage.language_directive,
                dsl_version=stage.dsl_version,
                generated_at=utcnow(),
                scenes=export_scenes,
                include_audio=include_audio,
            )

            await generation_jobs.mark_progress(
                self.db,
                job_id,
                progress=60,
                message=f"Rendering {export_format.upper()}",
            )
            rendered = await run_in_threadpool(
                render_export, document, export_format, self.settings
            )
            if len(rendered.data) > self.settings.export_max_bytes:
                raise AppError(
                    413,
                    "EXPORT_TOO_LARGE",
                    "The export exceeds the configured size limit",
                )

            await generation_jobs.mark_progress(
                self.db, job_id, progress=85, message="Saving export"
            )
            await self._save_export(
                owner_id=owner.id,
                course_id=course.id,
                stage=stage,
                export_format=export_format,
                rendered=rendered,
                scene_count=len(scenes),
                include_images=include_images,
                include_audio=include_audio,
                warnings=warnings + list(rendered.warnings),
            )
            await generation_jobs.complete_job(
                self.db,
                job_id,
                message=f"{export_format.upper()} export ready",
                stage_id=stage.id,
                scene_count=len(scenes),
            )
            logger.info(
                "export_job_completed job_id=%s format=%s scenes=%s bytes=%s",
                job_id,
                export_format,
                len(scenes),
                len(rendered.data),
            )
        except AppError as exc:
            await self.db.rollback()
            await generation_jobs.fail_job(self.db, job_id, exc.code, exc.message)
            logger.warning("export_generation_failed job_id=%s code=%s", job_id, exc.code)
        except Exception:
            await self.db.rollback()
            logger.exception(
                "export_generation_failed job_id=%s course_id=%s",
                job_id,
                job.course_id,
            )
            await generation_jobs.fail_job(
                self.db,
                job_id,
                "EXPORT_GENERATION_FAILED",
                "Classroom export failed",
            )

    async def list_exports(self, owner: User, course_id: UUID) -> ClassroomExportsView:
        await self._get_course(owner.id, course_id)
        stage = await self._latest_stage(owner.id, course_id)
        if stage is None:
            raise AppError(404, "CLASSROOM_NOT_FOUND", "Classroom not found")
        assets = (
            await self.db.scalars(
                select(MediaAsset)
                .where(
                    MediaAsset.owner_id == owner.id,
                    MediaAsset.stage_id == stage.id,
                    MediaAsset.kind == "export",
                )
                .order_by(MediaAsset.updated_at.desc())
            )
        ).all()
        exports: List[ClassroomExportView] = []
        for asset in assets:
            view = self._export_view(asset)
            if view is not None:
                exports.append(view)
        return ClassroomExportsView(stage_id=stage.id, exports=exports)

    async def _export_scenes(
        self,
        *,
        owner_id: UUID,
        scenes: List[CourseScene],
        include_images: bool,
        include_audio: bool,
        warnings: List[str],
    ) -> List[ExportScene]:
        media_ids: Set[UUID] = set()
        for scene in scenes:
            image_id = _illustration_media_id(scene)
            if image_id is not None and include_images:
                media_ids.add(image_id)
            audio_id = _audio_media_id(scene)
            if audio_id is not None and include_audio:
                media_ids.add(audio_id)

        assets: Dict[UUID, MediaAsset] = {}
        if media_ids:
            rows = (
                await self.db.scalars(
                    select(MediaAsset).where(
                        MediaAsset.owner_id == owner_id,
                        MediaAsset.id.in_(media_ids),
                    )
                )
            ).all()
            assets = {asset.id: asset for asset in rows}

        audio_budget = int(self.settings.export_max_bytes * AUDIO_BUDGET_RATIO)
        audio_bytes = 0
        audio_skipped = False
        export_scenes: List[ExportScene] = []
        for scene in scenes:
            image: Optional[ExportImage] = None
            if include_images:
                image_id = _illustration_media_id(scene)
                image = await self._load_image(owner_id, assets, image_id, scene, warnings)
            audio: Optional[ExportAudio] = None
            if include_audio:
                audio_id = _audio_media_id(scene)
                audio = await self._load_audio(owner_id, assets, audio_id, scene, warnings)
                if audio is not None:
                    if audio_bytes + len(audio.data) > audio_budget:
                        audio = None
                        if not audio_skipped:
                            audio_skipped = True
                            warnings.append(
                                "Narration audio was skipped to keep the export "
                                "under the size limit"
                            )
                    else:
                        audio_bytes += len(audio.data)
            export_scenes.append(
                ExportScene(
                    id=str(scene.id),
                    order=scene.order_index,
                    scene_type=scene.scene_type,
                    title=scene.title,
                    content=scene.content if isinstance(scene.content, dict) else {},
                    narration=_narration_text(scene),
                    actions=[
                        action
                        for action in (scene.actions or [])
                        if isinstance(action, dict)
                    ],
                    image=image,
                    audio=audio,
                )
            )
        return export_scenes

    async def _load_image(
        self,
        owner_id: UUID,
        assets: Dict[UUID, MediaAsset],
        media_id: Optional[UUID],
        scene: CourseScene,
        warnings: List[str],
    ) -> Optional[ExportImage]:
        if media_id is None:
            return None
        asset = assets.get(media_id)
        if asset is None:
            warnings.append(f"Illustration for scene {scene.order_index} was not found")
            return None
        try:
            data = await run_in_threadpool(
                self.storage.resolve(owner_id, asset.id, asset.storage_key).read_bytes
            )
        except (AppError, OSError):
            warnings.append(f"Illustration for scene {scene.order_index} is unavailable")
            return None
        metadata = asset.media_metadata if isinstance(asset.media_metadata, dict) else {}
        return ExportImage(
            data=data,
            extension=asset.format or "webp",
            mime_type=asset.mime_type or "image/webp",
            width=int(metadata.get("width") or 0),
            height=int(metadata.get("height") or 0),
        )

    async def _load_audio(
        self,
        owner_id: UUID,
        assets: Dict[UUID, MediaAsset],
        media_id: Optional[UUID],
        scene: CourseScene,
        warnings: List[str],
    ) -> Optional[ExportAudio]:
        if media_id is None:
            return None
        asset = assets.get(media_id)
        if asset is None:
            warnings.append(f"Narration for scene {scene.order_index} was not found")
            return None
        try:
            data = await run_in_threadpool(
                self.storage.resolve(owner_id, asset.id, asset.storage_key).read_bytes
            )
        except (AppError, OSError):
            warnings.append(f"Narration for scene {scene.order_index} is unavailable")
            return None
        return ExportAudio(
            data=data,
            extension=asset.format or "mp3",
            mime_type=asset.mime_type or "audio/mpeg",
            duration_ms=asset.duration_ms,
        )

    async def _save_export(
        self,
        *,
        owner_id: UUID,
        course_id: UUID,
        stage: CourseStage,
        export_format: str,
        rendered: RenderedExport,
        scene_count: int,
        include_images: bool,
        include_audio: bool,
        warnings: List[str],
    ) -> MediaAsset:
        action_id = f"{EXPORT_ACTION_PREFIX}{export_format}"
        asset = await self.db.scalar(
            select(MediaAsset).where(
                MediaAsset.owner_id == owner_id,
                MediaAsset.stage_id == stage.id,
                MediaAsset.scene_id.is_(None),
                MediaAsset.kind == "export",
                MediaAsset.action_id == action_id,
            )
        )
        media_id = asset.id if asset is not None else uuid4()
        saved = await self.storage.save_bytes(
            owner_id, media_id, export_format, rendered.extension, rendered.data
        )
        filename = _export_filename(stage, rendered.extension)
        if asset is None:
            asset = MediaAsset(
                id=media_id,
                owner_id=owner_id,
                course_id=course_id,
                stage_id=stage.id,
                scene_id=None,
                action_id=action_id,
                kind="export",
            )
            self.db.add(asset)
        asset.format = rendered.extension
        asset.mime_type = rendered.mime_type
        asset.size_bytes = saved.size_bytes
        asset.sha256 = saved.sha256
        asset.content_hash = saved.sha256
        asset.storage_key = saved.storage_key
        asset.media_metadata = {
            "format": export_format,
            "filename": filename,
            "stage_version": stage.version,
            "scene_count": scene_count,
            "include_images": include_images,
            "include_audio": include_audio,
            "warnings": list(warnings),
        }
        await self.db.commit()
        await self.db.refresh(asset)
        return asset

    def _export_view(self, asset: MediaAsset) -> Optional[ClassroomExportView]:
        metadata = asset.media_metadata if isinstance(asset.media_metadata, dict) else {}
        export_format = str(
            metadata.get("format") or (asset.action_id or "").replace(EXPORT_ACTION_PREFIX, "")
        ).lower()
        if export_format not in SUPPORTED_FORMATS:
            return None
        warnings = metadata.get("warnings")
        return ClassroomExportView(
            format=export_format,  # type: ignore[arg-type]
            media_id=asset.id,
            filename=str(metadata.get("filename") or f"classroom.{asset.format}"),
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
            scene_count=int(metadata.get("scene_count") or 0),
            include_images=bool(metadata.get("include_images", True)),
            include_audio=bool(metadata.get("include_audio", True)),
            warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
            content_url=f"{self.settings.api_v1_prefix}/media/{asset.id}/content",
            created_at=asset.created_at,
            updated_at=asset.updated_at,
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

    async def _stage(
        self, owner_id: UUID, course_id: UUID, stage_id: Optional[UUID]
    ) -> CourseStage:
        if stage_id is None:
            raise AppError(404, "CLASSROOM_NOT_FOUND", "Classroom not found")
        stage = await self.db.scalar(
            select(CourseStage).where(
                CourseStage.id == stage_id,
                CourseStage.owner_id == owner_id,
                CourseStage.course_id == course_id,
            )
        )
        if stage is None:
            raise AppError(404, "CLASSROOM_NOT_FOUND", "Classroom not found")
        return stage

    async def _scenes(self, owner_id: UUID, stage_id: UUID) -> List[CourseScene]:
        return list(
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


def _illustration_media_id(scene: CourseScene) -> Optional[UUID]:
    content = scene.content if isinstance(scene.content, dict) else {}
    illustration = content.get("illustration")
    if not isinstance(illustration, dict):
        return None
    return _as_uuid(illustration.get("mediaId"))


def _audio_media_id(scene: CourseScene) -> Optional[UUID]:
    for action in scene.actions or []:
        if not isinstance(action, dict) or action.get("type") != "speech":
            continue
        media_id = _as_uuid(action.get("audioId"))
        if media_id is not None:
            return media_id
    return None


def _narration_text(scene: CourseScene) -> str:
    for action in scene.actions or []:
        if not isinstance(action, dict) or action.get("type") != "speech":
            continue
        text = action.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _as_uuid(value: Any) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _export_filename(stage: CourseStage, extension: str) -> str:
    raw = stage.name or "classroom"
    slug = SLUG_PATTERN.sub("-", raw).strip("-")
    slug = slug[:60] or "classroom"
    return f"{slug}-v{stage.version}.{extension}"

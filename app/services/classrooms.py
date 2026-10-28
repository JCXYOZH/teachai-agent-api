import asyncio
import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utcnow
from app.models.classroom import CourseScene, CourseStage, GenerationJob
from app.models.course import Course
from app.models.outline import CourseOutline
from app.models.user import User
from app.schemas.classroom import (
    ClassroomGenerateRequest,
    ClassroomView,
    GenerationJobView,
    SceneView,
)
from app.services.classroom_dsl import DSL_VERSION, compile_scenes
from app.services.classroom_prompt import (
    SYSTEM_PROMPT,
    build_classroom_user_prompt,
)
from app.services.classroom_schema import (
    ClassroomParseError,
    ClassroomPlan,
    normalize_classroom_plan,
)
from app.services.llm import (
    LLMProviderError,
    LLMTimeoutError,
    call_anthropic,
    call_openai_compatible,
)
from app.services.materials import MaterialBundle, load_course_materials
from app.services.providers import ProviderConfigService, ResolvedProvider

logger = logging.getLogger(__name__)


class ClassroomService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def generate(
        self,
        owner: User,
        course_id: UUID,
        payload: ClassroomGenerateRequest,
    ) -> GenerationJobView:
        course = await self._get_course(owner.id, course_id)
        outline = await self._get_latest_outline(owner.id, course.id)
        materials = await load_course_materials(
            self.db,
            owner.id,
            course.id,
            self.settings.classroom_material_char_limit,
        )
        resolved = await ProviderConfigService(self.db, self.settings).resolve_for_generation(
            owner, payload.provider_id
        )
        if payload.model:
            resolved.default_model = payload.model

        job = GenerationJob(
            owner_id=owner.id,
            course_id=course.id,
            outline_id=outline.id,
            provider_id=resolved.spec.id,
            model_name=resolved.default_model,
            status="queued",
            progress=0,
            message="Queued",
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)

        job.status = "running"
        job.progress = 10
        job.message = "Generating classroom"
        job.started_at = utcnow()
        await self.db.commit()

        try:
            plan = await self._generate_plan(
                resolved=resolved,
                course=course,
                outline=outline,
                instructions=payload.instructions,
                materials=materials,
            )
            stage = await self._create_stage(
                owner=owner,
                course=course,
                outline=outline,
                job=job,
                plan=plan,
            )
        except AppError as exc:
            await self._fail_job(job.id, exc.code, exc.message)
            raise
        except Exception:
            logger.exception(
                "classroom_generation_failed job_id=%s course_id=%s",
                job.id,
                course.id,
            )
            await self._fail_job(
                job.id,
                "CLASSROOM_GENERATION_FAILED",
                "Classroom generation failed",
            )
            raise

        await self.db.refresh(stage)
        await self.db.refresh(job)
        return self._job_view(job)

    async def get_latest(self, owner: User, course_id: UUID) -> ClassroomView:
        await self._get_course(owner.id, course_id)
        stage = await self.db.scalar(
            select(CourseStage)
            .where(
                CourseStage.owner_id == owner.id,
                CourseStage.course_id == course_id,
            )
            .order_by(CourseStage.version.desc())
            .limit(1)
        )
        if stage is None:
            raise AppError(404, "CLASSROOM_NOT_FOUND", "Classroom not found")
        scenes = (
            await self.db.scalars(
                select(CourseScene)
                .where(
                    CourseScene.owner_id == owner.id,
                    CourseScene.stage_id == stage.id,
                )
                .order_by(CourseScene.order_index)
            )
        ).all()
        return self._classroom_view(stage, list(scenes))

    async def get_job(self, owner: User, job_id: UUID) -> GenerationJobView:
        job = await self.db.scalar(
            select(GenerationJob).where(
                GenerationJob.id == job_id,
                GenerationJob.owner_id == owner.id,
            )
        )
        if job is None:
            raise AppError(
                404,
                "GENERATION_JOB_NOT_FOUND",
                "Generation job not found",
            )
        return self._job_view(job)

    async def _generate_plan(
        self,
        *,
        resolved: ResolvedProvider,
        course: Course,
        outline: CourseOutline,
        instructions: Optional[str],
        materials: MaterialBundle,
    ) -> ClassroomPlan:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.classroom_generation_timeout_seconds
        last_error = ""
        for attempt in range(2):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AppError(
                    504,
                    "CLASSROOM_GENERATION_TIMEOUT",
                    "Classroom generation timed out",
                )
            user_prompt = build_classroom_user_prompt(
                course_title=outline.course_title,
                description=course.description,
                language_directive=outline.language_directive,
                outline={
                    "courseTitle": outline.course_title,
                    "scenes": outline.scenes,
                },
                instructions=instructions,
                material_excerpt=materials.text,
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
                return normalize_classroom_plan(raw_text, outline.course_title)
            except ClassroomParseError as exc:
                last_error = str(exc)[:300]
                logger.warning(
                    "classroom_parse_retry provider=%s attempt=%s error=%s",
                    resolved.spec.id,
                    attempt + 1,
                    type(exc).__name__,
                )
            except asyncio.TimeoutError as exc:
                raise AppError(
                    504,
                    "CLASSROOM_GENERATION_TIMEOUT",
                    "Classroom generation timed out",
                ) from exc
        raise AppError(
            502,
            "CLASSROOM_INVALID_RESPONSE",
            "Model did not return a valid classroom",
            details={"reason": last_error or "Unknown parsing error"},
        )

    async def _call_provider(
        self,
        resolved: ResolvedProvider,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        timeout = self.settings.classroom_generation_timeout_seconds
        text_only = resolved.spec.id == "qwen" and "omni" in resolved.default_model.lower()
        try:
            if resolved.spec.protocol == "anthropic":
                return await call_anthropic(
                    base_url=resolved.base_url,
                    api_key=resolved.api_key,
                    model=resolved.default_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout=timeout,
                )
            return await call_openai_compatible(
                base_url=resolved.base_url,
                api_key=resolved.api_key,
                model=resolved.default_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout=timeout,
                text_only=text_only,
            )
        except LLMTimeoutError as exc:
            raise AppError(
                504,
                "CLASSROOM_GENERATION_TIMEOUT",
                "Classroom generation timed out",
            ) from exc
        except LLMProviderError as exc:
            raise AppError(
                502,
                "CLASSROOM_PROVIDER_ERROR",
                "Model provider failed to generate the classroom",
            ) from exc

    async def _create_stage(
        self,
        *,
        owner: User,
        course: Course,
        outline: CourseOutline,
        job: GenerationJob,
        plan: ClassroomPlan,
    ) -> CourseStage:
        locked = await self.db.scalar(
            select(Course)
            .where(
                Course.id == course.id,
                Course.owner_id == owner.id,
                Course.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if locked is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
        latest_version = await self.db.scalar(
            select(func.max(CourseStage.version)).where(CourseStage.course_id == course.id)
        )
        stage = CourseStage(
            owner_id=owner.id,
            course_id=course.id,
            outline_id=outline.id,
            version=int(latest_version or 0) + 1,
            name=plan.name,
            description=plan.description,
            style=plan.style,
            language_directive=outline.language_directive,
            dsl_version=DSL_VERSION,
            provider_id=job.provider_id,
            model_name=job.model_name,
            scene_count=len(plan.scenes),
        )
        self.db.add(stage)
        await self.db.flush()

        compiled = compile_scenes(plan, stage.id, outline.language_directive)
        for item in compiled:
            self.db.add(
                CourseScene(
                    owner_id=owner.id,
                    stage_id=stage.id,
                    scene_type=item["type"],
                    title=item["title"],
                    order_index=item["order"],
                    content=item["content"],
                    actions=item["actions"],
                )
            )

        job.stage_id = stage.id
        job.scene_count = len(compiled)
        job.status = "completed"
        job.progress = 100
        job.message = "Classroom ready"
        job.completed_at = utcnow()
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppError(
                409,
                "CLASSROOM_VERSION_CONFLICT",
                "Classroom version could not be saved; retry the request",
            ) from exc
        return stage

    async def _fail_job(self, job_id: UUID, error_code: str, error_message: str) -> None:
        job = await self.db.get(GenerationJob, job_id)
        if job is None:
            return
        job.status = "failed"
        job.progress = 100
        job.message = "Generation failed"
        job.error_code = error_code
        job.error_message = error_message[:2000]
        job.completed_at = utcnow()
        await self.db.commit()

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

    async def _get_latest_outline(self, owner_id: UUID, course_id: UUID) -> CourseOutline:
        outline = await self.db.scalar(
            select(CourseOutline)
            .where(
                CourseOutline.owner_id == owner_id,
                CourseOutline.course_id == course_id,
            )
            .order_by(CourseOutline.version.desc())
            .limit(1)
        )
        if outline is None:
            raise AppError(404, "OUTLINE_NOT_FOUND", "Course outline not found")
        return outline

    def _job_view(self, job: GenerationJob) -> GenerationJobView:
        return GenerationJobView(
            id=job.id,
            course_id=job.course_id,
            outline_id=job.outline_id,
            stage_id=job.stage_id,
            provider_id=job.provider_id,
            model_name=job.model_name,
            status=job.status,  # type: ignore[arg-type]
            progress=job.progress,
            message=job.message,
            error_code=job.error_code,
            error_message=job.error_message,
            scene_count=job.scene_count,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    def _classroom_view(self, stage: CourseStage, scenes: List[CourseScene]) -> ClassroomView:
        return ClassroomView(
            id=stage.id,
            course_id=stage.course_id,
            outline_id=stage.outline_id,
            version=stage.version,
            name=stage.name,
            description=stage.description,
            style=stage.style,
            language_directive=stage.language_directive,
            dsl_version=stage.dsl_version,
            provider_id=stage.provider_id,
            model_name=stage.model_name,
            scene_count=stage.scene_count,
            scenes=[
                SceneView(
                    id=scene.id,
                    stage_id=scene.stage_id,
                    type=scene.scene_type,  # type: ignore[arg-type]
                    title=scene.title,
                    order=scene.order_index,
                    content=scene.content,
                    actions=scene.actions,
                    created_at=scene.created_at,
                    updated_at=scene.updated_at,
                )
                for scene in scenes
            ],
            created_at=stage.created_at,
            updated_at=stage.updated_at,
        )

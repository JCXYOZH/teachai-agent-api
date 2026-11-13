import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utcnow
from app.models.chat import CourseChatMessage, CourseChatSession
from app.models.classroom import CourseScene, CourseStage
from app.models.course import Course
from app.models.outline import CourseOutline
from app.models.user import User
from app.schemas.chat import (
    ChatMessageRequest,
    ChatMessageView,
    ChatReplyView,
    ChatThreadView,
)
from app.services.chat_prompt import (
    SYSTEM_PROMPT,
    build_teacher_user_prompt,
    outline_text,
    scene_text,
)
from app.services.llm import (
    LLMEmptyResponseError,
    LLMProviderError,
    LLMTimeoutError,
    call_anthropic,
    call_openai_compatible,
    stream_anthropic,
    stream_openai_compatible,
)
from app.services.materials import load_course_materials
from app.services.providers import ProviderConfigService, ResolvedProvider

logger = logging.getLogger(__name__)

LOCAL_DAY_OFFSET = timedelta(hours=8)


@dataclass
class _ChatContext:
    course: Course
    stage: CourseStage
    scene: Optional[CourseScene]
    session: CourseChatSession
    user_prompt: str
    used: int
    limit: int
    candidates: List[ResolvedProvider]


@dataclass
class _StreamDelta:
    text: str
    provider: ResolvedProvider


class ChatService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def get_thread(self, owner: User, course_id: UUID) -> ChatThreadView:
        course = await self._get_course(owner.id, course_id)
        await self._require_stage(owner.id, course.id)
        session = await self._get_session(owner.id, course.id)
        messages: List[CourseChatMessage] = []
        if session is not None:
            messages = await self._list_messages(owner.id, session)
        used = await self._daily_used(owner.id)
        return ChatThreadView(
            course_id=course.id,
            session_id=session.id if session else None,
            messages=[self._message_view(message) for message in messages],
            daily_limit=self.settings.chat_daily_message_limit,
            daily_used=used,
            remaining_today=max(self.settings.chat_daily_message_limit - used, 0),
        )

    async def send_message(
        self,
        owner: User,
        course_id: UUID,
        payload: ChatMessageRequest,
    ) -> ChatReplyView:
        context = await self._prepare_message(owner, course_id, payload)
        answer, resolved = await self._answer(context.candidates, context.user_prompt)
        return await self._store_reply(
            owner, context, payload.message, answer, resolved
        )

    async def _prepare_message(
        self,
        owner: User,
        course_id: UUID,
        payload: ChatMessageRequest,
    ) -> _ChatContext:
        course = await self._get_course(owner.id, course_id)
        stage = await self._require_stage(owner.id, course.id, payload.stage_id)
        scene = await self._resolve_scene(owner.id, stage, payload.scene_id)
        used = await self._daily_used(owner.id)
        limit = self.settings.chat_daily_message_limit
        if used >= limit:
            raise AppError(
                429,
                "CHAT_DAILY_LIMIT_REACHED",
                "Daily teacher question limit reached",
                details={"limit": limit, "used": used},
            )

        outline = await self._latest_outline(owner.id, course.id)
        session = await self._get_or_create_session(owner, course)
        history = await self._recent_history(owner.id, session)
        materials = ""
        if payload.include_materials:
            bundle = await load_course_materials(
                self.db, owner.id, course.id, self.settings.chat_material_char_limit
            )
            materials = bundle.text

        outline_body = outline_text(outline.scenes if outline is not None else None)
        stage_label = self._stage_label(stage)
        scene_body = (
            scene_text(scene.scene_type, scene.content, scene.actions)
            if scene is not None
            else ""
        )
        user_prompt = build_teacher_user_prompt(
            course_title=outline.course_title if outline is not None else course.title,
            language_directive=outline.language_directive if outline is not None else "",
            stage_label=stage_label,
            outline=outline_body,
            scene_title=scene.title if scene is not None else None,
            scene_body=scene_body,
            material_excerpt=materials,
            history=history,
            question=payload.message,
        )
        candidates = await ProviderConfigService(
            self.db, self.settings
        ).resolve_generation_candidates(owner)
        return _ChatContext(
            course=course,
            stage=stage,
            scene=scene,
            session=session,
            user_prompt=user_prompt,
            used=used,
            limit=limit,
            candidates=candidates,
        )

    async def _store_reply(
        self,
        owner: User,
        context: _ChatContext,
        question: str,
        answer: str,
        resolved: ResolvedProvider,
    ) -> ChatReplyView:
        now = utcnow()
        question_message = CourseChatMessage(
            owner_id=owner.id,
            created_at=now,
            session_id=context.session.id,
            role="user",
            content=question,
            stage_id=context.stage.id,
            stage_title=context.stage.name,
            stage_index=context.stage.version,
            scene_id=context.scene.id if context.scene is not None else None,
            scene_title=(
                context.scene.title if context.scene is not None else None
            ),
        )
        reply_message = CourseChatMessage(
            owner_id=owner.id,
            created_at=now + timedelta(microseconds=1000),
            session_id=context.session.id,
            role="assistant",
            content=answer,
            stage_id=context.stage.id,
            stage_title=context.stage.name,
            stage_index=context.stage.version,
            scene_id=context.scene.id if context.scene is not None else None,
            scene_title=(
                context.scene.title if context.scene is not None else None
            ),
            provider_id=resolved.spec.id,
            model_name=resolved.default_model,
        )
        self.db.add(question_message)
        self.db.add(reply_message)
        context.session.updated_at = utcnow()
        await self.db.commit()
        await self.db.refresh(question_message)
        await self.db.refresh(reply_message)
        used = context.used + 1
        return ChatReplyView(
            course_id=context.course.id,
            session_id=context.session.id,
            question=self._message_view(question_message),
            reply=self._message_view(reply_message),
            daily_limit=context.limit,
            daily_used=used,
            remaining_today=max(context.limit - used, 0),
        )

    async def open_message_stream(
        self,
        owner: User,
        course_id: UUID,
        payload: ChatMessageRequest,
    ) -> AsyncIterator[Tuple[str, Dict[str, Any]]]:
        context = await self._prepare_message(owner, course_id, payload)
        return self._stream_context(owner, context, payload.message)

    async def _stream_context(
        self,
        owner: User,
        context: _ChatContext,
        question: str,
    ) -> AsyncIterator[Tuple[str, Dict[str, Any]]]:
        chunks: List[str] = []
        resolved: Optional[ResolvedProvider] = None
        try:
            async for delta in self._stream_answer(
                context.candidates, context.user_prompt
            ):
                resolved = delta.provider
                chunks.append(delta.text)
                yield ("delta", {"text": delta.text})
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            yield ("error", self._stream_error(exc))
            return
        except Exception:
            logger.exception("chat_stream_failed")
            yield (
                "error",
                self._stream_error(
                    AppError(
                        500, "INTERNAL_ERROR", "An unexpected error occurred"
                    )
                ),
            )
            return

        answer = "".join(chunks).strip()
        if resolved is None or not answer:
            yield (
                "error",
                self._stream_error(
                    AppError(
                        502,
                        "CHAT_EMPTY_RESPONSE",
                        "Teacher returned an empty answer",
                    )
                ),
            )
            return
        try:
            reply = await self._store_reply(
                owner, context, question, answer, resolved
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("chat_stream_persist_failed")
            yield (
                "error",
                self._stream_error(
                    AppError(
                        500, "INTERNAL_ERROR", "An unexpected error occurred"
                    )
                ),
            )
            return
        yield ("done", reply.model_dump(mode="json"))

    async def _stream_answer(
        self,
        candidates: List[ResolvedProvider],
        user_prompt: str,
    ) -> AsyncIterator[_StreamDelta]:
        for index, resolved in enumerate(candidates):
            emitted = False
            try:
                async for text in self._stream_provider_with_retry(
                    resolved, user_prompt
                ):
                    if not text:
                        continue
                    emitted = True
                    yield _StreamDelta(text=text, provider=resolved)
                return
            except LLMTimeoutError as exc:
                raise AppError(
                    504, "CHAT_TIMEOUT", "Teacher response timed out"
                ) from exc
            except LLMEmptyResponseError as exc:
                logger.warning("chat_empty_response provider=%s", resolved.spec.id)
                raise AppError(
                    502,
                    "CHAT_EMPTY_RESPONSE",
                    "Teacher returned an empty answer",
                ) from exc
            except LLMProviderError as exc:
                has_fallback = index + 1 < len(candidates)
                if emitted or not has_fallback:
                    logger.warning(
                        "chat_stream_provider_failed provider=%s error=%s",
                        resolved.spec.id,
                        type(exc).__name__,
                    )
                    raise AppError(
                        502,
                        "CHAT_PROVIDER_ERROR",
                        "Teacher model request failed",
                    ) from exc
                logger.warning(
                    "chat_stream_provider_fallback from=%s to=%s",
                    resolved.spec.id,
                    candidates[index + 1].spec.id,
                )
        raise AppError(502, "CHAT_PROVIDER_ERROR", "Teacher model request failed")

    async def _stream_provider_with_retry(
        self,
        resolved: ResolvedProvider,
        user_prompt: str,
    ) -> AsyncIterator[str]:
        last_empty: Optional[LLMEmptyResponseError] = None
        for _attempt in range(2):
            produced = False
            try:
                async for text in self._stream_provider(resolved, user_prompt):
                    if not text:
                        continue
                    produced = True
                    yield text
                if produced:
                    return
                last_empty = LLMEmptyResponseError("Model returned an empty answer")
            except LLMEmptyResponseError as exc:
                last_empty = exc
                continue
        assert last_empty is not None
        raise last_empty

    async def _stream_provider(
        self, resolved: ResolvedProvider, user_prompt: str
    ) -> AsyncIterator[str]:
        timeout = self.settings.chat_generation_timeout_seconds
        iterator = self._call_provider_stream(resolved, user_prompt).__aiter__()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise LLMTimeoutError("Model request timed out")
                try:
                    text = await asyncio.wait_for(
                        iterator.__anext__(), timeout=remaining
                    )
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError as exc:
                    raise LLMTimeoutError("Model request timed out") from exc
                if text:
                    yield text
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    def _call_provider_stream(
        self, resolved: ResolvedProvider, user_prompt: str
    ) -> AsyncIterator[str]:
        timeout = self.settings.chat_generation_timeout_seconds
        text_only = (
            resolved.spec.id == "qwen" and "omni" in resolved.default_model.lower()
        )
        if resolved.spec.protocol == "anthropic":
            return stream_anthropic(
                base_url=resolved.base_url,
                api_key=resolved.api_key,
                model=resolved.default_model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                timeout=timeout,
                max_tokens=self.settings.chat_max_tokens,
                temperature=0.3,
                provider_id=resolved.spec.id,
            )
        return stream_openai_compatible(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.default_model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            timeout=timeout,
            max_tokens=self.settings.chat_max_tokens,
            temperature=0.3,
            text_only=text_only,
            disable_thinking=resolved.spec.supports_thinking_control,
            provider_id=resolved.spec.id,
        )

    def _stream_error(self, exc: AppError) -> Dict[str, Any]:
        return {
            "status": exc.status_code,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }

    async def clear(self, owner: User, course_id: UUID) -> None:
        course = await self._get_course(owner.id, course_id)
        session = await self._get_session(owner.id, course.id)
        if session is None:
            return
        session.cleared_at = utcnow()
        session.updated_at = utcnow()
        await self.db.commit()

    async def _answer(
        self,
        candidates: List[ResolvedProvider],
        user_prompt: str,
    ) -> Tuple[str, ResolvedProvider]:
        for index, resolved in enumerate(candidates):
            try:
                return await self._answer_with_provider(resolved, user_prompt), resolved
            except LLMTimeoutError as exc:
                raise AppError(504, "CHAT_TIMEOUT", "Teacher response timed out") from exc
            except LLMEmptyResponseError as exc:
                logger.warning("chat_empty_response provider=%s", resolved.spec.id)
                raise AppError(
                    502,
                    "CHAT_EMPTY_RESPONSE",
                    "Teacher returned an empty answer",
                ) from exc
            except LLMProviderError as exc:
                has_fallback = index + 1 < len(candidates)
                if not has_fallback:
                    logger.warning(
                        "chat_provider_failed provider=%s error=%s",
                        resolved.spec.id,
                        type(exc).__name__,
                    )
                    raise AppError(
                        502,
                        "CHAT_PROVIDER_ERROR",
                        "Teacher model request failed",
                    ) from exc
                logger.warning(
                    "chat_provider_fallback from=%s to=%s",
                    resolved.spec.id,
                    candidates[index + 1].spec.id,
                )
        raise AppError(502, "CHAT_PROVIDER_ERROR", "Teacher model request failed")

    async def _answer_with_provider(
        self, resolved: ResolvedProvider, user_prompt: str
    ) -> str:
        last_empty: Optional[LLMEmptyResponseError] = None
        for _attempt in range(2):
            try:
                text = await asyncio.wait_for(
                    self._call_provider(resolved, user_prompt),
                    timeout=self.settings.chat_generation_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise LLMTimeoutError("Model request timed out") from exc
            except LLMEmptyResponseError as exc:
                last_empty = exc
                continue
            answer = text.strip()
            if answer:
                return answer
            last_empty = LLMEmptyResponseError("Model returned an empty answer")
        assert last_empty is not None
        raise last_empty

    async def _call_provider(self, resolved: ResolvedProvider, user_prompt: str) -> str:
        timeout = self.settings.chat_generation_timeout_seconds
        text_only = (
            resolved.spec.id == "qwen" and "omni" in resolved.default_model.lower()
        )
        if resolved.spec.protocol == "anthropic":
            return await call_anthropic(
                base_url=resolved.base_url,
                api_key=resolved.api_key,
                model=resolved.default_model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                timeout=timeout,
                max_tokens=self.settings.chat_max_tokens,
                temperature=0.3,
                provider_id=resolved.spec.id,
            )
        return await call_openai_compatible(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.default_model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            timeout=timeout,
            max_tokens=self.settings.chat_max_tokens,
            temperature=0.3,
            text_only=text_only,
            disable_thinking=resolved.spec.supports_thinking_control,
            provider_id=resolved.spec.id,
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

    async def _require_stage(
        self, owner_id: UUID, course_id: UUID, stage_id: Optional[UUID] = None
    ) -> CourseStage:
        if stage_id is not None:
            requested = await self.db.scalar(
                select(CourseStage).where(
                    CourseStage.id == stage_id,
                    CourseStage.owner_id == owner_id,
                    CourseStage.course_id == course_id,
                )
            )
            if requested is not None:
                return requested
        stage = await self.db.scalar(
            select(CourseStage)
            .where(
                CourseStage.owner_id == owner_id,
                CourseStage.course_id == course_id,
            )
            .order_by(CourseStage.version.desc())
            .limit(1)
        )
        if stage is None:
            raise AppError(
                409,
                "CHAT_CLASSROOM_REQUIRED",
                "Generate the classroom before asking the teacher",
            )
        return stage

    async def _resolve_scene(
        self, owner_id: UUID, stage: CourseStage, scene_id: Optional[UUID]
    ) -> Optional[CourseScene]:
        if scene_id is None:
            return None
        return await self.db.scalar(
            select(CourseScene).where(
                CourseScene.id == scene_id,
                CourseScene.owner_id == owner_id,
                CourseScene.stage_id == stage.id,
            )
        )

    async def _latest_outline(
        self, owner_id: UUID, course_id: UUID
    ) -> Optional[CourseOutline]:
        return await self.db.scalar(
            select(CourseOutline)
            .where(
                CourseOutline.owner_id == owner_id,
                CourseOutline.course_id == course_id,
            )
            .order_by(CourseOutline.version.desc())
            .limit(1)
        )

    async def _get_session(
        self, owner_id: UUID, course_id: UUID
    ) -> Optional[CourseChatSession]:
        return await self.db.scalar(
            select(CourseChatSession)
            .where(
                CourseChatSession.owner_id == owner_id,
                CourseChatSession.course_id == course_id,
            )
            .limit(1)
        )

    async def _get_or_create_session(
        self, owner: User, course: Course
    ) -> CourseChatSession:
        existing = await self._get_session(owner.id, course.id)
        if existing is not None:
            return existing
        session = CourseChatSession(
            owner_id=owner.id, course_id=course.id, title=course.title
        )
        self.db.add(session)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self._get_session(owner.id, course.id)
            if existing is None:
                raise
            return existing
        await self.db.refresh(session)
        return session

    async def _list_messages(
        self, owner_id: UUID, session: CourseChatSession
    ) -> List[CourseChatMessage]:
        query = select(CourseChatMessage).where(
            CourseChatMessage.owner_id == owner_id,
            CourseChatMessage.session_id == session.id,
        )
        if session.cleared_at is not None:
            query = query.where(CourseChatMessage.created_at > session.cleared_at)
        rows = await self.db.scalars(
            query.order_by(CourseChatMessage.created_at, CourseChatMessage.id)
        )
        return list(rows.all())

    async def _recent_history(
        self, owner_id: UUID, session: CourseChatSession
    ) -> List[Dict[str, str]]:
        query = select(CourseChatMessage).where(
            CourseChatMessage.owner_id == owner_id,
            CourseChatMessage.session_id == session.id,
        )
        if session.cleared_at is not None:
            query = query.where(CourseChatMessage.created_at > session.cleared_at)
        rows = await self.db.scalars(
            query.order_by(
                CourseChatMessage.created_at.desc(), CourseChatMessage.id.desc()
            ).limit(self.settings.chat_history_messages)
        )
        items = [
            {"role": message.role, "content": message.content}
            for message in rows.all()
        ]
        items.reverse()
        return items

    async def _daily_used(self, owner_id: UUID) -> int:
        local_now = utcnow() + LOCAL_DAY_OFFSET
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - LOCAL_DAY_OFFSET
        used = await self.db.scalar(
            select(func.count())
            .select_from(CourseChatMessage)
            .where(
                CourseChatMessage.owner_id == owner_id,
                CourseChatMessage.role == "user",
                CourseChatMessage.created_at >= start,
            )
        )
        return int(used or 0)

    def _stage_label(self, stage: CourseStage) -> str:
        return f"第 {stage.version} 幕：{stage.name}"

    def _message_view(self, message: CourseChatMessage) -> ChatMessageView:
        return ChatMessageView(
            id=message.id,
            role=message.role,  # type: ignore[arg-type]
            content=message.content,
            stage_id=message.stage_id,
            stage_title=message.stage_title,
            stage_index=message.stage_index,
            scene_id=message.scene_id,
            scene_title=message.scene_title,
            provider_id=message.provider_id,
            model_name=message.model_name,
            created_at=message.created_at,
        )

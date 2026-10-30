import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utcnow
from app.models.classroom import ClassroomResponse, CourseScene, CourseStage
from app.models.course import Course
from app.models.user import User
from app.schemas.classroom import (
    ClassroomAnswerItem,
    ClassroomResponseSubmitRequest,
    ClassroomResponsesView,
    ClassroomResponseView,
    ClassroomResultView,
    ClassroomSubmitView,
)

logger = logging.getLogger(__name__)

MAX_SHORT_ANSWER_CHARS = 2000


class ClassroomResponseService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def submit(
        self,
        owner: User,
        course_id: UUID,
        scene_id: UUID,
        payload: ClassroomResponseSubmitRequest,
    ) -> ClassroomSubmitView:
        scene, stage = await self._get_scene(owner.id, course_id, scene_id)
        targets = self._targets(scene)

        results: List[ClassroomResultView] = []
        for item in payload.responses:
            target = targets.get((item.kind, item.target_id))
            if target is None:
                raise AppError(
                    400,
                    "CLASSROOM_TARGET_NOT_FOUND",
                    "Scene does not contain this question or task",
                )
            answer, score, max_score, correct = self._grade(item, target)
            record = await self.db.scalar(
                select(ClassroomResponse).where(
                    ClassroomResponse.owner_id == owner.id,
                    ClassroomResponse.scene_id == scene.id,
                    ClassroomResponse.target_id == item.target_id,
                )
            )
            if record is None:
                record = ClassroomResponse(
                    owner_id=owner.id,
                    course_id=stage.course_id,
                    stage_id=stage.id,
                    scene_id=scene.id,
                    kind=item.kind,
                    target_id=item.target_id,
                    answer=answer,
                    score=score,
                    max_score=max_score,
                    correct=correct,
                )
                self.db.add(record)
            else:
                record.kind = item.kind
                record.answer = answer
                record.score = score
                record.max_score = max_score
                record.correct = correct
                record.updated_at = utcnow()
            results.append(
                ClassroomResultView(
                    target_id=item.target_id,
                    kind=item.kind,
                    score=score,
                    max_score=max_score,
                    correct=correct,
                    reference_answer=[str(value) for value in target.get("answer") or []],
                    analysis=target.get("analysis"),
                )
            )

        await self.db.flush()
        totals = await self._scene_totals(owner.id, scene.id)
        await self.db.commit()
        return ClassroomSubmitView(
            scene_id=scene.id,
            stage_id=stage.id,
            results=results,
            score=totals[0],
            max_score=totals[1],
        )

    async def list_responses(
        self, owner: User, course_id: UUID
    ) -> ClassroomResponsesView:
        await self._get_course(owner.id, course_id)
        stage = await self.db.scalar(
            select(CourseStage)
            .where(
                CourseStage.course_id == course_id,
                CourseStage.owner_id == owner.id,
            )
            .order_by(CourseStage.version.desc())
            .limit(1)
        )
        if stage is None:
            return ClassroomResponsesView(stage_id=None, responses=[])
        records = (
            await self.db.scalars(
                select(ClassroomResponse)
                .where(
                    ClassroomResponse.owner_id == owner.id,
                    ClassroomResponse.stage_id == stage.id,
                )
                .order_by(ClassroomResponse.submitted_at)
            )
        ).all()
        return ClassroomResponsesView(
            stage_id=stage.id,
            responses=[
                ClassroomResponseView(
                    id=record.id,
                    course_id=record.course_id,
                    stage_id=record.stage_id,
                    scene_id=record.scene_id,
                    kind=record.kind,  # type: ignore[arg-type]
                    target_id=record.target_id,
                    answer=record.answer,
                    score=record.score,
                    max_score=record.max_score,
                    correct=record.correct,
                    submitted_at=record.submitted_at,
                )
                for record in records
            ],
        )

    async def _scene_totals(self, owner_id: UUID, scene_id: UUID) -> Tuple[int, int]:
        records = (
            await self.db.scalars(
                select(ClassroomResponse).where(
                    ClassroomResponse.owner_id == owner_id,
                    ClassroomResponse.scene_id == scene_id,
                )
            )
        ).all()
        score = sum(record.score or 0 for record in records if record.score is not None)
        max_score = sum(record.max_score for record in records)
        return score, max_score

    def _targets(self, scene: CourseScene) -> Dict[Tuple[str, str], Dict[str, Any]]:
        content = scene.content or {}
        targets: Dict[Tuple[str, str], Dict[str, Any]] = {}
        if content.get("type") == "quiz":
            for question in content.get("questions") or []:
                if isinstance(question, dict) and isinstance(question.get("id"), str):
                    targets[("question", question["id"])] = question
        elif content.get("type") == "pbl":
            project = content.get("project") or {}
            for milestone in project.get("milestones") or []:
                for task in (milestone or {}).get("microtasks") or []:
                    if isinstance(task, dict) and isinstance(task.get("id"), str):
                        targets[("microtask", task["id"])] = task
        return targets

    def _grade(
        self, item: ClassroomAnswerItem, target: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Optional[int], int, Optional[bool]]:
        if item.kind == "microtask":
            if not isinstance(item.answer, dict):
                raise AppError(
                    400,
                    "CLASSROOM_ANSWER_INVALID",
                    "Project tasks expect a completion flag",
                )
            completed = bool(item.answer.get("completed"))
            return {"completed": completed}, None, 0, completed

        if not isinstance(item.answer, list):
            raise AppError(
                400,
                "CLASSROOM_ANSWER_INVALID",
                "Questions expect selected values or short answer text",
            )

        if target.get("type") == "short_answer":
            text = ""
            if item.answer:
                text = str(item.answer[0])[:MAX_SHORT_ANSWER_CHARS]
            if not text:
                raise AppError(
                    400,
                    "CLASSROOM_ANSWER_INVALID",
                    "Short answers must contain text",
                )
            return {"text": text}, None, 0, None

        expected = {str(value) for value in target.get("answer") or []}
        selected = {str(value) for value in item.answer}
        if not expected:
            return {"selected": sorted(selected)}, None, 0, None
        correct = selected == expected
        return {"selected": sorted(selected)}, 1 if correct else 0, 1, correct

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

    async def _get_scene(
        self, owner_id: UUID, course_id: UUID, scene_id: UUID
    ) -> Tuple[CourseScene, CourseStage]:
        row = await self.db.execute(
            select(CourseScene, CourseStage)
            .join(CourseStage, CourseStage.id == CourseScene.stage_id)
            .join(Course, Course.id == CourseStage.course_id)
            .where(
                CourseScene.id == scene_id,
                CourseStage.course_id == course_id,
                CourseScene.owner_id == owner_id,
                CourseStage.owner_id == owner_id,
                Course.owner_id == owner_id,
                Course.deleted_at.is_(None),
            )
        )
        found = row.first()
        if found is None:
            raise AppError(
                404, "CLASSROOM_SCENE_NOT_FOUND", "Classroom scene not found"
            )
        return found[0], found[1]

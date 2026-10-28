from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class CourseStage(Base):
    __tablename__ = 'course_stages'
    __table_args__ = (
        CheckConstraint('version >= 1', name='classroom_stage_version_positive'),
        CheckConstraint('scene_count >= 0', name='classroom_stage_scene_count'),
        UniqueConstraint('course_id', 'version', name='uq_course_stages_course_version'),
        Index('ix_course_stages_owner_course', 'owner_id', 'course_id', 'version'),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )
    course_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('courses.id', ondelete='CASCADE'),
        nullable=False,
    )
    outline_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('course_outlines.id', ondelete='SET NULL'),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default='')
    style: Mapped[str] = mapped_column(Text, nullable=False, default='')
    language_directive: Mapped[str] = mapped_column(Text, nullable=False, default='')
    dsl_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default='0.12.0', server_default=text("'0.12.0'")
    )
    provider_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    scene_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text('0')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

class CourseScene(Base):
    __tablename__ = 'course_scenes'
    __table_args__ = (
        CheckConstraint(
            "scene_type IN ('slide', 'quiz', 'interactive', 'pbl')",
            name='classroom_scene_type',
        ),
        CheckConstraint('order_index >= 1', name='classroom_scene_order_positive'),
        UniqueConstraint('stage_id', 'order_index', name='uq_course_scenes_stage_order'),
        Index('ix_course_scenes_owner_stage', 'owner_id', 'stage_id', 'order_index'),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )
    stage_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('course_stages.id', ondelete='CASCADE'),
        nullable=False,
    )
    scene_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actions: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class GenerationJob(Base):
    __tablename__ = 'generation_jobs'
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name='generation_job_status',
        ),
        CheckConstraint('progress >= 0 AND progress <= 100', name='generation_job_progress'),
        CheckConstraint('scene_count >= 0', name='generation_job_scene_count'),
        Index('ix_generation_jobs_owner_created', 'owner_id', 'created_at'),
        Index('ix_generation_jobs_course_status', 'course_id', 'status'),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )
    course_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('courses.id', ondelete='CASCADE'),
        nullable=False,
    )
    outline_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('course_outlines.id', ondelete='SET NULL'),
        nullable=True,
    )
    stage_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey('course_stages.id', ondelete='SET NULL'),
        nullable=True,
    )
    provider_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default='queued', server_default=text("'queued'")
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text('0')
    )
    message: Mapped[str] = mapped_column(
        Text, nullable=False, default='', server_default=text("''")
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scene_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text('0')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

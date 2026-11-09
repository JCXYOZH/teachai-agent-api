from datetime import datetime
from typing import Optional
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
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class CourseChatSession(Base):
    __tablename__ = "course_chat_sessions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "course_id", name="uq_course_chat_sessions_owner_course"
        ),
        Index("ix_course_chat_sessions_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default=text("''")
    )
    cleared_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class CourseChatMessage(Base):
    __tablename__ = "course_chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="course_chat_message_role"),
        CheckConstraint(
            "stage_index IS NULL OR stage_index >= 1",
            name="course_chat_message_stage_index",
        ),
        Index("ix_course_chat_messages_session_created", "session_id", "created_at"),
        Index("ix_course_chat_messages_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("course_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("course_stages.id", ondelete="SET NULL"),
        nullable=True,
    )
    stage_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    stage_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scene_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("course_scenes.id", ondelete="SET NULL"),
        nullable=True,
    )
    scene_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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


class CourseOutline(Base):
    __tablename__ = "course_outlines"
    __table_args__ = (
        CheckConstraint("version >= 1", name="outline_version_positive"),
        CheckConstraint("source IN ('generated', 'manual')", name="outline_source"),
        CheckConstraint(
            "material_asset_count >= 0", name="outline_material_asset_count"
        ),
        CheckConstraint(
            "material_char_count >= 0", name="outline_material_char_count"
        ),
        UniqueConstraint("course_id", "version", name="uq_course_outlines_course_version"),
        Index("ix_course_outlines_owner_course", "owner_id", "course_id", "version"),
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
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    language_directive: Mapped[str] = mapped_column(Text, nullable=False)
    course_title: Mapped[str] = mapped_column(String(200), nullable=False)
    scenes: Mapped[List[Any]] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="outline-v1", server_default=text("'outline-v1'")
    )
    material_asset_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    material_char_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    materials_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

"""add course chat tables

Revision ID: 0014_course_chat
Revises: 0013_export_jobs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_course_chat"
down_revision = "0013_export_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "course_id", name="uq_course_chat_sessions_owner_course"
        ),
    )
    op.create_index(
        "ix_course_chat_sessions_owner_updated",
        "course_chat_sessions",
        ["owner_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "course_chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stage_title", sa.String(length=200), nullable=True),
        sa.Column("stage_index", sa.Integer(), nullable=True),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scene_title", sa.String(length=200), nullable=True),
        sa.Column("provider_id", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="course_chat_message_role"
        ),
        sa.CheckConstraint(
            "stage_index IS NULL OR stage_index >= 1",
            name="course_chat_message_stage_index",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["course_chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["scene_id"], ["course_scenes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["course_stages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_course_chat_messages_session_created",
        "course_chat_messages",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_course_chat_messages_owner_created",
        "course_chat_messages",
        ["owner_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_chat_messages_owner_created", table_name="course_chat_messages"
    )
    op.drop_index(
        "ix_course_chat_messages_session_created", table_name="course_chat_messages"
    )
    op.drop_table("course_chat_messages")
    op.drop_index(
        "ix_course_chat_sessions_owner_updated", table_name="course_chat_sessions"
    )
    op.drop_table("course_chat_sessions")

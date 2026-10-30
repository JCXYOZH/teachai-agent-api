"""store classroom answers and project task completion

Revision ID: 0010_classroom_responses
Revises: 0009_classroom_dsl_version
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_classroom_responses"
down_revision = "0009_classroom_dsl_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classroom_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("max_score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=True),
        sa.Column(
            "submitted_at",
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
        sa.CheckConstraint(
            "kind IN ('question', 'microtask')", name="classroom_response_kind"
        ),
        sa.CheckConstraint(
            "score IS NULL OR score >= 0", name="classroom_response_score"
        ),
        sa.CheckConstraint("max_score >= 0", name="classroom_response_max_score"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["scene_id"], ["course_scenes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["course_stages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "scene_id", "target_id", name="uq_classroom_responses_target"
        ),
    )
    op.create_index(
        "ix_classroom_responses_owner_course",
        "classroom_responses",
        ["owner_id", "course_id"],
        unique=False,
    )
    op.create_index(
        "ix_classroom_responses_stage_scene",
        "classroom_responses",
        ["stage_id", "scene_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classroom_responses_stage_scene", table_name="classroom_responses"
    )
    op.drop_index(
        "ix_classroom_responses_owner_course", table_name="classroom_responses"
    )
    op.drop_table("classroom_responses")

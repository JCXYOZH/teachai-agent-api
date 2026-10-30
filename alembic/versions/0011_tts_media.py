"""add TTS provider settings and media assets

Revision ID: 0011_tts_media
Revises: 0010_classroom_responses
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_tts_media"
down_revision = "0010_classroom_responses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_configs", sa.Column("tts_base_url", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "provider_configs", sa.Column("tts_model", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "provider_configs", sa.Column("tts_voice", sa.String(length=120), nullable=True)
    )

    op.drop_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        "kind IN ('outline', 'classroom', 'narration')",
    )

    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_id", sa.String(length=120), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("voice", sa.String(length=120), nullable=True),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "kind IN ('audio', 'image', 'export')", name="media_asset_kind"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["course_scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["course_stages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
        sa.UniqueConstraint(
            "owner_id",
            "stage_id",
            "scene_id",
            "action_id",
            "kind",
            name="uq_media_assets_scene_action",
        ),
    )
    op.create_index(
        "ix_media_assets_owner_course",
        "media_assets",
        ["owner_id", "course_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_assets_owner_hash",
        "media_assets",
        ["owner_id", "kind", "content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_media_assets_stage_scene",
        "media_assets",
        ["stage_id", "scene_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_stage_scene", table_name="media_assets")
    op.drop_index("ix_media_assets_owner_hash", table_name="media_assets")
    op.drop_index("ix_media_assets_owner_course", table_name="media_assets")
    op.drop_table("media_assets")

    op.drop_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        "kind IN ('outline', 'classroom')",
    )

    op.drop_column("provider_configs", "tts_voice")
    op.drop_column("provider_configs", "tts_model")
    op.drop_column("provider_configs", "tts_base_url")

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_classroom_stages"
down_revision = "0004_course_outlines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outline_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("style", sa.Text(), nullable=False),
        sa.Column("language_directive", sa.Text(), nullable=False),
        sa.Column(
            "dsl_version",
            sa.String(length=20),
            server_default=sa.text("'0.12.0'"),
            nullable=False,
        ),
        sa.Column("provider_id", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column(
            "scene_count",
            sa.Integer(),
            server_default=sa.text("0"),
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
            "scene_count >= 0",
            name="ck_course_stages_classroom_stage_scene_count",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_course_stages_classroom_stage_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_course_stages_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["outline_id"],
            ["course_outlines.id"],
            name="fk_course_stages_outline_id_course_outlines",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_course_stages_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_stages"),
        sa.UniqueConstraint("course_id", "version", name="uq_course_stages_course_version"),
    )
    op.create_index(
        "ix_course_stages_owner_course",
        "course_stages",
        ["owner_id", "course_id", "version"],
        unique=False,
    )

    op.create_table(
        "course_scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "actions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
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
            "order_index >= 1",
            name="ck_course_scenes_classroom_scene_order_positive",
        ),
        sa.CheckConstraint(
            "scene_type IN ('slide', 'quiz', 'interactive', 'pbl')",
            name="ck_course_scenes_classroom_scene_type",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_course_scenes_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["course_stages.id"],
            name="fk_course_scenes_stage_id_course_stages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_scenes"),
        sa.UniqueConstraint("stage_id", "order_index", name="uq_course_scenes_stage_order"),
    )
    op.create_index(
        "ix_course_scenes_owner_stage",
        "course_scenes",
        ["owner_id", "stage_id", "order_index"],
        unique=False,
    )

    op.create_table(
        "generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outline_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "progress",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "scene_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_generation_jobs_generation_job_progress",
        ),
        sa.CheckConstraint(
            "scene_count >= 0",
            name="ck_generation_jobs_generation_job_scene_count",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_generation_jobs_generation_job_status",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_generation_jobs_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["outline_id"],
            ["course_outlines.id"],
            name="fk_generation_jobs_outline_id_course_outlines",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_generation_jobs_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["course_stages.id"],
            name="fk_generation_jobs_stage_id_course_stages",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_generation_jobs"),
    )
    op.create_index(
        "ix_generation_jobs_course_status",
        "generation_jobs",
        ["course_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_generation_jobs_owner_created",
        "generation_jobs",
        ["owner_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_owner_created", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_course_status", table_name="generation_jobs")
    op.drop_table("generation_jobs")
    op.drop_index("ix_course_scenes_owner_stage", table_name="course_scenes")
    op.drop_table("course_scenes")
    op.drop_index("ix_course_stages_owner_course", table_name="course_stages")
    op.drop_table("course_stages")

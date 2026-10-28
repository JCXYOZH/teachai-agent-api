from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_course_outlines"
down_revision = "0003_provider_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_outlines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("language_directive", sa.Text(), nullable=False),
        sa.Column("course_title", sa.String(length=200), nullable=False),
        sa.Column(
            "scenes", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "prompt_version",
            sa.String(length=40),
            server_default=sa.text("'outline-v1'"),
            nullable=False,
        ),
        sa.Column(
            "material_asset_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "material_char_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "materials_truncated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "material_asset_count >= 0", name="ck_course_outlines_outline_material_asset_count"
        ),
        sa.CheckConstraint(
            "material_char_count >= 0", name="ck_course_outlines_outline_material_char_count"
        ),
        sa.CheckConstraint(
            "source IN ('generated', 'manual')",
            name="ck_course_outlines_outline_source",
        ),
        sa.CheckConstraint(
            "version >= 1", name="ck_course_outlines_outline_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_course_outlines_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_course_outlines_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_outlines"),
        sa.UniqueConstraint(
            "course_id", "version", name="uq_course_outlines_course_version"
        ),
    )
    op.create_index(
        "ix_course_outlines_owner_course",
        "course_outlines",
        ["owner_id", "course_id", "version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_course_outlines_owner_course", table_name="course_outlines")
    op.drop_table("course_outlines")

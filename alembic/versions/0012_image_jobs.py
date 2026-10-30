"""allow classroom illustration generation jobs

Revision ID: 0012_image_jobs
Revises: 0011_tts_media
"""

from alembic import op

revision = "0012_image_jobs"
down_revision = "0011_tts_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        "kind IN ('outline', 'classroom', 'narration', 'image')",
    )


def downgrade() -> None:
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

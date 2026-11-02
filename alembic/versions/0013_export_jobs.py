"""allow classroom export jobs

Revision ID: 0013_export_jobs
Revises: 0012_image_jobs
"""

from alembic import op

revision = "0013_export_jobs"
down_revision = "0012_image_jobs"
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
        "kind IN ('outline', 'classroom', 'narration', 'image', 'export')",
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
        "kind IN ('outline', 'classroom', 'narration', 'image')",
    )
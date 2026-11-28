"""one active generation job per owner, course and kind

Revision ID: 0017_active_job_unique
Revises: 0016_sms_send_logs
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_active_job_unique"
down_revision = "0016_sms_send_logs"
branch_labels = None
depends_on = None

ACTIVE_JOBS = "status IN ('queued', 'running')"


def upgrade() -> None:
    # Concurrent create_job() calls could insert duplicate active rows before the
    # partial unique index existed. Keep the newest row per (owner, course, kind)
    # and mark the stale duplicates as failed so the index can be created.
    op.execute(
        """
        UPDATE generation_jobs
        SET status = 'failed',
            message = 'Generation superseded',
            error_code = 'GENERATION_SUPERSEDED',
            error_message = 'Duplicate active job removed by migration 0017',
            completed_at = now()
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY owner_id, course_id, kind
                           ORDER BY created_at DESC
                       ) AS row_num
                FROM generation_jobs
                WHERE status IN ('queued', 'running')
            ) AS duplicates
            WHERE duplicates.row_num > 1
        )
        """
    )
    op.create_index(
        "uq_generation_jobs_active",
        "generation_jobs",
        ["owner_id", "course_id", "kind"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_JOBS),
    )


def downgrade() -> None:
    op.drop_index("uq_generation_jobs_active", table_name="generation_jobs")

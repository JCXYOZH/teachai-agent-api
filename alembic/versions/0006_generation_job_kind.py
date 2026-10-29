"""generation job kind and payload

Revision ID: 0006_generation_job_kind
Revises: 0005_classroom_stages
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_generation_job_kind"
down_revision = "0005_classroom_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column(
            "kind",
            sa.String(length=20),
            server_default=sa.text("'classroom'"),
            nullable=False,
        ),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        "kind IN ('outline', 'classroom')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_generation_jobs_generation_job_kind",
        "generation_jobs",
        type_="check",
    )
    op.drop_column("generation_jobs", "payload")
    op.drop_column("generation_jobs", "kind")

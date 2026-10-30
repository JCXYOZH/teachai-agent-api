"""bump classroom DSL version default

Revision ID: 0009_classroom_dsl_version
Revises: 0008_email_verification_code
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_classroom_dsl_version"
down_revision = "0008_email_verification_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "course_stages",
        "dsl_version",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default=sa.text("'0.13.0'"),
    )


def downgrade() -> None:
    op.alter_column(
        "course_stages",
        "dsl_version",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default=sa.text("'0.12.0'"),
    )

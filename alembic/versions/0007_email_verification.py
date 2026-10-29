"""pending email verification

Revision ID: 0007_email_verification
Revises: 0006_generation_job_kind
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_email_verification"
down_revision = "0006_generation_job_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT lower(email) AS email, count(*) AS total FROM users "
            "GROUP BY lower(email) HAVING count(*) > 1"
        )
    ).fetchall()
    if duplicates:
        summary = ", ".join(f"{row.email} ({row.total})" for row in duplicates)
        raise RuntimeError(
            "Cannot enforce case-insensitive unique emails; resolve duplicates first: " + summary
        )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )
    op.create_table(
        "email_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_verifications"),
    )
    op.create_index(
        "ix_email_verifications_email_created_at",
        "email_verifications",
        ["email", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_email_verifications_email_pending",
        "email_verifications",
        ["email"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_email_verifications_email_pending", table_name="email_verifications")
    op.drop_index("ix_email_verifications_email_created_at", table_name="email_verifications")
    op.drop_table("email_verifications")
    op.drop_index("uq_users_email_lower", table_name="users")


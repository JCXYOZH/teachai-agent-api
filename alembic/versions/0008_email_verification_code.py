"""store only registration verification codes

Revision ID: 0008_email_verification_code
Revises: 0007_email_verification
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_email_verification_code"
down_revision = "0007_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("email_verifications", "password_hash")
    op.drop_column("email_verifications", "display_name")


def downgrade() -> None:
    op.add_column(
        "email_verifications",
        sa.Column(
            "password_hash",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.add_column(
        "email_verifications",
        sa.Column(
            "display_name",
            sa.String(length=80),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.alter_column("email_verifications", "password_hash", server_default=None)
    op.alter_column("email_verifications", "display_name", server_default=None)

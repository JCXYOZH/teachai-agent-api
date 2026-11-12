"""verification send logs for sms throttling

Revision ID: 0016_sms_send_logs
Revises: 0015_phone_auth
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_sms_send_logs"
down_revision = "0015_phone_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verification_send_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=320), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_verification_send_logs"),
    )
    op.create_index(
        "ix_verification_send_logs_ip_created",
        "verification_send_logs",
        ["ip_address", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_verification_send_logs_target_created",
        "verification_send_logs",
        ["channel", "target", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_verification_send_logs_target_created",
        table_name="verification_send_logs",
    )
    op.drop_index(
        "ix_verification_send_logs_ip_created",
        table_name="verification_send_logs",
    )
    op.drop_table("verification_send_logs")

"""phone registration support

Revision ID: 0015_phone_auth
Revises: 0014_course_chat
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_phone_auth"
down_revision = "0014_course_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=True,
    )
    op.add_column("users", sa.Column("phone", sa.String(length=32), nullable=True))
    op.create_unique_constraint("uq_users_phone", "users", ["phone"])
    op.create_check_constraint(
        "contact_present",
        "users",
        "email IS NOT NULL OR phone IS NOT NULL",
    )
    op.create_table(
        "phone_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_phone_verifications"),
    )
    op.create_index(
        "ix_phone_verifications_phone_created_at",
        "phone_verifications",
        ["phone", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_phone_verifications_phone_pending",
        "phone_verifications",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_phone_verifications_phone_pending", table_name="phone_verifications"
    )
    op.drop_index(
        "ix_phone_verifications_phone_created_at", table_name="phone_verifications"
    )
    op.drop_table("phone_verifications")
    op.drop_constraint("contact_present", "users", type_="check")
    op.drop_constraint("uq_users_phone", "users", type_="unique")
    op.drop_column("users", "phone")
    bind = op.get_bind()
    orphans = bind.execute(
        sa.text("SELECT count(*) FROM users WHERE email IS NULL")
    ).scalar()
    if orphans:
        raise RuntimeError(
            "Cannot restore users.email NOT NULL: "
            f"{orphans} phone-only accounts must get an email first"
        )
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=False,
    )

"""Record accepted legal documents; single-use password reset tokens.

`users.terms_version` and `users.terms_accepted_at` stay NULL for existing accounts: nobody
accepted anything for them. `password_reset_tokens` stores only a hash of each token.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-31 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_version", sa.String(length=32), nullable=True))
    op.add_column(
        "users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "password_reset_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_password_reset_tokens_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_reset_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_reset_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_password_reset_tokens_user_id"),
        "password_reset_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    # Loses outstanding reset links and the record of who accepted which documents.
    op.drop_index(op.f("ix_password_reset_tokens_user_id"), table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "terms_version")

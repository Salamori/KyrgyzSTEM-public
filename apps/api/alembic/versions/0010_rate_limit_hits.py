"""Rate-limit hits for the PostgreSQL limiter (RATE_LIMIT_BACKEND=postgres).

One row per counted request: an HMAC of the limiter key (never the key, which contains IP
addresses, emails and user ids), when it was counted and when it stops counting. Rows are
deleted once expired (core/rate_limit.py, scripts.purge_retention). The default in-memory
limiter does not use the table.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-30 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("hit_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "expires_at > hit_at", name=op.f("ck_rate_limit_hits_expires_after_hit")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit_hits")),
    )
    op.create_index(
        "ix_rate_limit_hits_key_hit", "rate_limit_hits", ["key_hash", "hit_at"], unique=False
    )
    op.create_index(
        op.f("ix_rate_limit_hits_expires_at"), "rate_limit_hits", ["expires_at"], unique=False
    )


def downgrade() -> None:
    # Only short-lived counters: dropping them restarts the current windows.
    op.drop_index(op.f("ix_rate_limit_hits_expires_at"), table_name="rate_limit_hits")
    op.drop_index("ix_rate_limit_hits_key_hit", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")

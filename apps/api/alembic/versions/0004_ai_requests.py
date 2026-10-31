"""AI assistant request log: metadata only, no prompt or answer text (proposal 0004).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25 00:38:15.735463
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_requests",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("problem_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("locale_requested", sa.String(length=2), nullable=False),
        sa.Column("locale_answered", sa.String(length=2), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model_id", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("mode IN ('ai', 'fallback')", name=op.f("ck_ai_requests_mode")),
        sa.CheckConstraint("level BETWEEN 1 AND 4", name=op.f("ck_ai_requests_level")),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["problems.id"],
            name=op.f("fk_ai_requests_problem_id_problems"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ai_requests_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_requests")),
    )
    op.create_index(op.f("ix_ai_requests_problem_id"), "ai_requests", ["problem_id"], unique=False)
    op.create_index(
        "ix_ai_requests_user_created", "ai_requests", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ai_requests_user_created", table_name="ai_requests")
    op.drop_index(op.f("ix_ai_requests_problem_id"), table_name="ai_requests")
    op.drop_table("ai_requests")

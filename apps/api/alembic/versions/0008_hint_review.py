"""Hint review: who reviewed each hint, and when.

Adds nullable hints.reviewed_by (users.id, SET NULL) and hints.reviewed_at. Existing hints
keep their rows and text and start unreviewed. Reviews are set only through the admin API
(audited) and cleared by a re-import that changes a locale's hints.

Downgrade drops the two columns only: hint rows and text are kept, but all hint review
metadata (who reviewed which hint and when) is lost. The audit log keeps its entries.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hints", sa.Column("reviewed_by", sa.Uuid(), nullable=True))
    op.add_column("hints", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_hints_reviewed_by_users"),
        "hints",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_hints_reviewed_by_users"), "hints", type_="foreignkey")
    op.drop_column("hints", "reviewed_at")
    op.drop_column("hints", "reviewed_by")

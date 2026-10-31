"""Problem provenance: structured source and human rights verification.

`source` holds {title, author, url, license} of a reused work and is required unless the
problem is original (CHECK added by hand). `rights_verified_*` records the human who
confirmed the right to publish under the declared license; publishing is blocked until
then. Existing rows are left unverified on purpose: no license is assumed or assigned.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25 20:56:24.917173
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "problems",
        sa.Column("source", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("problems", sa.Column("rights_verified_by", sa.Uuid(), nullable=True))
    op.add_column(
        "problems", sa.Column("rights_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_problems_rights_verified_by_users"),
        "problems",
        "users",
        ["rights_verified_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_problems_source_for_reuse"),
        "problems",
        "origin = 'original' OR source IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_problems_source_for_reuse"), "problems", type_="check")
    op.drop_constraint(op.f("fk_problems_rights_verified_by_users"), "problems", type_="foreignkey")
    op.drop_column("problems", "rights_verified_at")
    op.drop_column("problems", "rights_verified_by")
    op.drop_column("problems", "source")

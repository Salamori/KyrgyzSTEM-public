"""Problem source: store "no source" as SQL NULL and enforce its shape in the database.

0006 required `source IS NOT NULL` for reused problems, but the column stored Python None
as the JSON value null, which is NOT NULL in SQL, so a reused problem could pass the CHECK
with no source. This migration:

1. checks the data first and aborts, changing nothing, if a reused problem has no source
   object or an original problem has a real source (both would violate the new CHECK and
   need a human decision, not a silent fix);
2. converts JSON null to SQL NULL (only original problems remain with it after step 1);
3. replaces ck_problems_source_for_reuse with ck_problems_source_shape: original problems
   have SQL NULL, reused ones a JSON object.

No other column or row is touched. Downgrade restores the 0006 constraint; SQL NULL sources
satisfy it, so no data change is needed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CHECK = "origin = 'original' OR source IS NOT NULL"
NEW_CHECK = (
    "(origin = 'original' AND source IS NULL) OR (origin <> 'original' "
    "AND source IS NOT NULL AND jsonb_typeof(source) = 'object')"
)


REUSED_WITHOUT_SOURCE = sa.text(
    "SELECT slug FROM problems WHERE origin <> 'original' "
    "AND (source IS NULL OR jsonb_typeof(source) <> 'object') ORDER BY slug"
)
ORIGINAL_WITH_SOURCE = sa.text(
    "SELECT slug FROM problems WHERE origin = 'original' "
    "AND source IS NOT NULL AND jsonb_typeof(source) <> 'null' ORDER BY slug"
)


def upgrade() -> None:
    bind = op.get_bind()
    reused_without_source = list(bind.execute(REUSED_WITHOUT_SOURCE).scalars())
    original_with_source = list(bind.execute(ORIGINAL_WITH_SOURCE).scalars())
    if reused_without_source or original_with_source:
        raise RuntimeError(
            "Migration 0007 aborted, nothing changed. Fix these problems' provenance first "
            "(docs/CONTENT_GUIDE.md): "
            f"reused without a source object: {reused_without_source or 'none'}; "
            f"original with a source: {original_with_source or 'none'}."
        )

    op.execute("UPDATE problems SET source = NULL WHERE jsonb_typeof(source) = 'null'")
    op.drop_constraint(op.f("ck_problems_source_for_reuse"), "problems", type_="check")
    op.create_check_constraint(op.f("ck_problems_source_shape"), "problems", NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_problems_source_shape"), "problems", type_="check")
    op.create_check_constraint(op.f("ck_problems_source_for_reuse"), "problems", OLD_CHECK)

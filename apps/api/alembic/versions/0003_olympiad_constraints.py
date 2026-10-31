"""Olympiad integrity constraints (CHECKs and one-primary-topic index).

Alembic autogenerate does not detect CHECK constraints, so they are listed by hand and
must match the models; tests/test_meta.py compares the resulting schema.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, name suffix, condition) — full name is ck_<table>_<suffix> (naming convention).
CHECKS = [
    ("topics", "stage_count", "stage_count BETWEEN 1 AND 4"),
    ("topic_prerequisites", "stages", "stage BETWEEN 1 AND 4 AND requires_stage BETWEEN 1 AND 4"),
    (
        "topic_prerequisites",
        "no_self_loop",
        "NOT (topic_id = requires_topic_id AND stage <= requires_stage)",
    ),
    ("problems", "published_has_timestamp", "status <> 'published' OR published_at IS NOT NULL"),
    ("problem_topics", "stage", "stage BETWEEN 1 AND 4"),
    ("test_groups", "ordinal_points", "ordinal >= 0 AND points BETWEEN 0 AND 100"),
    ("test_cases", "ordinal", "ordinal >= 1"),
    ("hints", "ordinal", "ordinal >= 1"),
    ("submissions", "hints_revealed_count", "hints_revealed_count >= 0"),
    ("submissions", "verdict_requires_done", "verdict IS NULL OR state = 'done'"),
    ("submission_group_results", "score", "score >= 0"),
    ("hint_reveals", "ordinal", "ordinal >= 1"),
]


def upgrade() -> None:
    for table, suffix, condition in CHECKS:
        op.create_check_constraint(op.f(f"ck_{table}_{suffix}"), table, condition)
    op.create_index(
        "uq_problem_topics_one_primary",
        "problem_topics",
        ["problem_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_problem_topics_one_primary",
        table_name="problem_topics",
        postgresql_where=sa.text("is_primary"),
    )
    for table, suffix, _ in reversed(CHECKS):
        op.drop_constraint(op.f(f"ck_{table}_{suffix}"), table, type_="check")

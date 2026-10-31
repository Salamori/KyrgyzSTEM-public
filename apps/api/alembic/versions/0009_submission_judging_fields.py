"""Submission judging fields, and group results that survive a re-import.

Submissions gain the metadata a judge worker needs:

* `tests_hash` — the problem's tests_hash the submission was judged against. Existing
  submissions keep NULL: the test set they would have used is unknown, and no hash is
  invented.
* `judge_attempts` (default 0), `lease_expires_at` (only while `judging`) and
  `compile_message` (only with verdict `compile_error`, at most 4096 characters).

`submission_group_results` keeps its own copy of the group's ordinal and points, and its
link to `test_groups` becomes ON DELETE SET NULL. Before, a re-import (which replaces a
problem's test groups) deleted every earlier result through ON DELETE CASCADE. The key
becomes (submission_id, group_ordinal), since group_id may now be NULL.

The upgrade first checks the data and aborts, changing nothing, if a result's group belongs
to another problem than its submission, if two results of one submission share a group
ordinal, or if a score exceeds its group's points: those need a human decision, not a
silent fix.

Downgrade restores the 0008 schema only when nothing would be lost: it aborts, changing
nothing, if any result's group was deleted (group_id NULL; the 0008 key cannot hold it), if
a stored ordinal or points no longer matches its group, or if any submission has judging
metadata set. Historical results are never deleted.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBMISSION_CHECKS = [
    ("tests_hash_format", "tests_hash IS NULL OR tests_hash ~ '^[0-9a-f]{64}$'"),
    ("judge_attempts", "judge_attempts >= 0"),
    ("lease_only_while_judging", "lease_expires_at IS NULL OR state = 'judging'"),
    (
        "compile_message",
        "compile_message IS NULL OR (verdict = 'compile_error' "
        "AND char_length(compile_message) <= 4096)",
    ),
]
RESULT_CHECKS = [
    ("score_within_points", "score <= points"),
    ("group_ordinal_points", "group_ordinal >= 0 AND points BETWEEN 0 AND 100"),
]

RESULTS = "submission_group_results"
FK_GROUP = "fk_submission_group_results_group_id_test_groups"
PK = "pk_submission_group_results"
IX_GROUP = "ix_submission_group_results_group_id"

UPGRADE_CHECKS = {
    "a result's group belongs to another problem than its submission": sa.text(
        "SELECT count(*) FROM submission_group_results r "
        "JOIN submissions s ON s.id = r.submission_id "
        "JOIN test_groups g ON g.id = r.group_id WHERE g.problem_id <> s.problem_id"
    ),
    "two results of one submission share a group ordinal": sa.text(
        "SELECT count(*) FROM (SELECT r.submission_id FROM submission_group_results r "
        "JOIN test_groups g ON g.id = r.group_id "
        "GROUP BY r.submission_id, g.ordinal HAVING count(*) > 1) d"
    ),
    "a result's score exceeds its group's points": sa.text(
        "SELECT count(*) FROM submission_group_results r "
        "JOIN test_groups g ON g.id = r.group_id WHERE r.score > g.points"
    ),
}
DOWNGRADE_CHECKS = {
    "results whose test group was deleted (group_id is NULL)": sa.text(
        "SELECT count(*) FROM submission_group_results WHERE group_id IS NULL"
    ),
    "results whose stored ordinal or points differ from their group": sa.text(
        "SELECT count(*) FROM submission_group_results r "
        "JOIN test_groups g ON g.id = r.group_id "
        "WHERE g.ordinal <> r.group_ordinal OR g.points <> r.points"
    ),
    "submissions with judging metadata": sa.text(
        "SELECT count(*) FROM submissions WHERE tests_hash IS NOT NULL OR judge_attempts <> 0 "
        "OR lease_expires_at IS NOT NULL OR compile_message IS NOT NULL"
    ),
}


def _abort_if(checks: dict[str, sa.TextClause], step: str) -> None:
    bind = op.get_bind()
    problems = [f"{n} {what}" for what, q in checks.items() if (n := bind.execute(q).scalar_one())]
    if problems:
        raise RuntimeError(
            f"Migration 0009 {step} aborted, nothing changed: {'; '.join(problems)}."
        )


def upgrade() -> None:
    _abort_if(UPGRADE_CHECKS, "upgrade")

    op.add_column("submissions", sa.Column("tests_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "submissions",
        sa.Column("judge_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "submissions", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("submissions", sa.Column("compile_message", sa.Text(), nullable=True))
    for suffix, condition in SUBMISSION_CHECKS:
        op.create_check_constraint(op.f(f"ck_submissions_{suffix}"), "submissions", condition)

    # Copy each result's group ordinal and points, then make the copy the key.
    op.add_column(RESULTS, sa.Column("group_ordinal", sa.Integer(), nullable=True))
    op.add_column(RESULTS, sa.Column("points", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE submission_group_results r SET group_ordinal = g.ordinal, points = g.points "
        "FROM test_groups g WHERE g.id = r.group_id"
    )
    op.alter_column(RESULTS, "group_ordinal", nullable=False)
    op.alter_column(RESULTS, "points", nullable=False)
    op.drop_constraint(PK, RESULTS, type_="primary")
    op.drop_constraint(FK_GROUP, RESULTS, type_="foreignkey")
    op.alter_column(RESULTS, "group_id", nullable=True)
    op.create_primary_key(PK, RESULTS, ["submission_id", "group_ordinal"])
    op.create_foreign_key(
        FK_GROUP, RESULTS, "test_groups", ["group_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index(IX_GROUP, RESULTS, ["group_id"], unique=False)
    for suffix, condition in RESULT_CHECKS:
        op.create_check_constraint(op.f(f"ck_{RESULTS}_{suffix}"), RESULTS, condition)


def downgrade() -> None:
    _abort_if(DOWNGRADE_CHECKS, "downgrade")

    for suffix, _ in RESULT_CHECKS:
        op.drop_constraint(op.f(f"ck_{RESULTS}_{suffix}"), RESULTS, type_="check")
    op.drop_index(IX_GROUP, table_name=RESULTS)
    op.drop_constraint(FK_GROUP, RESULTS, type_="foreignkey")
    op.drop_constraint(PK, RESULTS, type_="primary")
    op.alter_column(RESULTS, "group_id", nullable=False)
    op.create_primary_key(PK, RESULTS, ["submission_id", "group_id"])
    op.create_foreign_key(
        FK_GROUP, RESULTS, "test_groups", ["group_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_column(RESULTS, "points")
    op.drop_column(RESULTS, "group_ordinal")

    for suffix, _ in SUBMISSION_CHECKS:
        op.drop_constraint(op.f(f"ck_submissions_{suffix}"), "submissions", type_="check")
    op.drop_column("submissions", "compile_message")
    op.drop_column("submissions", "lease_expires_at")
    op.drop_column("submissions", "judge_attempts")
    op.drop_column("submissions", "tests_hash")

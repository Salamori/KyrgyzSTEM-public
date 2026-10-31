"""Data migrations, run against a scratch database on the disposable test server.

Each test creates its own database next to TEST_DATABASE_URL's (same server), migrates it
with Alembic and drops it afterwards, so the suite's shared schema is never touched.
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import API_DIR, TEST_DATABASE_URL


@pytest.fixture
def scratch_engine() -> Iterator[Engine]:
    base = make_url(TEST_DATABASE_URL)
    name = f"{base.database}_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(base.set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _migrate(engine: Engine, direction: str, revision: str) -> None:
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "alembic"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        getattr(command, direction)(cfg, revision)


def _version(engine: Engine) -> str:
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def _checks(engine: Engine) -> set[str]:
    sql = (
        "SELECT conname FROM pg_constraint WHERE conrelid = 'problems'::regclass "
        "AND conname LIKE 'ck_problems_source%'"
    )
    with engine.connect() as conn:
        return set(conn.execute(text(sql)).scalars())


def _insert_problem(
    conn: Any, slug: str, origin: str, source_json: str | None, **extra: Any
) -> None:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "slug": slug,
        "status": "in_review",
        "published_at": None,
        "published_by": None,
        "rights_verified_by": None,
        "rights_verified_at": None,
        "origin": origin,
        "license": "CC-BY-4.0",
        "source": source_json,
        **extra,
    }
    conn.execute(
        text(
            "INSERT INTO problems (id, slug, status, difficulty_band, difficulty_status, "
            "difficulty_rationale, time_limit_ms, memory_limit_mb, scoring, origin, license, "
            "attribution_md, authors_md, generated_with_ai, source, rights_verified_by, "
            "rights_verified_at, published_at, published_by, created_at, updated_at) VALUES "
            "(:id, :slug, :status, 'easy', 'reviewed', 'Synthetic rationale.', 1000, 256, "
            "'ioi', :origin, :license, 'Synthetic attribution.', 'Test suite', true, "
            "CAST(:source AS jsonb), :rights_verified_by, :rights_verified_at, :published_at, "
            ":published_by, now(), now())"
        ),
        values,
    )


def _snapshot(engine: Engine) -> dict[str, dict[str, Any]]:
    """Every problem column except source, plus translations: what 0007 must not change."""
    with engine.connect() as conn:
        problems = conn.execute(text("SELECT * FROM problems ORDER BY slug")).mappings().all()
        translations = (
            conn.execute(text("SELECT * FROM problem_translations ORDER BY problem_id, locale"))
            .mappings()
            .all()
        )
    return {
        "problems": {p["slug"]: {k: v for k, v in p.items() if k != "source"} for p in problems},
        "translations": {f"{t['problem_id']}:{t['locale']}": dict(t) for t in translations},
    }


def _sources(engine: Engine) -> dict[str, tuple[bool, str | None]]:
    sql = "SELECT slug, source IS NULL, jsonb_typeof(source) FROM problems ORDER BY slug"
    with engine.connect() as conn:
        return {slug: (is_null, kind) for slug, is_null, kind in conn.execute(text(sql))}


def test_0007_converts_json_null_and_keeps_everything_else(scratch_engine: Engine) -> None:
    _migrate(scratch_engine, "upgrade", "0006")
    reviewer = uuid.uuid4()
    with scratch_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name, role, "
                "preferred_locale, is_active, is_synthetic, created_at, updated_at) VALUES "
                "(:id, 'reviewer@example.com', 'x', '[Synthetic] Reviewer', 'admin', 'en', "
                "true, true, now(), now())"
            ),
            {"id": reviewer},
        )
        # The state 0006 + the old importer produced: "no source" stored as JSON null.
        _insert_problem(
            conn, "original-published", "original", "null",
            status="published", published_at="2026-10-01T00:00:00Z", published_by=reviewer,
            rights_verified_by=reviewer, rights_verified_at="2026-09-30T00:00:00Z",
        )  # fmt: skip
        _insert_problem(conn, "original-draft", "original", "null")
        _insert_problem(conn, "original-sql-null", "original", None)
        _insert_problem(
            conn, "adapted", "adapted",
            '{"title": "t", "author": "a", "url": "https://example.com", "license": "CC0-1.0"}',
        )  # fmt: skip
        conn.execute(
            text(
                "INSERT INTO problem_translations (problem_id, locale, title, legend_md, "
                "input_md, output_md, notes_md, reviewed_by, reviewed_at) SELECT id, 'en', "
                "'Title', 'Legend', 'In', 'Out', NULL, :reviewer, '2026-09-29T00:00:00Z' "
                "FROM problems WHERE slug = 'original-published'"
            ),
            {"reviewer": reviewer},
        )
    before = _snapshot(scratch_engine)
    assert _checks(scratch_engine) == {"ck_problems_source_for_reuse"}

    _migrate(scratch_engine, "upgrade", "0007")
    assert _version(scratch_engine) == "0007"
    assert _checks(scratch_engine) == {"ck_problems_source_shape"}
    assert _sources(scratch_engine) == {
        "adapted": (False, "object"),
        "original-draft": (True, None),
        "original-published": (True, None),
        "original-sql-null": (True, None),
    }
    assert _snapshot(scratch_engine) == before  # status, rights, reviews, timestamps intact

    _migrate(scratch_engine, "downgrade", "0006")
    assert _version(scratch_engine) == "0006"
    assert _checks(scratch_engine) == {"ck_problems_source_for_reuse"}
    assert _snapshot(scratch_engine) == before

    _migrate(scratch_engine, "upgrade", "head")
    assert _checks(scratch_engine) == {"ck_problems_source_shape"}
    assert _snapshot(scratch_engine) == before
    assert _sources(scratch_engine)["adapted"] == (False, "object")


@pytest.mark.parametrize(
    ("slug", "origin", "source_json"),
    [
        ("reused-json-null", "adapted", "null"),
        ("reused-string", "licensed", '"not an object"'),
        (
            "original-with-source",
            "original",
            '{"title": "t", "author": "a", "url": "https://example.com", "license": "CC0-1.0"}',
        ),
    ],
)
def test_0007_aborts_without_changing_anything_on_invalid_provenance(
    scratch_engine: Engine, slug: str, origin: str, source_json: str
) -> None:
    _migrate(scratch_engine, "upgrade", "0006")
    with scratch_engine.begin() as conn:
        _insert_problem(conn, slug, origin, source_json)
    before = _sources(scratch_engine)

    with pytest.raises(RuntimeError, match=f"Migration 0007 aborted.*{slug}"):
        _migrate(scratch_engine, "upgrade", "0007")

    assert _version(scratch_engine) == "0006"
    assert _checks(scratch_engine) == {"ck_problems_source_for_reuse"}
    assert _sources(scratch_engine) == before


def _hint_columns(engine: Engine) -> set[str]:
    sql = "SELECT column_name FROM information_schema.columns WHERE table_name = 'hints'"
    with engine.connect() as conn:
        return set(conn.execute(text(sql)).scalars())


def _hint_rows(engine: Engine) -> list[tuple[Any, ...]]:
    sql = "SELECT id, problem_id, locale, ordinal, body_md FROM hints ORDER BY locale, ordinal"
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


def test_0008_adds_hint_review_columns_and_downgrade_keeps_hints(scratch_engine: Engine) -> None:
    _migrate(scratch_engine, "upgrade", "0007")
    with scratch_engine.begin() as conn:
        _insert_problem(conn, "with-hints", "original", None)
        for locale, ordinal, body in (
            ("en", 1, "First."),
            ("en", 2, "Second."),
            ("ru", 1, "Один."),
        ):
            conn.execute(
                text(
                    "INSERT INTO hints (id, problem_id, locale, ordinal, body_md) "
                    "SELECT :id, id, :locale, :ordinal, :body FROM problems "
                    "WHERE slug = 'with-hints'"
                ),
                {"id": uuid.uuid4(), "locale": locale, "ordinal": ordinal, "body": body},
            )
    rows = _hint_rows(scratch_engine)
    assert len(rows) == 3 and not {"reviewed_by", "reviewed_at"} & _hint_columns(scratch_engine)

    _migrate(scratch_engine, "upgrade", "0008")
    assert _version(scratch_engine) == "0008"
    assert {"reviewed_by", "reviewed_at"} <= _hint_columns(scratch_engine)
    assert _hint_rows(scratch_engine) == rows  # rows and text intact
    with scratch_engine.connect() as conn:
        unreviewed = conn.execute(
            text("SELECT count(*) FROM hints WHERE reviewed_by IS NULL AND reviewed_at IS NULL")
        ).scalar_one()
    assert unreviewed == 3  # existing hints start unreviewed
    with scratch_engine.begin() as conn:
        conn.execute(text("UPDATE hints SET reviewed_at = now() WHERE ordinal = 1"))

    _migrate(scratch_engine, "downgrade", "0007")
    assert _version(scratch_engine) == "0007"
    assert not {"reviewed_by", "reviewed_at"} & _hint_columns(scratch_engine)
    assert _hint_rows(scratch_engine) == rows  # only the review metadata is lost

    _migrate(scratch_engine, "upgrade", "head")
    assert {"reviewed_by", "reviewed_at"} <= _hint_columns(scratch_engine)
    assert _hint_rows(scratch_engine) == rows


# ------------------------------------------------------------------ 0009 judging fields


def _insert_user(conn: Any) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, password_hash, display_name, role, "
            "preferred_locale, is_active, is_synthetic, created_at, updated_at) VALUES "
            "(:id, :email, 'x', '[Synthetic] Student', 'student', 'ru', true, true, now(), now())"
        ),
        {"id": user_id, "email": f"{user_id.hex[:12]}@example.com"},
    )
    return user_id


def _insert_groups(conn: Any, slug: str, points: dict[int, int]) -> dict[int, uuid.UUID]:
    ids = {}
    for ordinal, pts in points.items():
        ids[ordinal] = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO test_groups (id, problem_id, ordinal, points, description_md) "
                "SELECT :id, id, :ordinal, :points, 'Synthetic.' FROM problems WHERE slug = :slug"
            ),
            {"id": ids[ordinal], "ordinal": ordinal, "points": pts, "slug": slug},
        )
    return ids


def _insert_submission(
    conn: Any, user_id: uuid.UUID, slug: str, results: list[tuple[uuid.UUID, str, int]]
) -> uuid.UUID:
    submission_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO submissions (id, user_id, problem_id, context, language, source_code, "
            "state, verdict, score, hints_revealed_count, created_at, judged_at) "
            "SELECT :id, :user_id, id, 'practice', 'python', 'print(1)', 'done', 'partial', "
            ":score, 0, now(), now() FROM problems WHERE slug = :slug"
        ),
        {
            "id": submission_id,
            "user_id": user_id,
            "slug": slug,
            "score": sum(score for _, _, score in results),
        },
    )
    for group_id, verdict, score in results:
        conn.execute(
            text(
                "INSERT INTO submission_group_results (submission_id, group_id, verdict, score) "
                "VALUES (:submission_id, :group_id, :verdict, :score)"
            ),
            {
                "submission_id": submission_id,
                "group_id": group_id,
                "verdict": verdict,
                "score": score,
            },
        )
    return submission_id


def _results(engine: Engine) -> list[tuple[Any, ...]]:
    sql = (
        "SELECT submission_id, group_id, group_ordinal, points, verdict, score "
        "FROM submission_group_results ORDER BY submission_id, group_ordinal"
    )
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


def _columns(engine: Engine, table: str) -> set[str]:
    sql = "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
    with engine.connect() as conn:
        return set(conn.execute(text(sql), {"t": table}).scalars())


def _key_and_delete_rule(engine: Engine) -> tuple[list[str], str]:
    with engine.connect() as conn:
        key = list(
            conn.execute(
                text(
                    "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                    "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = 'submission_group_results'::regclass AND i.indisprimary "
                    "ORDER BY array_position(i.indkey, a.attnum)"
                )
            ).scalars()
        )
        rule = conn.execute(
            text(
                "SELECT delete_rule FROM information_schema.referential_constraints "
                "WHERE constraint_name = 'fk_submission_group_results_group_id_test_groups'"
            )
        ).scalar_one()
    return key, rule


JUDGING_COLUMNS = {"tests_hash", "judge_attempts", "lease_expires_at", "compile_message"}


def _seed_0008(engine: Engine) -> tuple[uuid.UUID, dict[int, uuid.UUID]]:
    _migrate(engine, "upgrade", "0008")
    with engine.begin() as conn:
        _insert_problem(conn, "judged", "original", None)
        groups = _insert_groups(conn, "judged", {0: 0, 1: 40, 2: 60})
        user_id = _insert_user(conn)
        submission_id = _insert_submission(
            conn,
            user_id,
            "judged",
            [
                (groups[0], "accepted", 0),
                (groups[1], "accepted", 40),
                (groups[2], "wrong_answer", 0),
            ],
        )
    return submission_id, groups


def test_0009_copies_group_ordinal_and_points_and_keeps_results_after_groups_go(
    scratch_engine: Engine,
) -> None:
    submission_id, groups = _seed_0008(scratch_engine)
    _migrate(scratch_engine, "upgrade", "0009")
    assert _version(scratch_engine) == "0009"
    assert JUDGING_COLUMNS <= _columns(scratch_engine, "submissions")
    assert _results(scratch_engine) == [
        (submission_id, groups[0], 0, 0, "accepted", 0),
        (submission_id, groups[1], 1, 40, "accepted", 40),
        (submission_id, groups[2], 2, 60, "wrong_answer", 0),
    ]
    assert _key_and_delete_rule(scratch_engine) == (["submission_id", "group_ordinal"], "SET NULL")
    with scratch_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT tests_hash, judge_attempts, lease_expires_at, compile_message "
                "FROM submissions"
            )
        ).one()
    assert tuple(row) == (None, 0, None, None)  # no invented test-set hash

    # A re-import replaces the test groups: the results stay, with their ordinal and points.
    with scratch_engine.begin() as conn:
        conn.execute(text("DELETE FROM test_groups"))
    assert _results(scratch_engine) == [
        (submission_id, None, 0, 0, "accepted", 0),
        (submission_id, None, 1, 40, "accepted", 40),
        (submission_id, None, 2, 60, "wrong_answer", 0),
    ]

    # The 0008 schema cannot hold a result without its group: downgrade refuses, unchanged.
    before = _results(scratch_engine)
    with pytest.raises(RuntimeError, match="group_id is NULL"):
        _migrate(scratch_engine, "downgrade", "0008")
    assert _version(scratch_engine) == "0009"
    assert _results(scratch_engine) == before
    assert JUDGING_COLUMNS <= _columns(scratch_engine, "submissions")


def test_0009_downgrade_round_trips_when_nothing_would_be_lost(scratch_engine: Engine) -> None:
    submission_id, groups = _seed_0008(scratch_engine)
    _migrate(scratch_engine, "upgrade", "0009")
    _migrate(scratch_engine, "downgrade", "0008")
    assert _version(scratch_engine) == "0008"
    assert not JUDGING_COLUMNS & _columns(scratch_engine, "submissions")
    assert not {"group_ordinal", "points"} & _columns(scratch_engine, "submission_group_results")
    assert _key_and_delete_rule(scratch_engine) == (["submission_id", "group_id"], "CASCADE")
    with scratch_engine.connect() as conn:
        kept = conn.execute(
            text("SELECT group_id, score FROM submission_group_results ORDER BY score, group_id")
        ).all()
    assert sorted(kept) == sorted([(groups[0], 0), (groups[1], 40), (groups[2], 0)])
    _migrate(scratch_engine, "upgrade", "head")
    assert [r[2:] for r in _results(scratch_engine)] == [
        (0, 0, "accepted", 0),
        (1, 40, "accepted", 40),
        (2, 60, "wrong_answer", 0),
    ]
    assert submission_id in {r[0] for r in _results(scratch_engine)}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE submissions SET judge_attempts = 1",
        "UPDATE submissions SET tests_hash = repeat('a', 64)",
        "UPDATE submissions SET state = 'judging', verdict = NULL, score = NULL, "
        "lease_expires_at = now()",
        "UPDATE submissions SET verdict = 'compile_error', score = 0, compile_message = 'error: x'",
    ],
)
def test_0009_downgrade_refuses_to_drop_judging_metadata(
    scratch_engine: Engine, statement: str
) -> None:
    _seed_0008(scratch_engine)
    _migrate(scratch_engine, "upgrade", "0009")
    with scratch_engine.begin() as conn:
        conn.execute(text(statement))
    before = _results(scratch_engine)
    with pytest.raises(RuntimeError, match="judging metadata"):
        _migrate(scratch_engine, "downgrade", "0008")
    assert _version(scratch_engine) == "0009"
    assert _results(scratch_engine) == before


@pytest.mark.parametrize("defect", ["other_problem", "score_over_points"])
def test_0009_aborts_without_changing_anything_on_inconsistent_results(
    scratch_engine: Engine, defect: str
) -> None:
    _migrate(scratch_engine, "upgrade", "0008")
    with scratch_engine.begin() as conn:
        _insert_problem(conn, "mine", "original", None)
        _insert_problem(conn, "other", "original", None)
        mine = _insert_groups(conn, "mine", {1: 100})
        other = _insert_groups(conn, "other", {1: 100})
        user_id = _insert_user(conn)
        if defect == "other_problem":
            _insert_submission(conn, user_id, "mine", [(other[1], "accepted", 100)])
        else:
            conn.execute(text("UPDATE test_groups SET points = 30 WHERE id = :id"), {"id": mine[1]})
            _insert_submission(conn, user_id, "mine", [(mine[1], "accepted", 100)])
    with scratch_engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM submission_group_results")).all()
    with pytest.raises(RuntimeError, match="Migration 0009 upgrade aborted"):
        _migrate(scratch_engine, "upgrade", "0009")
    assert _version(scratch_engine) == "0008"
    assert not JUDGING_COLUMNS & _columns(scratch_engine, "submissions")
    assert not {"group_ordinal", "points"} & _columns(scratch_engine, "submission_group_results")
    with scratch_engine.connect() as conn:
        assert conn.execute(text("SELECT * FROM submission_group_results")).all() == rows

"""The v2 judging snapshot (Step 5.3.1): synthetic data only, no database, no code execution."""

import dataclasses
import hashlib
import json
from typing import Any

import pytest

from app.modules.judge import snapshot as snap
from app.modules.judge.core import GroupSpec
from app.modules.judge.snapshot import (
    CHECKER_ID,
    JudgingSnapshot,
    SnapshotError,
    SnapshotGroup,
    SnapshotTest,
)
from app.modules.olympiad.models import Scoring

V1 = "ab" * 32


def make(**changes: Any) -> JudgingSnapshot:
    base = JudgingSnapshot(
        tests_hash_v1=V1,
        scoring=Scoring.ioi,
        time_limit_ms=1000,
        memory_limit_mb=256,
        groups=(SnapshotGroup(0, 0), SnapshotGroup(1, 30), SnapshotGroup(2, 70)),
        tests=(
            SnapshotTest(0, 1, True),
            SnapshotTest(1, 1, False),
            SnapshotTest(1, 2, False),
            SnapshotTest(2, 1, False),
        ),
    )
    return dataclasses.replace(base, **changes)


# ------------------------------------------------------------------ the hash


def test_hash_is_the_sha256_of_the_canonical_v2_document() -> None:
    document = snap.snapshot_document(make())
    assert document == {
        "version": "ks-judge-snapshot/2",
        "tests_hash_v1": V1,
        "scoring": "ioi",
        "time_limit_ms": 1000,
        "memory_limit_mb": 256,
        "checker": "tokens-v1",
        "groups": [[0, 0], [1, 30], [2, 70]],
        "tests": [[0, 1, True], [1, 1, False], [1, 2, False], [2, 1, False]],
    }
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert snap.snapshot_hash(make()) == hashlib.sha256(text.encode()).hexdigest()


def test_hash_is_deterministic_and_independent_of_row_order() -> None:
    shuffled = make(groups=tuple(reversed(make().groups)), tests=tuple(reversed(make().tests)))
    assert snap.snapshot_hash(make()) == snap.snapshot_hash(make()) == snap.snapshot_hash(shuffled)


def test_v2_never_equals_the_v1_hash_it_builds_on() -> None:
    value = snap.snapshot_hash(make())
    assert len(value) == 64 and value != V1 and value == value.lower()


@pytest.mark.parametrize(
    "changes",
    [
        {"tests_hash_v1": "cd" * 32},  # different test inputs or outputs
        {"scoring": Scoring.binary},
        {"time_limit_ms": 2000},
        {"memory_limit_mb": 512},
        {"groups": (SnapshotGroup(0, 0), SnapshotGroup(1, 40), SnapshotGroup(2, 60))},  # points
        {  # a test moved to another group
            "tests": (
                SnapshotTest(0, 1, True),
                SnapshotTest(1, 1, False),
                SnapshotTest(2, 1, False),
                SnapshotTest(2, 2, False),
            )
        },
        {  # a test added
            "tests": (
                SnapshotTest(0, 1, True),
                SnapshotTest(0, 2, True),
                SnapshotTest(1, 1, False),
                SnapshotTest(1, 2, False),
                SnapshotTest(2, 1, False),
            )
        },
    ],
)
def test_every_judging_relevant_field_changes_the_hash(changes: dict[str, Any]) -> None:
    assert snap.snapshot_hash(make(**changes)) != snap.snapshot_hash(make())


def test_checker_id_is_part_of_the_document() -> None:
    assert make().checker == CHECKER_ID
    assert snap.snapshot_document(make())["checker"] == CHECKER_ID


# ------------------------------------------------------------------ fail closed


@pytest.mark.parametrize(
    "changes",
    [
        {"tests_hash_v1": ""},
        {"tests_hash_v1": "h" * 64},  # not hex
        {"tests_hash_v1": "AB" * 32},  # not lowercase
        {"tests_hash_v1": "ab" * 31},  # too short
        {"tests_hash_v1": None},
        {"checker": "exact-v1"},  # an unknown checker never hashes silently
        {"scoring": "icpc"},
        {"time_limit_ms": 0},
        {"memory_limit_mb": -1},
        {"groups": ()},
        {"groups": (SnapshotGroup(0, 0), SnapshotGroup(1, 30), SnapshotGroup(1, 70))},
        {"groups": (SnapshotGroup(0, 0), SnapshotGroup(1, 30), SnapshotGroup(2, 101))},
        {"groups": (SnapshotGroup(-1, 0), SnapshotGroup(1, 30), SnapshotGroup(2, 70))},
        {"tests": (SnapshotTest(0, 1, True), SnapshotTest(1, 1, False))},  # group 2 empty
        {"tests": (*make().tests, SnapshotTest(3, 1, False))},  # unknown group
        {"tests": (*make().tests, SnapshotTest(1, 1, False))},  # duplicate test
        {"tests": (*make().tests, SnapshotTest(1, 0, False))},  # ordinal below 1
        {"tests": (SnapshotTest(0, 1, False), *make().tests[1:])},  # a sample not flagged
        {"tests": (*make().tests[:3], SnapshotTest(2, 1, True))},  # a scored test flagged
    ],
)
def test_invalid_snapshots_never_get_a_hash(changes: dict[str, Any]) -> None:
    bad = make(**changes)
    with pytest.raises(SnapshotError):
        snap.snapshot_hash(bad)
    assert not snap.matches(snap.snapshot_hash(make()), bad)


# ------------------------------------------------------------------ matching


def test_matches_only_the_same_snapshot() -> None:
    stored = snap.snapshot_hash(make())
    assert snap.matches(stored, make())
    assert not snap.matches(stored, make(time_limit_ms=1500))


@pytest.mark.parametrize("stored", [None, "", V1])
def test_null_or_foreign_stored_hash_is_unverifiable(stored: str | None) -> None:
    # NULL (historical or unbindable) and a v1 value are never accepted as a v2 match.
    assert not snap.matches(stored, make())


def test_missing_snapshot_is_unverifiable() -> None:
    assert not snap.matches(snap.snapshot_hash(make()), None)


# ------------------------------------------------------------------ judged == hashed


def test_group_specs_come_from_the_hashed_snapshot() -> None:
    outputs = {(0, 1): "s", (1, 1): "a", (1, 2): "b", (2, 1): "c"}
    assert snap.group_specs(make(), outputs) == [
        GroupSpec(0, 0, ("s",)),
        GroupSpec(1, 30, ("a", "b")),
        GroupSpec(2, 70, ("c",)),
    ]


def test_group_specs_order_tests_by_ordinal() -> None:
    shuffled = make(tests=tuple(reversed(make().tests)))
    outputs = {(0, 1): "s", (1, 1): "a", (1, 2): "b", (2, 1): "c"}
    assert snap.group_specs(shuffled, outputs)[1] == GroupSpec(1, 30, ("a", "b"))


@pytest.mark.parametrize(
    "outputs",
    [
        {(0, 1): "s", (1, 1): "a", (2, 1): "c"},  # a test's output missing
        {(0, 1): "s", (1, 1): "a", (1, 2): "b", (2, 1): "c", (2, 2): "d"},  # an extra one
    ],
)
def test_group_specs_require_exactly_the_snapshot_tests(
    outputs: dict[tuple[int, int], str],
) -> None:
    with pytest.raises(SnapshotError):
        snap.group_specs(make(), outputs)


def test_group_specs_refuse_an_invalid_snapshot() -> None:
    with pytest.raises(SnapshotError):
        snap.group_specs(make(tests_hash_v1=""), {})

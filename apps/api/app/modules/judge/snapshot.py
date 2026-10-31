"""The judging snapshot: which exact test set and judging rules a submission is bound to.

`submissions.tests_hash` stores only the v2 snapshot hash computed here, never the problem's
own `problems.tests_hash` (v1). v1 stays what it is: the importer's hash of the test inputs
and outputs in generation order, used by the publication gate and reference-solution
verification. v1 cannot be recomputed from database rows (they keep no generation order), so
v2 builds on it instead of replacing it.

The v2 document covers everything the judge uses to reach a verdict:

* the problem's v1 hash, standing for the test inputs and expected outputs;
* the scoring mode, the time and memory limits and the checker id;
* every group as (ordinal, points) and every test as (group ordinal, ordinal, sample flag).

It is versioned by its tag, so a v2 value is never mistaken for, or compared with, a v1 value.

Trust assumption: v1 stands for the test data only because the importer writes the test rows
and `problems.tests_hash` in one transaction, under the problem row lock. A direct database
edit of test inputs or outputs that leaves v1 unchanged is outside this integrity guarantee.

Pure and deterministic: no database session, network or code execution. Invalid or
inconsistent snapshots raise `SnapshotError`; nothing invalid gets a hash (fail closed).
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.modules.judge.core import SAMPLE_GROUP, GroupSpec
from app.modules.olympiad.models import Scoring

SNAPSHOT_VERSION = "ks-judge-snapshot/2"
# The output comparison the judge uses: token-wise (`core.check_output` / `content.same_output`).
# Changing the checker means a new id, so earlier snapshots no longer match.
CHECKER_ID = "tokens-v1"
_HEX64 = re.compile(r"[0-9a-f]{64}")


class SnapshotError(ValueError):
    """A snapshot that cannot be hashed or used for judging (fail closed)."""


@dataclass(frozen=True)
class SnapshotGroup:
    ordinal: int
    points: int


@dataclass(frozen=True)
class SnapshotTest:
    __test__ = False  # not a pytest class

    group_ordinal: int
    ordinal: int
    is_sample: bool


@dataclass(frozen=True)
class JudgingSnapshot:
    """The judging-relevant state of one problem, as read in one database snapshot."""

    tests_hash_v1: str
    scoring: Scoring
    time_limit_ms: int
    memory_limit_mb: int
    groups: tuple[SnapshotGroup, ...]
    tests: tuple[SnapshotTest, ...]
    checker: str = CHECKER_ID


def _validate(s: JudgingSnapshot) -> None:
    if not isinstance(s.tests_hash_v1, str) or not _HEX64.fullmatch(s.tests_hash_v1):
        raise SnapshotError("the problem's test-set hash is missing or malformed")
    if s.checker != CHECKER_ID:
        raise SnapshotError(f"unknown checker {s.checker!r}")
    try:
        Scoring(s.scoring)
    except ValueError:
        raise SnapshotError(f"unknown scoring mode {s.scoring!r}") from None
    if s.time_limit_ms <= 0 or s.memory_limit_mb <= 0:
        raise SnapshotError("limits must be positive")
    ordinals = [g.ordinal for g in s.groups]
    if not ordinals or len(set(ordinals)) != len(ordinals):
        raise SnapshotError("groups are missing or duplicated")
    if any(g.ordinal < 0 or not 0 <= g.points <= 100 for g in s.groups):
        raise SnapshotError("invalid group ordinal or points")
    keys = [(t.group_ordinal, t.ordinal) for t in s.tests]
    if len(set(keys)) != len(keys):
        raise SnapshotError("duplicate tests")
    if any(t.ordinal < 1 for t in s.tests):
        raise SnapshotError("invalid test ordinal")
    if {t.group_ordinal for t in s.tests} != set(ordinals):
        raise SnapshotError("every group needs tests, and every test a known group")
    # The judge treats group 0 as the samples, so the stored flag must say the same.
    if any(t.is_sample != (t.group_ordinal == SAMPLE_GROUP) for t in s.tests):
        raise SnapshotError("sample flags disagree with the sample group")


def snapshot_document(s: JudgingSnapshot) -> dict[str, object]:
    """The canonical v2 document (groups and tests sorted; order of input is irrelevant)."""
    _validate(s)
    return {
        "version": SNAPSHOT_VERSION,
        "tests_hash_v1": s.tests_hash_v1,
        "scoring": Scoring(s.scoring).value,
        "time_limit_ms": s.time_limit_ms,
        "memory_limit_mb": s.memory_limit_mb,
        "checker": s.checker,
        "groups": sorted([g.ordinal, g.points] for g in s.groups),
        "tests": sorted([t.group_ordinal, t.ordinal, t.is_sample] for t in s.tests),
    }


def snapshot_hash(s: JudgingSnapshot) -> str:
    """The v2 hash stored in `submissions.tests_hash`."""
    text = json.dumps(
        snapshot_document(s), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def matches(stored: str | None, s: JudgingSnapshot | None) -> bool:
    """Whether a submission's stored v2 hash identifies this snapshot. A NULL stored hash,
    a missing snapshot or an invalid one never matches: the submission is unverifiable."""
    if stored is None or s is None:
        return False
    try:
        current = snapshot_hash(s)
    except SnapshotError:
        return False
    return hmac.compare_digest(stored, current)


def group_specs(
    s: JudgingSnapshot, expected_outputs: Mapping[tuple[int, int], str]
) -> list[GroupSpec]:
    """The judging core's groups, built from the snapshot itself, so the points and test
    structure that are judged are exactly the ones that were hashed. `expected_outputs` maps
    (group ordinal, test ordinal) to the expected output, and must cover exactly the
    snapshot's tests."""
    _validate(s)
    if set(expected_outputs) != {(t.group_ordinal, t.ordinal) for t in s.tests}:
        raise SnapshotError("expected outputs do not match the snapshot's tests")
    specs = []
    for group in sorted(s.groups, key=lambda g: g.ordinal):
        ordinals = sorted(t.ordinal for t in s.tests if t.group_ordinal == group.ordinal)
        outputs: Sequence[str] = tuple(expected_outputs[(group.ordinal, o)] for o in ordinals)
        specs.append(GroupSpec(group.ordinal, group.points, tuple(outputs)))
    return specs

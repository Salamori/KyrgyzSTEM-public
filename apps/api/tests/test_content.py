"""Checks every problem in content/: metadata, samples, brute-force cross-checks, generated
tests against the stated constraints, Python speed, and (when a compiler exists) C++.

These tests execute repository content code (reviewed like any other code), never
student submissions.
"""

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.modules.olympiad.content import (
    ContentError,
    Curriculum,
    ProblemContent,
    TestSpec,
    build_tests,
    find_cycle,
    load_curriculum,
    load_problems,
    parse_statement,
    same_output,
    solver,
    verify_problem,
)

CONTENT = get_settings().content_dir
PROBLEMS = load_problems(CONTENT / "problems")
IDS = [p.slug for p in PROBLEMS]


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(CONTENT / "curriculum.toml")


def test_curriculum_covers_required_topics(curriculum: Curriculum) -> None:
    required = {
        "implementation", "complexity", "greedy", "binary-search", "sorting", "prefix-sums",
        "two-pointers", "graphs", "trees", "dynamic-programming", "combinatorics",
        "number-theory", "data-structures",
    }  # fmt: skip
    assert required <= {t.slug for t in curriculum.topics}
    assert all({"en", "ru", "ky"} <= t.names.keys() for t in curriculum.topics)


def test_find_cycle_detects_cycles() -> None:
    assert find_cycle([("a", "b"), ("b", "c")]) is None
    cycle = find_cycle([("a", "b"), ("b", "c"), ("c", "a")])
    assert cycle is not None and cycle[0] == cycle[-1]


def test_curriculum_rejects_cycles(tmp_path: Path) -> None:
    bad = tmp_path / "c.toml"
    bad.write_text(
        """
[[topics]]
slug = "a"
stages = 1
names = { en = "A" }
[[topics]]
slug = "b"
stages = 1
names = { en = "B" }
[[prerequisites]]
topic = "a"
stage = 1
requires = "b"
requires_stage = 1
strength = "required"
[[prerequisites]]
topic = "b"
stage = 1
requires = "a"
requires_stage = 1
strength = "helpful"
""",
        encoding="utf-8",
    )
    with pytest.raises(ContentError, match="cycle"):
        load_curriculum(bad)


def test_statement_parser() -> None:
    st = parse_statement("# T\nLegend\n## Input\nIn\n## Output\nOut\n")
    assert (st.title, st.legend, st.input, st.output, st.notes) == (
        "T",
        "Legend",
        "In",
        "Out",
        None,
    )
    with pytest.raises(ContentError):
        parse_statement("No title\n## Input\nx\n## Output\ny")
    with pytest.raises(ContentError):
        parse_statement("# T\nLegend\n## Input\nonly input")


def test_only_the_synthetic_sample_ships_publicly() -> None:
    assert [p.slug for p in PROBLEMS] == ["sample-sum"]


@pytest.mark.parametrize("problem", PROBLEMS, ids=IDS)
def test_metadata(problem: ProblemContent, curriculum: Curriculum) -> None:
    for ref in problem.topics:
        topic = curriculum.topic(ref.slug)
        assert topic is not None, ref.slug
        assert 1 <= ref.stage <= topic.stages
    known_tags = {t.slug for t in curriculum.tags}
    assert set(problem.tags) <= known_tags
    # Bilingual from the start; Kyrgyz statements are added only after native review.
    assert {"en", "ru"} <= problem.statements.keys()
    assert problem.editorials.keys() >= problem.statements.keys()
    assert {"en", "ru"} <= problem.hints.keys() and len(problem.hints["en"]) >= 2
    if problem.generated_with_ai:
        assert "review" in problem.attribution.lower()
    for name in ("solution.py", "brute.py", "solution.cpp", "gen.py"):
        assert (problem.directory / name).exists(), name


@pytest.mark.parametrize("problem", PROBLEMS, ids=IDS)
def test_samples_and_random_cross_check(problem: ProblemContent) -> None:
    result = verify_problem(problem, rounds=300)
    assert result.samples_checked >= 1


@pytest.fixture(scope="module")
def built() -> dict[str, list[TestSpec]]:
    # Generation validates every test against the problem's constraints.
    return {p.slug: build_tests(p) for p in PROBLEMS}


@pytest.mark.parametrize("problem", PROBLEMS, ids=IDS)
def test_python_reference_is_fast_enough(
    problem: ProblemContent, built: dict[str, list[TestSpec]]
) -> None:
    solve = solver(problem, "solution")
    biggest = max(built[problem.slug], key=lambda t: len(t.input))
    start = time.perf_counter()
    solve(biggest.input)
    # Generous bound: catches accidental O(n^2), not a performance guarantee.
    assert time.perf_counter() - start < 5


def _cxx() -> list[str] | None:
    if command := os.environ.get("KS_CXX"):
        return shlex.split(command)
    found = shutil.which("g++") or shutil.which("clang++")
    return [found] if found else None


@pytest.mark.parametrize("problem", PROBLEMS, ids=IDS)
def test_cpp_reference_matches(
    problem: ProblemContent,
    built: dict[str, list[TestSpec]],
    tmp_path: Path,
) -> None:
    cxx = _cxx()
    if cxx is None:
        pytest.skip("no C++ compiler (set KS_CXX or install g++)")
    binary = tmp_path / "solution.exe"
    compile_cmd = [
        *cxx,
        "-std=c++17",
        "-O2",
        "-o",
        str(binary),
        str(problem.directory / "solution.cpp"),
    ]
    subprocess.run(compile_cmd, check=True, capture_output=True)  # noqa: S603
    for test in built[problem.slug]:
        run = subprocess.run(  # noqa: S603
            [str(binary)], input=test.input, capture_output=True, text=True, check=True, timeout=10
        )
        assert same_output(run.stdout, test.output), f"group {test.group} test {test.ordinal}"

"""Load and verify repository content (content/curriculum.toml, content/problems/*).

Content directories contain Python helpers (solution.py, brute.py, gen.py) written by
contributors and reviewed like any other code. They are executed only by this loader
(import script and tests), never in the request path, and never for student submissions.
"""

import hashlib
import importlib.util
import random
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

BANDS = ("intro", "easy", "medium", "hard", "challenge")
ORIGINS = ("original", "licensed", "adapted")
# Licenses that allow redistribution of statements. Extend only after legal review.
ALLOWED_LICENSES = ("CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0")
# The licenses an adaptation may carry, given its source's license (attribution and
# share-alike terms survive adaptation; CC0 imposes nothing).
ADAPTATION_LICENSES = {
    "CC0-1.0": ALLOWED_LICENSES,
    "CC-BY-4.0": ("CC-BY-4.0", "CC-BY-SA-4.0"),
    "CC-BY-SA-4.0": ("CC-BY-SA-4.0",),
}
SOURCE_FIELDS = ("title", "author", "url", "license")
LOCALES = ("ky", "ru", "en")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_TEST_BYTES = 4 << 20  # per test input; see docs/CONTENT_GUIDE.md


class ContentError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContentError(message)


# ---------------------------------------------------------------- curriculum


@dataclass(frozen=True)
class TopicSpec:
    slug: str
    stages: int
    names: dict[str, str]
    summaries: dict[str, str]


@dataclass(frozen=True)
class PrerequisiteSpec:
    topic: str
    stage: int
    requires: str
    requires_stage: int
    strength: str


@dataclass(frozen=True)
class TagSpec:
    slug: str
    names: dict[str, str]


@dataclass(frozen=True)
class Curriculum:
    topics: list[TopicSpec]
    prerequisites: list[PrerequisiteSpec]
    tags: list[TagSpec]

    def topic(self, slug: str) -> TopicSpec | None:
        return next((t for t in self.topics if t.slug == slug), None)


def find_cycle(edges: list[tuple[str, str]]) -> list[str] | None:
    """Return one cycle in a directed graph given as (from, to) edges, or None."""
    graph: dict[str, list[str]] = {}
    for a, b in edges:
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, [])
    state: dict[str, int] = dict.fromkeys(graph, 0)  # 0 new, 1 on stack, 2 done
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph[node]:
            if state[nxt] == 1:
                return [*stack[stack.index(nxt) :], nxt]
            if state[nxt] == 0 and (cycle := visit(nxt)):
                return cycle
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if state[node] == 0 and (cycle := visit(node)):
            return cycle
    return None


def load_curriculum(path: Path) -> Curriculum:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    topics = [
        TopicSpec(
            slug=t["slug"],
            stages=int(t["stages"]),
            names=dict(t["names"]),
            summaries=dict(t.get("summary", {})),
        )
        for t in raw.get("topics", [])
    ]
    by_slug = {t.slug: t for t in topics}
    _require(len(by_slug) == len(topics), "duplicate topic slug")
    for t in topics:
        _require(bool(SLUG_RE.match(t.slug)), f"bad topic slug {t.slug!r}")
        _require(1 <= t.stages <= 4, f"{t.slug}: stages must be 1-4")
        _require("en" in t.names, f"{t.slug}: English name required")

    prereqs = [
        PrerequisiteSpec(
            topic=p["topic"],
            stage=int(p["stage"]),
            requires=p["requires"],
            requires_stage=int(p["requires_stage"]),
            strength=p["strength"],
        )
        for p in raw.get("prerequisites", [])
    ]
    for p in prereqs:
        for slug, stage in ((p.topic, p.stage), (p.requires, p.requires_stage)):
            _require(slug in by_slug, f"prerequisite references unknown topic {slug!r}")
            _require(1 <= stage <= by_slug[slug].stages, f"{slug}: stage {stage} out of range")
        _require(p.strength in ("required", "helpful"), f"bad strength {p.strength!r}")

    # Nodes are topic stages; stage s implicitly depends on stage s-1 of the same topic.
    edges = [(f"{p.topic}:{p.stage}", f"{p.requires}:{p.requires_stage}") for p in prereqs]
    edges += [
        (f"{t.slug}:{s}", f"{t.slug}:{s - 1}") for t in topics for s in range(2, t.stages + 1)
    ]
    cycle = find_cycle(edges)
    _require(cycle is None, f"prerequisite cycle: {' -> '.join(cycle or [])}")

    tags = [TagSpec(slug=t["slug"], names=dict(t["names"])) for t in raw.get("tags", [])]
    _require(len({t.slug for t in tags}) == len(tags), "duplicate tag slug")
    return Curriculum(topics=topics, prerequisites=prereqs, tags=tags)


# ---------------------------------------------------------------- problems


@dataclass(frozen=True)
class Statement:
    title: str
    legend: str
    input: str
    output: str
    notes: str | None


@dataclass(frozen=True)
class TopicRef:
    slug: str
    stage: int
    primary: bool


@dataclass(frozen=True)
class GroupSpec:
    ordinal: int  # 0 = samples (0 points), 1.. = scored groups
    points: int
    description: str


@dataclass(frozen=True)
class TestSpec:
    group: int
    ordinal: int
    input: str
    output: str
    is_sample: bool


@dataclass(frozen=True)
class Source:
    """The work a licensed or adapted problem comes from."""

    title: str
    author: str
    url: str
    license: str


@dataclass
class ProblemContent:
    directory: Path
    slug: str
    band: str
    band_rationale: str
    time_limit_ms: int
    memory_limit_mb: int
    scoring: str
    origin: str
    license: str
    authors: str
    attribution: str
    generated_with_ai: bool
    source: Source | None
    topics: list[TopicRef]
    tags: list[str]
    groups: list[GroupSpec]
    hints: dict[str, list[str]]
    statements: dict[str, Statement]
    editorials: dict[str, str]
    samples: list[tuple[str, str]] = field(default_factory=list)


def parse_statement(text: str) -> Statement:
    """`# Title`, legend, then 2-3 `## ` sections in order: input, output, [notes]."""
    lines = text.strip().splitlines()
    _require(bool(lines) and lines[0].startswith("# "), "statement must start with '# Title'")
    title = lines[0][2:].strip()
    parts = re.split(r"^## .*$", "\n".join(lines[1:]), flags=re.MULTILINE)
    _require(len(parts) in (3, 4), "statement needs '## Input', '## Output' and optional notes")
    legend, input_md, output_md, *rest = (p.strip() for p in parts)
    _require(bool(title and legend and input_md and output_md), "empty statement section")
    return Statement(title, legend, input_md, output_md, rest[0] if rest else None)


def _load_source(slug: str, meta: dict[str, object]) -> Source | None:
    return check_source(slug, str(meta["origin"]), str(meta["license"]), meta.get("source"))


def check_source(slug: str, origin: str, license: str, raw: object) -> Source | None:
    """The provenance rules for a problem's source, shared by the loader and the
    publication gate: an original problem has none; a licensed or adapted one needs every
    source field, an http(s) URL, an allow-listed source license and a compatible license.
    Raises ContentError naming the first violation."""
    if origin == "original":
        _require(raw is None, f"{slug}: an original problem has no [source]")
        return None
    _require(isinstance(raw, dict), f"{slug}: origin {origin!r} requires a [source] table")
    table = cast(dict[str, object], raw)
    _require(set(table) == set(SOURCE_FIELDS), f"{slug}: [source] needs exactly {SOURCE_FIELDS}")
    values = {k: str(table[k]).strip() for k in SOURCE_FIELDS}
    _require(all(values.values()), f"{slug}: [source] fields must not be empty")
    _require(
        values["url"].startswith(("https://", "http://")), f"{slug}: source url must be http(s)"
    )
    source = Source(**values)
    _require(
        source.license in ALLOWED_LICENSES,
        f"{slug}: source license not in allow-list (needs a maintainer's legal check)",
    )
    if origin == "licensed":
        _require(
            license == source.license, f"{slug}: a licensed problem keeps its source's license"
        )
    else:
        _require(
            license in ADAPTATION_LICENSES[source.license],
            f"{slug}: license {license} is not allowed for an adaptation of "
            f"{source.license} material",
        )
    return source


def load_problem(directory: Path) -> ProblemContent:
    meta = tomllib.loads((directory / "problem.toml").read_text(encoding="utf-8"))
    slug = meta["slug"]
    _require(slug == directory.name, f"slug {slug!r} must match directory name")
    _require(bool(SLUG_RE.match(slug)), f"bad slug {slug!r}")
    _require(meta["band"] in BANDS, f"{slug}: band must be one of {BANDS}")
    _require(meta["origin"] in ORIGINS, f"{slug}: origin must be one of {ORIGINS}")
    _require(meta["license"] in ALLOWED_LICENSES, f"{slug}: license not in allow-list")
    for key in ("band_rationale", "authors", "attribution"):
        _require(bool(str(meta.get(key, "")).strip()), f"{slug}: {key} is required")
    _require(meta.get("scoring") in ("ioi", "binary"), f"{slug}: scoring must be ioi|binary")
    source = _load_source(slug, meta)

    topics = [TopicRef(t["slug"], int(t["stage"]), bool(t.get("primary"))) for t in meta["topics"]]
    _require(sum(t.primary for t in topics) == 1, f"{slug}: exactly one primary topic")

    groups = [GroupSpec(0, 0, "Examples")] + [
        GroupSpec(i, int(g["points"]), g["description"])
        for i, g in enumerate(meta["groups"], start=1)
    ]
    _require(sum(g.points for g in groups) == 100, f"{slug}: group points must sum to 100")

    statements: dict[str, Statement] = {}
    editorials: dict[str, str] = {}
    for locale in LOCALES:
        st = directory / f"statement.{locale}.md"
        if st.exists():
            statements[locale] = parse_statement(st.read_text(encoding="utf-8"))
        ed = directory / f"editorial.{locale}.md"
        if ed.exists():
            editorials[locale] = ed.read_text(encoding="utf-8").strip()
    _require(bool(statements), f"{slug}: at least one statement")

    hints = {k: list(v) for k, v in meta.get("hints", {}).items()}
    _require(all(k in LOCALES for k in hints), f"{slug}: unknown hint locale")
    _require(len({len(v) for v in hints.values()}) <= 1, f"{slug}: hint counts differ by locale")

    samples = []
    for inp in sorted((directory / "samples").glob("*.in")):
        out = inp.with_suffix(".out")
        _require(out.exists(), f"{slug}: missing {out.name}")
        samples.append((inp.read_text(encoding="utf-8"), out.read_text(encoding="utf-8")))
    _require(bool(samples), f"{slug}: at least one sample")

    return ProblemContent(
        directory=directory,
        slug=slug,
        band=meta["band"],
        band_rationale=meta["band_rationale"],
        time_limit_ms=int(meta["time_limit_ms"]),
        memory_limit_mb=int(meta["memory_limit_mb"]),
        scoring=meta["scoring"],
        origin=meta["origin"],
        license=meta["license"],
        authors=meta["authors"],
        attribution=meta["attribution"],
        generated_with_ai=bool(meta["generated_with_ai"]),
        source=source,
        topics=topics,
        tags=list(meta.get("tags", [])),
        groups=groups,
        hints=hints,
        statements=statements,
        editorials=editorials,
        samples=samples,
    )


def load_problems(root: Path) -> list[ProblemContent]:
    return [load_problem(d) for d in sorted(root.iterdir()) if (d / "problem.toml").exists()]


# ---------------------------------------------------------------- execution helpers


class Solver(Protocol):
    def __call__(self, data: str) -> str: ...


def load_module(path: Path) -> ModuleType:
    name = f"ks_content_{path.parent.name.replace('-', '_')}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ContentError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def solver(problem: ProblemContent, name: str) -> Solver:
    return cast(Solver, load_module(problem.directory / f"{name}.py").solve)


def same_output(a: str, b: str) -> bool:
    """Token-wise comparison, the checker every problem here uses."""
    return a.split() == b.split()


def build_tests(problem: ProblemContent) -> list[TestSpec]:
    """Samples (group 0) plus generated tests with outputs from the reference solution."""
    gen = load_module(problem.directory / "gen.py")
    solve = solver(problem, "solution")
    validate = cast(Callable[[str, int], None], gen.validate)
    result = [
        TestSpec(0, i, inp, out, is_sample=True)
        for i, (inp, out) in enumerate(problem.samples, start=1)
    ]
    counters: dict[int, int] = {}
    valid_groups = {g.ordinal for g in problem.groups if g.ordinal > 0}
    for group, inp in cast(Callable[[], list[tuple[int, str]]], gen.tests)():
        _require(group in valid_groups, f"{problem.slug}: generator used unknown group {group}")
        validate(inp, group)
        _require(len(inp.encode()) <= MAX_TEST_BYTES, f"{problem.slug}: test input exceeds 4 MiB")
        counters[group] = counters.get(group, 0) + 1
        result.append(TestSpec(group, counters[group], inp, solve(inp), is_sample=False))
    _require(all(counters.get(g, 0) > 0 for g in valid_groups), f"{problem.slug}: empty group")
    return result


def tests_hash(tests: list[TestSpec]) -> str:
    digest = hashlib.sha256()
    for t in tests:
        for part in (str(t.group), str(t.ordinal), t.input, t.output):
            digest.update(part.encode())
            digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class Verification:
    samples_checked: int
    random_cases: int


def verify_problem(problem: ProblemContent, rounds: int = 300, seed: int = 7) -> Verification:
    """Samples must match both solutions; random small inputs must agree between them."""
    gen = load_module(problem.directory / "gen.py")
    main, brute = solver(problem, "solution"), solver(problem, "brute")
    validate = cast(Callable[[str, int], None], gen.validate)
    for inp, expected in problem.samples:
        validate(inp, 0)
        _require(same_output(main(inp), expected), f"{problem.slug}: solution fails a sample")
        _require(same_output(brute(inp), expected), f"{problem.slug}: brute fails a sample")
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    random_small = cast(Callable[[random.Random], str], gen.random_small)
    for _ in range(rounds):
        inp = random_small(rng)
        validate(inp, 0)
        got, want = main(inp), brute(inp)
        _require(same_output(got, want), f"{problem.slug}: mismatch on\n{inp}\n{got!r} vs {want!r}")
    return Verification(samples_checked=len(problem.samples), random_cases=rounds)

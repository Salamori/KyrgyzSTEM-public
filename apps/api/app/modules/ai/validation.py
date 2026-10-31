"""Output validation: structure, citations, solution leakage, correctness claims.

Heuristics, deliberately conservative: when in doubt the answer is withheld and the student
gets authored hints instead. Evaluation (docs/proposals/0004 §5) measures how often that
happens.
"""

import json
import re
from dataclasses import dataclass, field

from app.modules.ai.retrieval import Source

MAX_GROUNDED = 6
MAX_POINT_CHARS = 600
MAX_SUGGESTION_CHARS = 1500
OVERLAP_SHINGLE = 8
OVERLAP_THRESHOLD = 0.25

_REAL_CODE = re.compile(
    r"#include|\bint\s+main\s*\(|\bdef\s+\w+\s*\(|^\s*import\s+\w|\bstd::|\bcout\s*<<|\bcin\s*>>"
    r"|\bprintf\s*\(|\bscanf\s*\(|\bpublic\s+static\b|\bSystem\.out\b|\bprint\s*\(|```[a-z+]+",
    re.MULTILINE | re.IGNORECASE,
)
_CODE_LINE = re.compile(
    r"[;{}]\s*$|^\s*(for|while|if)\s*\(.*\)\s*\{?\s*$|\w+\s*(\+=|-=|==|=)\s*[\w(\[]"
)
_FENCE = re.compile(r"```")
_CORRECTNESS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(your|the)\s+(code|solution|program)\b[^.!?\n]{0,40}\b(is|looks)\s+(correct|right|fine)",
        r"\bwill\s+(pass|be\s+accepted|get\s+accepted)\b",
        r"\b(code|solution|program)\s+(passes|is\s+accepted)\b",
        r"(код|решение|программа)[^.!?\n]{0,40}(правильн|верн|корректн)",
        r"(пройд[её]т|будет\s+принят)",
        r"(код|чечим|программа)[^.!?\n]{0,40}(туура|кабыл\s+алынат)",
    )
]
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Validated:
    grounded: list[tuple[str, list[str]]] = field(default_factory=list)
    suggestion: str = ""
    insufficient_sources: bool = False
    flags: list[str] = field(default_factory=list)
    withheld: str | None = None  # reason when the whole answer must not be shown


def looks_like_code(text: str, level: int) -> bool:
    """Real-language code is never allowed below level 4; level 3 allows pseudocode."""
    if _REAL_CODE.search(text):
        return True
    if level >= 3:
        return False
    if _FENCE.search(text):
        return True
    return sum(1 for line in text.splitlines() if _CODE_LINE.search(line)) >= 3


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[i : i + OVERLAP_SHINGLE]) for i in range(len(words) - OVERLAP_SHINGLE + 1)}


def editorial_overlap(text: str, editorial: str) -> float:
    answer = _shingles(text)
    if not answer:
        return 0.0
    return len(answer & _shingles(editorial)) / len(answer)


def strip_correctness_claims(text: str) -> tuple[str, bool]:
    kept, removed = [], False
    for sentence in _SENTENCE.split(text):
        if any(p.search(sentence) for p in _CORRECTNESS):
            removed = True
        else:
            kept.append(sentence)
    return " ".join(kept).strip(), removed


def validate(raw: str, level: int, sources: list[Source], editorial: str | None) -> Validated:
    out = Validated()
    try:
        data = json.loads(raw)
        points = data["grounded"]
        suggestion = data["suggestion"]
        insufficient = data["insufficient_sources"]
        if not (
            isinstance(points, list)
            and isinstance(suggestion, str)
            and isinstance(insufficient, bool)
        ):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        out.withheld = "invalid_output"
        return out

    known = {s.id for s in sources}
    demoted: list[str] = []
    for item in points[:MAX_GROUNDED]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        ids = [i for i in item.get("source_ids", []) if isinstance(i, str) and i in known]
        if ids:
            out.grounded.append((item["text"][:MAX_POINT_CHARS], ids))
        else:
            demoted.append(item["text"])
            out.flags.append("unsupported_citation")
    if len(points) > MAX_GROUNDED:
        out.flags.append("truncated")

    suggestion = " ".join([suggestion, *demoted]).strip()
    if len(suggestion) > MAX_SUGGESTION_CHARS:
        suggestion = suggestion[:MAX_SUGGESTION_CHARS]
        out.flags.append("truncated")
    out.suggestion = suggestion
    out.insufficient_sources = insufficient

    everything = "\n".join([*(t for t, _ in out.grounded), out.suggestion])
    if level < 4 and looks_like_code(everything, level):
        out.withheld = "code_withheld"
        return out
    if level < 4 and editorial and editorial_overlap(everything, editorial) >= OVERLAP_THRESHOLD:
        out.withheld = "editorial_overlap"
        return out

    cleaned_points = []
    for text, ids in out.grounded:
        cleaned, removed = strip_correctness_claims(text)
        if removed:
            out.flags.append("correctness_claim_removed")
        if cleaned:
            cleaned_points.append((cleaned, ids))
    out.grounded = cleaned_points
    out.suggestion, removed = strip_correctness_claims(out.suggestion)
    if removed:
        out.flags.append("correctness_claim_removed")
    out.flags = sorted(set(out.flags))
    return out

"""Run the assistant evaluation set (proposal 0004 §5).

Usage (from apps/api):
  uv run python -m scripts.run_ai_eval --provider fake         # harness check only
  uv run python -m scripts.run_ai_eval --provider configured   # real model (costs money)

The fake provider returns a fixed answer: it checks that the harness runs and says nothing
about model quality. Answers are written to evals/assistant/runs/ (git-ignored) for human
review with RUBRIC.md; automated metrics are printed.
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.modules.ai import prompting, retrieval, validation
from app.modules.ai.provider import (
    AssistantModel,
    ModelRequest,
    ModelResult,
    ProviderError,
    get_assistant_model,
)
from app.modules.olympiad import content

EVAL_DIR = Path(__file__).resolve().parents[1] / "evals" / "assistant"


class HarnessModel:
    """Deterministic stand-in that exercises the pipeline. Not a model."""

    provider = "harness"
    model_id = "harness-fixed-answer"

    def generate(self, request: ModelRequest) -> ModelResult:
        answer = {
            "grounded": [{"text": "Read the statement carefully.", "source_ids": ["S1"]}],
            "suggestion": "Try a small example by hand first.",
            "insufficient_sources": False,
        }
        return ModelResult(json.dumps(answer), self.model_id, 0, 0)  # no tokens, no cost


def run_case(case: dict[str, Any], model: AssistantModel) -> dict[str, Any]:
    problem = content.load_problem(get_settings().content_dir / "problems" / case["problem"])
    reliable = {x.strip() for x in get_settings().llm_reliable_locales.split(",")}
    answered = case["locale"] if case["locale"] in reliable else "ru"
    if case.get("inject_statement"):
        statements = {
            loc: replace(st, legend=f"{st.legend}\n\n{case['inject_statement']}")
            for loc, st in problem.statements.items()
        }
        problem = replace(problem, statements=statements)
    sources, editorial = retrieval.sources_from_content(
        problem, answered, include_editorial=bool(case.get("include_editorial"))
    )
    code = tuple(case["student_code"]) if case.get("student_code") else None
    system, user = prompting.build(case["level"], answered, sources, case.get("question"), code)

    started = time.monotonic()
    raw, error, usage = "", None, (0, 0)
    try:
        result = model.generate(ModelRequest(system, user, prompting.OUTPUT_SCHEMA))
        raw, usage = result.text, (result.input_tokens, result.output_tokens)
    except ProviderError as exc:
        error = exc.kind
    latency_ms = int((time.monotonic() - started) * 1000)

    checked = validation.validate(raw, case["level"], sources, editorial) if raw else None
    canary = case.get("canary")
    s = get_settings()
    return {
        "id": case["id"],
        "problem": case["problem"],
        "level": case["level"],
        "locale_requested": case["locale"],
        "locale_answered": answered,
        "locale_ok": answered == case.get("expected_locale", case["locale"]),
        "provider": model.provider,
        "model_id": model.model_id,
        "prompt_version": prompting.PROMPT_VERSION,
        "error": error,
        "withheld": checked.withheld if checked else None,
        "flags": checked.flags if checked else [],
        "canary_leak": bool(canary and canary in raw),
        "raw_has_code": validation.looks_like_code(raw, case["level"]) if raw else False,
        "latency_ms": latency_ms,
        "input_tokens": usage[0],
        "output_tokens": usage[1],
        "cost_usd": (usage[0] * s.llm_price_input_per_mtok + usage[1] * s.llm_price_output_per_mtok)
        / 1e6,
        "answer": raw,
        "case_reviewed": bool(case.get("human_reviewed")),
        "review": {
            "factual": None,
            "reveal": None,
            "algorithm": None,
            "language": None,
            "safety": None,
            "reviewer": None,
            "notes": "",
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = sorted(r["latency_ms"] for r in rows)
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else 0
    return {
        "cases": len(rows),
        "answered": sum(1 for r in rows if not r["error"] and not r["withheld"]),
        "errors": sum(1 for r in rows if r["error"]),
        "withheld": sum(1 for r in rows if r["withheld"]),
        "canary_leaks": sum(1 for r in rows if r["canary_leak"]),
        "correctness_claims_removed": sum(
            1 for r in rows if "correctness_claim_removed" in r["flags"]
        ),
        "unsupported_citations": sum(1 for r in rows if "unsupported_citation" in r["flags"]),
        "locale_mismatches": sum(1 for r in rows if not r["locale_ok"]),
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "latency_p95_ms": p95,
        "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 6),
        "cases_human_reviewed": sum(1 for r in rows if r["case_reviewed"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["fake", "configured"], default="fake")
    parser.add_argument("--cases", type=Path, default=EVAL_DIR / "cases.json")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    model: AssistantModel | None = (
        HarnessModel() if args.provider == "fake" else get_assistant_model()
    )
    if model is None:
        print("No provider configured (LLM_PROVIDER / LLM_API_KEY).", file=sys.stderr)
        return 1
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    rows = [run_case(c, model) for c in cases]

    out = (
        args.out or EVAL_DIR / "runs" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{model.provider}.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    summary = summarize(rows)
    if args.provider == "fake":
        print("HARNESS CHECK ONLY: fixed answers, no information about model quality.")
    print(json.dumps(summary, indent=2))
    print(f"Answers for human review: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

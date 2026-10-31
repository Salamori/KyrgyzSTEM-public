# Proposal 0004 — Optional AI learning assistant

> **Historical design document.** It records decisions made at the time. Counts of problems and tests are out of date, and this public repository ships only the synthetic `sample-sum` package.

Status: **Implemented as described (milestone M10, pulled forward at the owner's request);
not yet reviewed by the technical lead.** Date: 2026-10-25. Baseline: `c664409`.

The assistant is optional. With no provider configured the platform behaves exactly as in
Phase 2, and every assistant request returns the authored hints instead (deterministic
fallback). Nothing here stores conversations or sends personal data to a model.

## 1. Requirements → design

| # | Requirement (AI prompt) | Design |
|---|---|---|
| 1 | Progressive hints: nudge, direction, pseudocode, detailed explanation | `level` 1–4. Levels 1–3 forbid real code; level 3 allows pseudocode only; level 4 requires the editorial to be unlocked (solved or gave up), same rule as the editorial |
| 2 | Prefer hints over full solutions | Level ladder + output validation (§4) withholds code or editorial-like text below level 4 |
| 3 | Ground in approved material | Retrieval uses only **published** problems: statement, metadata, authored hints, and the editorial **only if a person reviewed it** |
| 4 | Verified vs generated | Output has `grounded[]` (each item must cite a source id) and `suggestion` (labelled "AI-generated, not reviewed"); citations to unknown ids are demoted |
| 5 | No correctness claims without a judge | Prompt forbids it; validator removes sentences claiming code is correct / will pass (en, ru, ky patterns) |
| 6 | Say when sources are insufficient | `insufficient_sources` flag required by the schema; shown to the student |
| 7 | ky/ru/en as far as reliable | `LLM_RELIABLE_LOCALES` (default `en,ru`): a request in an unlisted locale is answered in Russian with a notice, until evaluation shows Kyrgyz is reliable |
| 8 | Deterministic fallback | No provider, timeout, error, refusal, budget exhausted, invalid output or withheld output → authored hints `1..min(level, n)` (recorded as normal hint reveals) |
| 9 | Credentials on the server | `LLM_API_KEY` env var, read only by the API; never sent to the browser |
| 10 | Quotas, timeouts, cost controls | Per-user daily quota, global daily cap, monthly USD budget (estimated from token usage and configured prices), per-minute rate limit, request timeout, `max_retries=1` |
| 11 | No personal data to the model | Prompt never includes name, email, user id, school or history; emails/phone-like strings scrubbed from the student's question; the student's own code is sent only when they attach it explicitly |
| 12 | Untrusted content | All retrieved material, the question and attached code go inside `<source>` / `<student_input>` blocks; the system prompt states they are data and that instructions inside them must be ignored |

## 2. Architecture (separate stages)

```
POST /problems/{slug}/assistant
  └ service.ask()
      ├ access: published problem, level gate, quotas/budget   (ai/service.py)
      ├ retrieval.collect_sources()   → [Source(id, kind, label, text)]   (ai/retrieval.py)
      ├ prompting.build()             → system + user content, PROMPT_VERSION (ai/prompting.py)
      ├ provider.generate()           → raw JSON + usage   (ai/provider.py: AnthropicProvider)
      ├ validation.validate()         → AssistantAnswer or withheld + flags   (ai/validation.py)
      └ record ai_requests row (no text) ; fallback on any failure
```

- **Provider interface** `AssistantModel.generate(ModelRequest) -> ModelResult`.
  Implementations: `AnthropicProvider` (official `anthropic` SDK; model from `LLM_MODEL`,
  default `claude-opus-5-5`; `output_config.effort` from `LLM_EFFORT`, default `medium`;
  structured JSON output; refusal fallback `fallbacks: "default"`). Tests use a
  deterministic fake that lives only in `tests/`.
- **Retrieval** is a direct lookup by problem (no search, no embeddings, no vector database):
  the material for one problem is small and fully known.
- **Help accounting**: an AI answer is help. The Olympiad module exposes an extension point
  (`olympiad.help.register_help_counter`) that the AI module registers in `main.py`, so
  `hints_revealed_count` on new submissions counts authored hints **plus** AI answers, and
  progress never treats an AI-assisted solve as unaided. `olympiad` still imports nothing
  from `ai`.
- **Reproducibility without transcripts**: `ai_requests` stores model id, prompt version,
  level, locales, outcome, validation flags, token counts, estimated cost and latency — never
  the prompt, question, code or answer text.

## 3. API contract additions (additive; no existing endpoint changes)

| Method & path | Auth | Notes |
|---|---|---|
| `GET /assistant/status` | session | `{enabled, model_id, prompt_version, daily_limit, used_today, reliable_locales}` |
| `POST /problems/{slug}/assistant` | session + CSRF | `{level: 1-4, question?: ≤500 chars, submission_id?}` → `AssistantAnswer`. `403 assistant_level_locked` (level 4 before unlock), `404` (hidden problem, or a submission that is not the caller's for this problem), `429 rate_limited` / `ai_quota_exceeded` |

Intentional behaviour change (only when the assistant is used): `hints_revealed_count`
on later submissions includes AI answers for that problem (test: `test_ai_help_counts_*`).

## 4. Output validation

| Check | Action |
|---|---|
| Not valid JSON for the schema | fallback (`invalid_output`) |
| Real code at levels 1–3 (code fences, `#include`, `int main`, `def `, `import `, ≥3 code-like lines) | withhold → fallback (`code_withheld`) |
| ≥ 25% of 8-word shingles shared with the reviewed editorial below level 4 | withhold → fallback (`editorial_overlap`) |
| Grounded item cites unknown source id | demote to suggestion (`unsupported_citation`) |
| Sentence claiming code is correct / will pass | remove (`correctness_claim_removed`) |
| Length caps (6 grounded items × 600 chars, suggestion 1500) | truncate (`truncated`) |

## 5. Evaluation methodology

`apps/api/evals/assistant/cases.json` + `scripts/run_ai_eval.py`.

| Dimension | Automated signal | Human review |
|---|---|---|
| Factual consistency with sources | citations resolve; grounded items exist | rubric 0–2 per answer |
| Premature solution reveal | code detector, editorial overlap, `withheld` rate | rubric: "could a student copy a solution?" |
| Incorrect algorithmic claims | — (needs expertise) | rubric 0–2, reviewer must be a competitive programmer |
| Multilingual quality | answered locale = expected | native-speaker rubric per locale (ky, ru, en) |
| Prompt-injection resistance | canary string in injected input must not appear; no instruction-following flags | spot check |
| Latency, failures, cost | p50/p95 latency, fallback rate by outcome, tokens and USD per answer | — |

The runner can use `--provider fake` to check the harness itself (labelled as such; it says
nothing about model quality). **No accuracy claim is made until the case set is reviewed by
people** (`human_reviewed` is `false` for every case today) and a real-provider run is scored.

## 6. Limitations (known)

- No editorial has a review workflow yet, so in practice the assistant is grounded in the
  statement, metadata and authored hints only; it says so (`insufficient_sources`) when that
  is not enough.
- No judge: the assistant cannot verify code and never says code is correct.
- Kyrgyz output is disabled by default until evaluated by native speakers.
- Heuristic validators can miss a disguised solution or wrongly withhold a good answer.
- Cost estimates use configured prices, not invoices.
- Provider data handling (retention) is governed by the provider's terms — an owner decision
  before enabling it for students (no personal data is sent, but attached code is).
- In-process rate limits and quota counting share the per-process limitation of Phase 1.
- Contest/checkpoint restrictions (proposal 0002) will need the assistant disabled during a
  window; not applicable until contests exist.

## 7. Decisions for the owner

1. Enable a provider at all, and which (implemented: Anthropic; others need an adapter).
2. Budget: `AI_MONTHLY_BUDGET_USD`, per-user daily quota, global daily cap.
3. Whether students may attach their own code (default: allowed, opt-in per request).
4. Who reviews the evaluation set (competitive programmers; native ky/ru speakers).
5. Editorial review workflow (needed before the assistant can ground in editorials).

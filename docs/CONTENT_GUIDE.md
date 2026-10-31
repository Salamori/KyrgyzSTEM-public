# Content Contribution Guide

How to add or change problems, topics and tags. Content lives in `content/` and goes
through pull-request review like code. Nothing reaches students until a reviewer who is
not the author publishes it through the admin API.

## Golden rules

1. **Original or licensed only.** Never copy statements, tests or solutions from
   contests, judges, books or websites unless the license allows it *and* you record the
   license and source. Re-telling a known contest problem in new words is still copying.
2. **Provenance is required.** Every problem declares `origin`, `license`, `authors` and
   `attribution`. The importer rejects anything else.
3. **No fake provenance.** Do not claim a problem appeared in an olympiad, attach an
   "official" rating, or name real people, schools or results.
4. **AI assistance is disclosed.** If any part (statement, tests, solution, editorial) was
   drafted with AI, set `generated_with_ai = true` and say so in `attribution`. It still
   needs full human review.
5. **Synthetic stories only.** Story characters are generic ("a student", "a club");
   no real names.

## Licensing

| What | Default license | Allowed alternatives |
|---|---|---|
| Statements, hints, editorials | CC BY 4.0, **only for original content whose rights have been verified** | CC BY-SA 4.0, CC0 1.0 |
| Code (solutions, generators) | MIT (repository license) | — |

CC BY 4.0 is never applied automatically. Choose the license deliberately; a reviewer then
confirms it (below). The license in `problem.toml` covers the whole problem: statements in
every language, hints and editorials. Material from a different source does not belong in
the same problem.

`origin = "licensed"` (reused as is) or `"adapted"` requires a `[source]` table, which the
loader checks:

```toml
[source]
title = "Name of the original work"
author = "Its author"
url = "https://where.it.was.published/"
license = "CC-BY-4.0"              # must be in the allow-list
```

* `licensed`: the problem keeps the source's license.
* `adapted`: CC0 sources allow any listed license; CC BY sources allow CC BY or CC BY-SA;
  CC BY-SA sources allow only CC BY-SA.
* `original`: no `[source]` table.

Licenses outside the allow-list in `apps/api/app/modules/olympiad/content.py` need a
maintainer's legal check first.

## Directory layout

```
content/
  curriculum.toml              topics, stages, prerequisites, tags
  problems/<slug>/
    problem.toml               metadata, subtasks, hints
    statement.en.md            statement per language (en and ru required for now)
    statement.ru.md
    editorial.en.md            one editorial per statement language
    editorial.ru.md
    samples/01.in, 01.out      hand-checked examples
    solution.py                reference solution: solve(input: str) -> str
    solution.cpp               reference solution in C++17, standard headers only
    brute.py                   independent slow solution: solve(input: str) -> str
    gen.py                     tests(), random_small(rng), validate(input, group)
```

`<slug>` is lowercase words joined by hyphens and must equal the directory name.

## `problem.toml`

```toml
slug = "my-problem"
band = "easy"                      # intro | easy | medium | hard | challenge
band_rationale = "One sentence: which idea(s) the solver needs and why that is this band."
time_limit_ms = 1000
memory_limit_mb = 256
scoring = "ioi"                    # ioi (subtasks) | binary

origin = "original"                # original | licensed | adapted (then add [source])
license = "..."                    # choose deliberately; see Licensing (not auto-applied)
authors = "Your name or 'KyrgyzSTEM contributors'"
attribution = "Original problem written for KyrgyzSTEM (2026)."
generated_with_ai = false

topics = [{ slug = "prefix-sums", stage = 1, primary = true }]   # exactly one primary
tags = ["overflow"]                # must exist in curriculum.toml

[[groups]]                         # subtasks; points must sum to 100
points = 30
description = "n ≤ 1000."
[[groups]]
points = 70
description = "Full constraints."

[hints]                            # same number per language, gentlest first
en = ["nudge", "key idea", "almost the solution"]
ru = ["…", "…", "…"]
```

Samples are added automatically as a 0-point group 0.

### Choosing a band

Bands describe the problem relative to **this curriculum**, not any external rating:

| Band | Meaning |
|---|---|
| Intro | One idea from a stage-1 topic, stated directly. |
| Easy | One stage-1/2 idea, but the story must be translated into it. |
| Medium | A stage-3 idea, or two stage-1/2 ideas combined. |
| Hard | A stage-4 idea or a non-obvious combination needing a proof. |
| Challenge | Beyond the curriculum's stated stages. |

The author's band is shown as *"Author's estimate"* until a different person confirms it
through the admin review (then *"Reviewed estimate"*). Every change is audited.

## Statement format

```markdown
# Title

Legend: the story and the task, in plain language. Define every symbol.

## Input
Exact format, line by line, with all constraints, e.g. `1 ≤ n ≤ 200 000`.

## Output
Exact format. Say what to print in every edge case (e.g. "print -1 if …").

## Notes
Optional: overflow warnings, clarifications. Never hints toward the solution.
```

Rules:

- Headings are matched by position: title, legend, then `##` Input, Output, optional Notes.
- Markdown only: no raw HTML, no images (they are stripped), no LaTeX yet. Write
  constraints with Unicode: `1 ≤ n ≤ 200 000`, `10^9`, `2·10^5`.
- Wrap identifiers and values in backticks: `` `a_i` ``.
- Every constraint used by `validate()` must appear in the statement, and vice versa.
- Mention when answers can exceed 32-bit integers.
- Translations must be faithful, not word-for-word; ask a native speaker to review.
  Kyrgyz statements are welcome only after review by a native speaker.

## Tests

`gen.py` must be deterministic (`random.Random(<fixed seed>)`) and provide:

- `tests() -> list[tuple[int, str]]` — `(group, input)` pairs. Outputs are computed from
  `solution.py`. Every scored group needs at least one test.
- `random_small(rng) -> str` — tiny random inputs for the brute-force cross-check.
- `validate(input, group) -> None` — asserts every constraint (group 0 = full
  constraints). The importer runs it on every sample and every generated test.

Coverage checklist (put a short comment next to each special case):

- [ ] minimum sizes (`n = 1`, empty-ish cases) and maximum sizes
- [ ] boundary values (0, max value, equal elements, already sorted, reversed)
- [ ] answers that overflow 32-bit integers, when possible
- [ ] cases that break plausible wrong ideas (wrong greedy, off-by-one, upper vs lower bound)
- [ ] worst cases for slow solutions in the full group (so O(n²) fails where intended)
- [ ] each test input ≤ 4 MiB (enforced); keep the whole problem small

## Solutions

- `solution.py` and `solution.cpp` must be correct on every test and pass the full
  group within the time limit. Read all input at once; avoid slow I/O.
- `brute.py` must be **independent**: a different, obviously-correct method (exhaustive
  search, direct simulation, a library function), not a copy of the reference.
- C++: C++17, standard headers only (no `<bits/stdc++.h>`), 64-bit integers where needed.

## Editorials

One per statement language, with these sections:

1. `## Idea` — the key observation and the algorithm.
2. `## Why it is correct` — a proof sketch (exchange argument, invariant, etc.).
3. `## Complexity` — and why simpler approaches only pass earlier subtasks.
4. `## Common mistakes` — concrete pitfalls students actually make.

Do not paste full solution code into the editorial.

## Checking your work

```bash
# from apps/api
uv run pytest tests/test_content.py          # metadata, samples, cross-check, speed, C++
# verifies and imports as in_review, linked to your account, so you cannot confirm rights
# for, review or publish it yourself:
uv run python -m scripts.import_content --author-email you@example.com my-problem
```

`test_content.py` runs every sample through both solutions, 300 random cross-checks
against `brute.py`, validates every generated test, times the Python solution on the
largest test, and (when `g++` or `KS_CXX` is available) checks the C++ solution on all
tests. CI runs it with `g++`.

## Review and publishing

Imported problems start as `in_review`. An admin who is **not the author** must:

1. confirm the rights: check the declared origin, license and `[source]`, and that we may
   publish the statements, hints and editorials under that license. AI-drafted text needs
   extra care, because rights in it are uncertain. The importer never sets this, and a
   re-import that changes origin, license, authors, attribution, the AI flag or the source
   clears it,
2. review **every** statement language that exists, every editorial that exists and every
   hint that exists (a problem with no hints or no editorial is fine: only what exists needs
   review),
3. review (confirm or change) the difficulty band with a written rationale. This also confirms
   the time and memory limits, scoring, topics (with stages) and tags. A re-import keeps the
   reviewed band, including a reviewer's correction, and its rationale while `band` in the file
   is still the band the review started from and those other fields are unchanged. Changing
   `band` in the file (even to the reviewer's band) or any of those fields resets the
   difficulty to the file's `band` and `band_rationale`, to be reviewed again,
4. publish. The API's publication gate refuses while any of these is missing and lists every
   missing item at once (the review panel shows them per language and hint): license,
   attribution and authors; a license and `[source]` that satisfy the rules above; confirmed
   rights; the reviews in step 2; a reference solution verified against the current tests; a
   reviewed difficulty; at least one sample; a publisher other than the linked author.

One independent reviewer is enough; the reviewer may also publish. A review counts only if it
was made through the audited review action by someone other than the linked author, and only
for the exact text that was reviewed (statements, editorials and hints: its SHA-256 is compared
with the audit entry). The rights confirmation counts only for the provenance it covered, and
the difficulty review only for the band it confirmed. If an account is linked as author after
it reviewed something (including the difficulty), that review stops counting. Reviews made
before these checks existed must be repeated. The rights confirmation and the "not an author" attestation are human
statements, not proof of rights or identity: the gate checks that they were made, not that
they are true. Unpublishing is never blocked by the gate.

Published problems cannot be re-imported; unpublish first (audited), so students never
see content change underneath them. Editing a statement clears that translation's review.

**Editorials** are reviewed per language in the same panel: the reviewer reads the full text
there (this never unlocks the editorial for their account or changes their progress) and
marks it reviewed. The same author rule applies, including the "not an author" attestation
when no author account is linked. Each review is audited with a SHA-256 hash of the exact
text. Re-importing keeps an editorial's review only if its text is unchanged. Students see
whether the editorial they opened has been reviewed. Every existing editorial must be reviewed
before publishing.

**Hints** are reviewed in the same panel, one collapsible section per language: the reviewer
reads every hint in order (this never reveals them on their account or changes progress), then
marks single hints or all hints of that language as reviewed. The same author rule and
attestation apply, and each hint gets its own audit entry with a SHA-256 hash of its text.
Because hints build on each other, re-importing keeps a language's hint reviews only if its
whole ordered list is unchanged: editing, adding, removing or reordering any hint clears every
review in that language (other languages keep theirs). Every existing hint must be reviewed
before publishing; students do not see hint review status.

**Who counts as the author.** The free-text `authors` field names no account, so the server
can only enforce "not the author" when the problem is linked to the author's account
(`created_by`). The import script therefore requires a choice:

* `--author-email EMAIL` — **required whenever the author is known** and has an account (an
  existing, active account; a different existing link is refused). The server then refuses
  that person for confirming rights, reviewing and publishing.
* `--author-unlinked` — only when no account can be linked (e.g. "KyrgyzSTEM contributors",
  or an external author). Confirming the rights then requires the reviewer to state "I am not
  an author or co-author" (`attest_not_author`). The audit entry records
  `author_identity: "unlinked"` and the attestation. This is a trusted admin's statement,
  **not proof of identity**: an admin who is in fact the author could attest falsely. This is
  why audit F-05 is mitigated, not closed.

Linking or changing the author later clears an earlier rights confirmation.

## Security note

`solution.py`, `brute.py` and `gen.py` are executed by the importer and the test suite.
They are code: review them as code. They are never uploaded through the web UI, and
student submissions are never executed by the API.

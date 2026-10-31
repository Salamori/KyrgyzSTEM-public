# Architecture

Status: Phase 2. Sections marked *(planned)* describe the agreed design for later
phases; they are contracts, not descriptions of shipped code.

## Overview

```
                ┌──────────────────────────────┐
 browser ──────▶│ apps/web  (Next.js, :3000)   │
                │  /api/* rewritten to the API │  same-origin → cookies just work
                └──────────────┬───────────────┘
                               │ HTTP (internal)
                ┌──────────────▼───────────────┐      ┌─────────────────────────┐
                │ apps/api  (FastAPI, :8000)   │─────▶│ Judge0 (optional,       │
                │  modules: auth, users,       │      │ isolated, planned P3)   │
                │  olympiad, orgs, ai, audit   │      └─────────────────────────┘
                └──────────────┬───────────────┘      ┌─────────────────────────┐
                               │ SQLAlchemy 2         │ LLM provider (optional, │
                ┌──────────────▼───────────────┐      │ planned P4)             │
                │ PostgreSQL 16                │      └─────────────────────────┘
                └──────────────────────────────┘
```

* **One API process, one database.** No microservices. Modules are Python packages
  with clear import rules, not separate deployables.
* **The web app never talks to the database** and never executes user code.
* **Submitted programs are never executed by the web or API process.** Judging is
  delegated to an external Judge0 instance through `JudgeClient` (planned P3). With no
  judge configured the platform still works: submissions are stored as
  `status = "not_judged"` and the UI says so.

## Repository layout

```
apps/
  api/                 FastAPI service (Python 3.12, uv)
    app/
      core/            config, db, security primitives, rate limiting, errors
      modules/
        auth/          registration, login, sessions, CSRF
        users/         profiles, admin user management
        audit/         append-only audit log
        olympiad/      problems, topics, hints, editorials, submissions, progress
                       (content.py loads content/, importer.py writes it, metrics.py)
        judge/         base.py: what the API sees (NoJudge, or QueueJudge for
                       JUDGE_LANGUAGES); queue.py, worker.py and runner.py: the queue,
                       one worker iteration and the persistent worker loop (entry point
                       scripts/judge_worker.py); judge0.py: the Judge0 CE executor, used
                       only by the worker and tested offline only
                       core.py: pure verdict/scoring/state rules (no I/O, not wired
                       into submissions yet); snapshot.py: the v2 judging snapshot hash
        teaching/      (P4) problem sets, learning paths, assignments
        ai/            optional assistant: provider, retrieval, prompting, validation
                       (proposal 0004); registers an olympiad help counter
        orgs/          organizations, memberships, groups (classes/clubs), join codes,
                       sessions and attendance, CSV export; permissions.py holds the
                       role → permission matrix (docs/SCHOOL_OPERATIONS.md)
      main.py          app factory, router registration
    alembic/           migrations (the only way the schema changes)
    scripts/           seed.py, export_openapi.py, import_content.py, purge_retention.py,
                       promote_admin.py
    tests/
  web/                 Next.js (App Router), TypeScript strict, Tailwind
    src/
      app/             routes
      components/      shared UI (state components: Loading, Empty, ErrorState…)
      lib/api/         generated OpenAPI types + typed fetch client
      lib/i18n/        ky / ru / en dictionaries
    e2e/               Playwright specs
content/               curriculum + problems (statements, tests, solutions)
docs/                  this folder
docker-compose.yml     postgres + api + web for local development (health checks)
deploy/                staging/production: compose file with Caddy, smoke test,
                       backup and restore scripts (docs/DEPLOYMENT.md)
.github/               CI and audit workflows, Dependabot, issue/PR templates
```

### Module boundaries

* `olympiad` must not import from `orgs`. School operations (Module C) may reference
  Olympiad entities (e.g. an event linked to a problem set) through IDs and the
  `olympiad` service layer, never the other way round.
* Every module exposes `router.py` (HTTP), `service.py` (business rules) and
  `models.py` (SQLAlchemy). Routers stay thin; authorization decisions live in
  dependencies (`require_user`, `require_role`) and services; organization routes build an
  `OrgContext` (`orgs/permissions.py`) from the caller's membership and check explicit
  permissions there.
* `core` imports nothing from `modules`.

## Key decisions (defaults chosen; revisit via ADR in `docs/` if needed)

| Decision | Choice | Why |
|---|---|---|
| Auth | Server-side sessions: opaque random token in an `HttpOnly; SameSite=Lax` cookie, SHA-256 hash stored in `auth_sessions`; Argon2id password hashing (`argon2-cffi`) | Revocable, no JWT pitfalls, no third-party identity provider to depend on. Students may not have Google accounts. |
| CSRF | Synchronizer token per session, sent in `X-CSRF-Token` on unsafe methods, plus `SameSite=Lax` | Defence in depth for cookie auth. |
| Web ↔ API | Next.js rewrites `/api/:path*` → API | Same origin in dev and prod; no CORS credentials. |
| API types | `scripts/export_openapi.py` → `apps/web/src/lib/api/openapi.json` → `openapi-typescript` → `schema.d.ts`; CI fails on drift | Single source of truth is the FastAPI/Pydantic schema. |
| i18n | Plain typed dictionaries (`ky`, `ru`, `en`), locale in a cookie | No routing changes; `en` is the key source of truth and TS enforces completeness. |
| Content i18n | `problem_translations` rows per locale with fallback ru → en → ky, flagged in the UI; publishable only when every existing translation is reviewed | Content quality over translation coverage. |
| Rate limiting | Sliding window keyed by IP + action; in-process by default, or in PostgreSQL (`RATE_LIMIT_BACKEND=postgres`, HMAC-hashed keys, per-key advisory lock) | In-process is simple but per-process: one API worker. PostgreSQL is shared by every instance (Vercel Functions, more workers). |
| IDs | UUIDv4 primary keys | Non-enumerable public IDs. |
| Time | `timestamptz` everywhere, UTC in the API | |
| Judge *(planned)* | Judge0 CE, separate compose profile, never on the public network | Battle-tested isolation; we don't build a sandbox. |
| LLM | `AssistantModel` protocol, Anthropic adapter (`claude-opus-5-5` default), env-configured; no provider → authored hints. Only metadata stored (`ai_requests`) | Optional, degrades gracefully, no transcripts. |

## Data model

Shipped in Phase 1 (`alembic/versions/0001_initial.py`):

| Table | Columns (abridged) | Notes |
|---|---|---|
| `users` | `id`, `email` (unique, lower-cased), `password_hash`, `display_name`, `role` (`student`/`teacher`/`admin`), `preferred_locale` (`ky`/`ru`/`en`), `is_active`, `is_synthetic`, `created_at`, `updated_at` | Minimal PII by design: no birth date, phone, address or school name on the user. `is_synthetic` marks seed data. |
| `auth_sessions` | `id`, `user_id` → users, `token_hash` (unique), `csrf_token_hash`, `created_at`, `expires_at`, `last_seen_at`, `revoked_at`, `user_agent` (truncated) | Raw tokens are never stored. |
| `audit_logs` | `id`, `actor_id` → users (nullable), `action`, `target_type`, `target_id`, `details` (JSON), `ip`, `created_at` | Append-only; no API to edit or delete. |

Planned (designed now so parallel work doesn't redesign it):

Shipped in Phase 2 (`alembic/versions/0002_olympiad.py`, design in
`docs/proposals/0001-olympiad-domain-model.md`):

| Table | Purpose |
|---|---|
| `topics`, `topic_translations` | Curriculum topics with `stage_count` (1–4) and localized names/summaries. |
| `topic_prerequisites` | Edges between topic *stages*, `required` or `helpful`; acyclic (checked on import). |
| `tags`, `tag_translations`, `problem_tags` | Technique labels; never gate anything. |
| `problems` | `status draft\|in_review\|published\|archived`, `difficulty_band` + `difficulty_status` (`author_estimate`\|`reviewed`) + rationale, limits, `scoring`, `origin`, SPDX `license`, `attribution_md`, `authors_md`, `generated_with_ai`, `tests_hash`, `published_at/by`. |
| `problem_translations` | Statement per locale (title, legend, input, output, notes) with `reviewed_by/at`. |
| `problem_topics` | Problem ↔ topic **stage**, one primary. |
| `test_groups`, `test_cases` | IOI subtasks; group 0 = samples (0 points). Test inputs ≤ 4 MiB, loaded lazily. |
| `hints`, `editorials` | Per locale; hints ordered gentlest first. |
| `reference_solutions` | Source per language; `verified_at` + `verified_tests_hash` (stale when tests change). |
| `submissions` | `context`, `language`, `source_code` (≤ 64 KiB), `state` (pipeline) **and** `verdict` (result), `score`, `hints_revealed_count` snapshot; judging metadata `tests_hash`, `judge_attempts`, `lease_expires_at`, `compile_message` (0009). |
| `submission_group_results` | Per-subtask verdicts (Phase 3 fills them), keyed by `(submission_id, group_ordinal)` with the group's `points` copied in; `group_id` is `SET NULL` when a re-import replaces the groups (0009). |
| `hint_reveals` | `(user, problem, ordinal)` — a reveal applies to every locale. |
| `editorial_unlocks` | `(user, problem, reason solved\|gave_up)`. |
| `self_reports` | Unverified "solved offline" claims, kept apart from evidence. |

Still planned:

* Phase 4: `problem_sets` (+ items, contest windows, `contest_participations`),
  `learning_paths` (+ steps), `assignments`, and `submissions.problem_set_id`.

### AI assistant (migration 0004)

`ai_requests(user_id, problem_id, level, locale_requested, locale_answered, mode ai|fallback,
outcome, provider, model_id, prompt_version, input_tokens, output_tokens, cost_usd,
latency_ms, flags)` — no prompt, question, code or answer text. Extension point:
`olympiad/help.py` lets `ai` add help counts without `olympiad` importing `ai`.

### School operations (migration 0005)

`organizations(name, kind, is_synthetic)`, `org_memberships(org_id, user_id, role
org_admin|teacher|student)`, `org_groups(org_id, name, kind class|club, lead_id,
join_code_hash, join_code_expires_at)`, `group_members(group_id, user_id, joined_at)`,
`group_sessions(group_id, starts_at, title)`, `attendance_records(session_id, user_id,
status present|absent|excused, marked_by, marked_at)`. All foreign keys cascade on delete.
Events, registrations and tutoring are not built yet.

### Content provenance (migration 0006)

`problems.source` (JSONB `{title, author, url, license}`, required by a CHECK unless
`origin = 'original'`), `problems.rights_verified_by` / `rights_verified_at` (set only by
an admin through `POST /admin/problems/{slug}/rights/verify`; publishing is blocked until
then; the importer clears it when provenance changes).

### Hint review (migration 0008)

`hints.reviewed_by` (users.id, `SET NULL`) and `hints.reviewed_at`, set only by an admin
through the audited hint-review endpoints. The importer keeps a locale's hint reviews only
when that locale's whole ordered hint list is unchanged. Downgrading 0008 drops the two
columns (hint rows and text are kept; review metadata is lost).

### Submission judging fields (migration 0009)

`submissions.tests_hash` (the v2 judging snapshot hash a submission is bound to, see below;
NULL for existing rows, never invented), `judge_attempts` (default 0), `lease_expires_at` (only
while `judging`) and `compile_message` (only with `compile_error`, ≤ 4096 characters).
`submission_group_results` keeps its own `group_ordinal` and `points` (key
`(submission_id, group_ordinal)`, `score <= points`), and its `group_id` link is now
`ON DELETE SET NULL`: a re-import, which recreates a problem's test groups, no longer
deletes earlier results, and the submission view reads the stored ordinal and points. The
upgrade aborts, changing nothing, on inconsistent results (a group of another problem, a
duplicate ordinal, a score above the points). The downgrade aborts, changing nothing, when
it would lose data: results whose group was deleted, stored values that differ from the
group, or any judging metadata. Nothing is wired to a judge yet.

### Judging snapshot hash (v2, no migration)

`problems.tests_hash` (v1) is unchanged: the importer's SHA-256 of the test inputs and
outputs in generation order, used by the publication gate and reference verification. It
cannot be recomputed from the rows (they keep no generation order). `submissions.tests_hash`
holds only the v2 hash from `judge/snapshot.py`: SHA-256 of a canonical, version-tagged
(`ks-judge-snapshot/2`) document of v1, the scoring mode, time and memory limits, the
checker id (`tokens-v1`), every group's (ordinal, points) and every test's (group ordinal,
ordinal, sample flag). The judge builds its groups from the same snapshot, so what is judged
is what was hashed. A NULL, foreign or mismatching stored hash never matches: such a
submission is unverifiable and is not judged. **Trust assumption:** v1 stands for the test
data only because the importer writes the test rows and v1 in one transaction under the
problem row lock; a direct database edit of test data that leaves v1 unchanged is outside
this integrity guarantee. Runtime and compiler versions are not in the hash (a runtime
profile is to be recorded with the judge integration).

### Judge queue (no migration)

`judge/queue.py` is the PostgreSQL queue, database operations only: it never runs, compiles
or judges code and produces no verdicts (the worker that calls it is the next step; with
`NoJudge` nothing is queued). `claim` takes the oldest `queued` submission with
`FOR UPDATE SKIP LOCKED`, sets `judging`, increments `judge_attempts` (the fencing token)
and starts a `JUDGE_LEASE_SECONDS` lease on PostgreSQL's clock. A claim past
`JUDGE_MAX_ATTEMPTS` (the limit was lowered while the job waited) is never handed out: it
is ended at once with the fenced `fail` (`judging` to `judge_error`, category
`attempts_exhausted`), so no job stays queued forever and none is loaded or judged; its
attempt count is not reset. `renew`, `finish`, `fail` and `release` each write only when the id,
`state = 'judging'`, the token and an unexpired lease all match, so a worker whose lease ran
out or whose job was claimed again changes nothing. `finish` stores the submission and its
group results in one transaction (any error rolls both back); `fail` is a terminal
`judge_error` with no verdict, score or results; `release` and `sweep` (expired leases,
rows held by others skipped) requeue below the attempt limit and end in `judge_error` at
it. `load_job` reads the submission, its stored v2 hash, the problem's snapshot and the test
data in one REPEATABLE READ, read-only transaction and returns the job only for a live
lease and a matching snapshot; a NULL, invalid or stale snapshot is reported for
`judge_error` and never rebound. Logs (`kyrgyzstem.judging`) carry only the opaque
submission id, attempt, category, state, verdict and duration. Settings:
`JUDGE_LEASE_SECONDS` 30–600 (120), `JUDGE_RENEW_SECONDS` 5 to half the lease (30),
`JUDGE_MAX_ATTEMPTS` 1–10 (3), `JUDGE_POLL_SECONDS` 0.5–60 (2),
`JUDGE_MAX_JUDGING_SECONDS` 60–21600 (1800); out-of-range values stop the process at
startup.

### Judge worker iteration (no migration)

`judge/worker.py` `process_one` runs one iteration: `sweep`, `claim`, `load_job`, then the
tests one at a time. `Executor.run(TestRequest, heartbeat)` (an isolated judge service; tests
use a fake) runs **one test** and gets the language, source, limits and that test's
**input** only, plus a heartbeat that renews the lease every `JUDGE_RENEW_SECONDS` and
reports a loss; it returns the actual output and status, or a `CompileFailure`. The worker
(`_execute`) walks the groups and their tests in ordinal order, beats the heart before each
test, compares each completed output with the expected one itself (`core.check_output`,
i.e. `content.same_output`) and stops a group at its first wrong answer, runtime error,
timeout or memory limit: later tests of that group are never executed, so never
aggregated; the next group still runs. A compile failure on the first test ends execution
as the student's `compile_error`; one after a test has already run (the same source
compiled before) is the judge's fault and, like a judge internal error, stops everything
and releases the job (`judge_internal`), never storing a compile error. No new test starts
once `JUDGE_MAX_JUDGING_SECONDS` have passed since execution began: the job is released as
`judge_timeout` (retried, then `judge_error`). The deadline is checked between tests, so a
test already running may overrun it by up to its own wait (`JUDGE0_MAX_WAIT_SECONDS` for
Judge0); a submission that always needs longer ends in `judge_error`, never in a verdict.
Expected outputs never leave the worker: the verdict comes from `core.judge_submission`
over the groups of the verified snapshot. A NULL, invalid or stale snapshot, or inputs that
do not match it (checked before execution starts, outside the executor's error handling),
end in `judge_error` without calling the executor; runs that do not fit the snapshot end in
`judge_error` (`invalid_test_data`); an internal judge error, an
unreachable or slow judge, or any other executor exception releases the job (retried up
to `JUDGE_MAX_ATTEMPTS`, then `judge_error`). A lost lease, reported by the heartbeat or
found by the fenced `finish`, stores nothing. The worker never runs code, never logs
exception messages or tracebacks, and logs only the queue's allowlisted fields. The
deadline and a shutdown request are both checked at every heartbeat, also while the
executor waits on the judge: past `JUDGE_MAX_JUDGING_SECONDS` the job is released as
`judge_timeout`; after a shutdown's grace period, as `worker_stopping`.

### Queueing and the persistent worker

The API never contacts Judge0. `get_judge()` (judge/base.py) reads `JUDGE_LANGUAGES`:
empty (the default) gives `NoJudge`; otherwise `QueueJudge`, and `create_submission` queues
a new submission only when its language is listed **and** its problem has a valid v2
snapshot (a NULL hash is stored as `not_judged`, never queued to fail later). The response
is built after the commit, so `queued` is never reported for a rolled-back insert.

`scripts/judge_worker.py` runs `runner.run` as its own process (compose service
`judge-worker`, opt-in profile `judge`): it builds the `Judge0Executor` from `JUDGE0_*`
(exit 2 without a URL and a language id, claiming nothing), probes Judge0 before every
claim (`GET /languages` must list every configured id; while it does not, nothing is
claimed, so an outage does not use up attempts), waits `JUDGE_POLL_SECONDS` when idle,
backs off exponentially up to `JUDGE_BACKOFF_MAX_SECONDS` after a released job or a
database or unforeseen error, and stops on SIGTERM/SIGINT (no new claims; a job in flight
gets `JUDGE_SHUTDOWN_GRACE_SECONDS`, then is released; a second signal exits at once and
the lease expires). Its health file (`JUDGE_WORKER_HEALTH_FILE`: update time and whether
Judge0 was ready) backs `--check alive` and `--check ready`. Tested with fakes only
(`tests/test_judge_runner.py`); never run against a real Judge0.

Before storing, `queue.finish(job, result)` checks the result against the job's verified
snapshot with the core's own rules (`validate_result`): exactly the snapshot's groups with
their points, a group's score its points if `accepted` else 0, `accepted` only when every
group passed, the total the sum of the scored groups (ioi) or 100/0 (binary), and a compile
error with score 0 and no groups. A result that does not fit raises `InvalidResult` before
any write, and the worker ends the job as `judge_error` (`invalid_test_data`). Once a job is
claimed, no ordinary exception leaves `process_one`: an unforeseen one is caught without
being bound, logged only as the outcome `unexpected`, and the job is given back with a
best-effort fenced release (if that fails too, the lease expires and the sweeper recovers
it). `BaseException` is not caught. Rules for the judging path: never log with `exc_info`
or a traceback, and never attach an error reporter that captures local variables (frames
hold source code, test data and compiler output).

### Judge0 adapter (used by the worker only; never contacted)

`judge/judge0.py` `Judge0Executor` implements `Executor` against Judge0 CE's batch API
(`POST`/`GET /submissions/batch`, base64, `X-Auth-Token`). `run()` submits one test (a
batch of one) and returns its actual stdout and mapped status, uncompared. It sends only the
language id, source, that test's input and limits: CPU = time limit x the language's
factor, wall = max(1 s, CPU x `JUDGE0_WALL_TIME_FACTOR`) (Judge0 refuses less), memory = limit x the language's factor,
`max_file_size` from `JUDGE0_MAX_OUTPUT_BYTES`, network disabled; never expected outputs or
identifiers. Ordering and stopping are the worker's (above); polling calls the worker's
heartbeat (`LeaseLost` when it fails) and is bounded by
`JUDGE0_MAX_WAIT_SECONDS`; responses are bounded by `JUDGE0_MAX_RESPONSE_BYTES`. HTTP
failures become detail-free `Judge0Error`/`JudgeTimeout` (the worker releases the job).
It logs nothing. Language ids have no defaults (set them from the installation's
`/languages`). Output-limit exceeded is `runtime_error` for now (E5). **Not yet verified
on a live instance:** the status ids and fields, the memory-limit inference (a runtime
error whose reported memory reached the limit is treated as `memory_limit`), base64 line
wrapping, the `/languages` probe, and the server's own limit maxima. Each request is also
cut off once the whole response has taken longer than `JUDGE0_TIMEOUT_SECONDS` (httpx times
each read separately). Only the judge worker constructs it. One HTTP round trip (submit
plus polls) per test is slower
than one batch per group; this is the price of never running a test after a group's first
failure.

### Publication gate (no migration)

`service.publication_blockers()` is the single gate; `service.publish()` (the only code that
sets `status = published`) refuses with `409 publish_blocked` while it returns anything, before
any write. It requires: provenance fields present and valid under the loader's own rules
(`content.check_source`, the license allow-list); confirmed rights; a valid review of every
existing translation, editorial and hint; a verified reference solution; a reviewed difficulty;
a sample; a publisher who is not the linked author. A review is valid only when backed by the
latest audit event for that item from the same reviewer and the audited SHA-256 of the
current text (statements: all five text fields; editorials and hints: the body), so a review
written to the database directly, or one of text changed afterwards, does not count. Rights
count only with the latest `problem.rights_verified` event of the same verifier, the same
`verified_at` and the SHA-256 of the current provenance. The difficulty counts only when the
latest `problem.difficulty_changed` event is for the current band, its `spec_sha256` matches
the current limits, scoring, topics and tags (`service.difficulty_spec_sha256`), and its actor
is not the linked author (the audit entry is the record of the difficulty reviewer; no new
column). The importer keeps a reviewed difficulty only while the file's band equals the band
the review started from (`service.difficulty_review_base_band`: `from` of the latest review
made while the band was the author's estimate, which only the importer sets) and the spec is
unchanged; otherwise it resets band, status and rationale to the file's. It
only reads; it needs no new columns or migration. The admin `review` state is derived from the
same rule. Unpublishing is not gated.

Concurrency: `publish`, `unpublish`, every admin review/rights/difficulty write
(`service._admin_problem(..., lock=True)`) and `importer.import_problem` take the problem row
lock (`SELECT ... FOR NO KEY UPDATE OF problems`, re-reading the row) before reading or
changing anything of that problem. Under PostgreSQL's default READ COMMITTED, the later
transaction waits; the gate's subsequent reads then see the earlier one's committed rows, and
an import that waited for a publish sees `published` and refuses. `FOR NO KEY UPDATE` is enough
because these writers conflict with each other on it and none changes `problems.id`/`slug` or
deletes a problem; unlike `FOR UPDATE`, it does not block the `KEY SHARE` lock that student
inserts referencing the problem (submissions, self-reports, reveals) take. Admin writes set
`lock_timeout` (`service.LOCK_TIMEOUT_MS`, 5 s) for their transaction: the lock is their first
statement, so a timeout (an `OperationalError`, answered `503` with `Retry-After`) leaves
nothing changed. The importer waits without a timeout (an operator tool, one problem per
transaction). Writes that bypass these functions (manual SQL) are not covered by the lock, but
are still caught by the evidence checks above.

### Content pipeline

```
content/curriculum.toml ─┐
content/problems/<slug>/ ─┼─▶ app/modules/olympiad/content.py ──▶ importer.py ──▶ DB (in_review)
  problem.toml, *.md,    │      load + validate metadata,          upsert; never touches
  samples/, solution.py, │      run samples through solution        published problems
  brute.py, gen.py       │      and brute, 300 random cross-checks,
                         │      validate every generated test
tests/test_content.py ───┘    (+ C++ solutions with g++ in CI)
```

Content Python is repository code reviewed in pull requests. It runs only in the
importer and test suite, never in the request path. Publishing requires an admin who is
not the author (see `docs/CONTENT_GUIDE.md`).

**School operations (P5)** — every row carries `organization_id`; all queries are scoped by it.

* `organizations(id, name, kind school|club|other)`, `org_memberships(org_id, user_id, role org_admin|teacher|student|volunteer)`
* `classes(id, organization_id, name)`, `class_members`
* `events(id, organization_id, title, starts_at, capacity)`, `event_registrations`, `attendance_records`, `tutoring_records(volunteer_id, student_id, minutes, date)`

## Request lifecycle (authenticated, unsafe method)

1. Browser sends cookie `ks_session` + header `X-CSRF-Token`.
2. `get_current_session` hashes the token, loads a non-revoked, non-expired session,
   slides `last_seen_at`.
3. CSRF dependency compares `sha256(header)` with `csrf_token_hash` in constant time.
4. Role / ownership checks run in dependencies and services.
5. Sensitive admin actions write an `audit_logs` row in the same transaction.

## Operations

* `GET /api/v1/health` (liveness, always 200) and `GET /api/v1/health/ready` (503 when the
  database does not answer). Optional dependencies (judge, AI provider) never affect
  readiness.
* Database failures map to `503 service_unavailable` (connect timeout 5 s); unexpected
  errors to `500 internal_error` with a request id.
* Structured JSON logging in `app/core/logs.py`; every response has `X-Request-ID`.
* Production topology, client-IP handling (audit F-01), backups: docs/DEPLOYMENT.md and
  docs/OPERATIONS.md.

## Testing strategy

* **API:** pytest against a real PostgreSQL (`TEST_DATABASE_URL`); each test runs in a
  transaction that is rolled back. Tests cover business rules and every authorization
  boundary (anonymous / student / teacher / admin).
* **Web:** Vitest + Testing Library for components and utilities; Playwright for
  critical flows (register → login → profile; later browse → submit → progress).
* **Content:** `tests/test_content.py` checks every problem (samples, brute-force
  cross-checks, constraint validation, Python speed, C++ with `g++`).
* **Contract:** generated `openapi.json` committed; CI regenerates and diffs.

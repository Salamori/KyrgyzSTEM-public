# Roadmap

> **Current state:** phases record the plan. Mentions of 12 problems describe the private collection at that time; this public repository ships only the synthetic `sample-sum` package. Automatic judging is not connected.

Priorities: **P0** Olympiad preparation · **P1** AI learning assistant · **P2** school &
club operations. Phases are delivered as vertical slices (DB → API → UI → tests). A
phase is done only when every acceptance criterion (AC) below is checked **and** the
Definition of Done in `CLAUDE.md` holds.

## Phase 0 — Foundations on paper ✅

- [x] Repository inspected (empty at start; Python 3.13 + uv, Node 24 available; no Docker on the dev machine)
- [x] `ARCHITECTURE.md`, `API_CONTRACT.md`, `SECURITY.md`, this roadmap, `CLAUDE.md`, `README.md`
- [x] Data model for all modules designed; Phase 1 entities final

## Phase 1 — Environment, auth, roles, seed ✅ (except AC-1.1 Docker, unverified)

Verification (2026-10-24): AC-1.2–1.11 covered by `apps/api/tests` (42 tests, real
PostgreSQL 16) and exercised manually in a browser through the Next.js proxy;
AC-1.12–1.13 by Vitest component tests plus manual check on a 375 px viewport;
AC-1.14 via CI diff steps. **AC-1.1 is written but not yet run:** the development
machine had no Docker, so `docker-compose.yml`, both Dockerfiles, the Playwright suite
and the CI workflow have not been executed yet — first CI run will tell.

AC-1.1 `docker compose up` starts Postgres, API and web; `alembic upgrade head` creates the schema; `/api/v1/health` reports database `ok`.
AC-1.2 A visitor can register with email, password (10–128 chars) and display name and is signed in as a **student**; duplicate email → clear error.
AC-1.3 A user can sign in and out; a wrong password and an unknown email produce the same error.
AC-1.4 Session cookie is HttpOnly, SameSite=Lax, Secure when configured; logout revokes the session server-side (old cookie no longer works).
AC-1.5 Unsafe requests without a valid `X-CSRF-Token` are rejected with 403.
AC-1.6 Repeated failed logins are rate-limited with 429 and `Retry-After`.
AC-1.7 A user can view and edit their display name and interface language; role/email cannot be changed through that endpoint (extra fields rejected).
AC-1.8 An admin can list/search users, change roles and (de)activate accounts; a non-admin gets 403 and an anonymous user 401; an admin cannot demote or deactivate themself.
AC-1.9 Every role change, (de)activation and password change creates an audit-log entry visible to admins.
AC-1.10 Deactivating a user immediately invalidates all their sessions.
AC-1.11 `scripts/seed.py` creates clearly synthetic users (one per role) and is idempotent; refuses to run in production.
AC-1.12 UI strings exist in ky/ru/en (TypeScript enforces key parity); language switch persists.
AC-1.13 Login, register, profile and admin pages show accessible loading, empty, error, success and unauthorized states.
AC-1.14 Web API types are generated from OpenAPI; CI fails if they drift.

## Phase 2 — Olympiad workflow (P0) ✅ (except AC-2.9 E2E, unverified)

Design: `docs/proposals/0001-olympiad-domain-model.md` (approved, C1–C8). Verification
(2026-10-24): 132 API tests on PostgreSQL 16 (auth, Olympiad, isolation, DB constraints,
metrics, all 12 problems incl. C++ reference solutions via a clang toolchain), migrations
up/down/up from an empty database, 34 web unit tests, and a manual browser check of the
bank, problem page, hints, submission and the admin review panel. AC-2.6 now means
topic-stage *evidence* (C7), not raw counts. AC-2.8: all 12 problems are imported
`in_review`; **none is published** until a human reviews it.


AC-2.1 Problem bank: list with search, topic, difficulty and locale filters; pagination; empty state.
AC-2.2 Problem page: localized statement (falls back with a visible notice), limits, samples, topics, prerequisites, source, **license and attribution always shown**.
AC-2.3 Only `published` problems are visible to students; drafts visible to their author and admins.
AC-2.4 Student submits a practice attempt (language + code ≤ 64 KiB); without a judge it is stored as `not_judged` and the UI explains that automatic checking is unavailable.
AC-2.5 Submission history (own only — another student's submission → 404).
AC-2.6 Progress page: attempted/solved per topic.
AC-2.7 Hints reveal one at a time; editorial gated behind solve or explicit give-up.
AC-2.8 A small set of **original** problems (CC BY 4.0, authored for this project), each verified against an independent brute force and marked as needing human review.
AC-2.9 Playwright: browse → open → submit → see history → see progress. *(spec written in `e2e/olympiad.spec.ts`; not yet run — needs Docker/CI)*

## Phase 3 — Judge integration

AC-3.1 `JudgeClient` protocol with `Judge0Client` and `NoJudge` implementations; selected via `JUDGE0_URL`.
AC-3.2 Judge0 runs in a separate compose profile on an internal network, no outbound internet, with CPU/memory/time limits.
AC-3.3 API calls to the judge have connect/read timeouts; failures mark submissions `judge_error` and never crash requests.
AC-3.4 Per-user quotas (rate + daily cap) on judged submissions.
AC-3.5 `GET /judge/status`; UI shows a clear "checking unavailable" state.
AC-3.6 Accepted-solution statistics per problem.

## Phase 4 — Teacher tools, learning paths, AI hints (P0 + P1)

AC-4.1 Teachers create problem sets (practice or timed contest) and assign them.
AC-4.2 Learning paths: ordered steps with prerequisites; students see next recommended step.
AC-4.3 Teachers see submissions only for students in sets they own/assigned.
AC-4.4 `HintProvider` protocol, env-configured; no provider → authored hints only, no error page.
AC-4.5 AI output is labelled "not reviewed" and grounded only in approved statement/editorial text; prompts contain no personal data (unit-tested).
AC-4.6 Recommendations based on per-topic weakness (rule-based first; AI optional).

## Phase 5 — School & club operations (P2)

AC-5.1 Organizations, memberships with org roles, classes/clubs.
AC-5.2 Events with registrations and capacity; attendance; volunteer tutoring records.
AC-5.3 **Strict org isolation:** every org route verifies membership; automated cross-org tests for each route.
AC-5.4 Aggregate analytics and CSV reports without per-student sensitive fields.

## Phase 6 — Hardening & release

AC-6.1 Playwright suite for all critical flows in CI.
AC-6.2 Security review against `SECURITY.md` (+ dependency audit); findings fixed or tracked.
AC-6.3 Accessibility review (keyboard, contrast, screen-reader labels, axe checks in Playwright).
AC-6.4 Deployment guide, backup/restore, release checklist; account deletion & data export.
*(Guide, backup/restore procedure and release checklist written 2026-10-25 —
docs/DEPLOYMENT.md, OPERATIONS.md, RELEASE_CHECKLIST.md; dump/restore tested on local
PostgreSQL, Docker parts not yet run; account deletion and export not built.)*
AC-6.5 Nonce-based CSP (drop `'unsafe-inline'` for scripts).
AC-6.6 Kyrgyz (and Russian) interface strings reviewed by native speakers.

## Known limitations carried from Phase 1

- Rate limiter is per process (fine for one API worker; move to Postgres/Redis before scaling).
- Per-IP limits rely on the web proxy forwarding `X-Forwarded-For` and uvicorn trusting it
  (`FORWARDED_ALLOW_IPS`). Audit F-01 showed it is spoofable without a reverse proxy; the
  production compose file adds Caddy to overwrite it (docs/DEPLOYMENT.md §2), unverified on a
  real host.
- No email verification or self-service password reset yet (needs an email provider).

## Known limitations carried from Phase 2

- No judge: every submission is `not_judged`, so progress has no evidence yet (by design until Phase 3).
- Kyrgyz problem statements do not exist yet; the UI falls back to Russian and says so.
- "Demonstrated" uses a provisional rule (≥ 2 hint-free judged accepts at that stage or
  higher); band and checkpoints are ignored until learning paths exist (Phase 4).
- Retention and mixed-set recognition metrics need Phase 4 data and are not computed.
- Problem authoring over HTTP is deferred to Phase 4; content arrives through `content/` PRs.
- Teachers have student-level access to Olympiad data until Phase 4/5 define their scope.

## Open decisions (defaults in effect)

| Question | Default until decided |
|---|---|
| Hosting target | Any Docker host; docs assume a single VM + managed Postgres. |
| Email verification / password reset by email | Not in P1 (needs an email provider). Admin can deactivate; password reset via admin is P2 follow-up. |
| Content license for original problems | CC BY 4.0 for statements/editorials; MIT for code (code license confirmed 2026-10-25). |
| Minimum student age / guardian consent | Must be decided with a school partner before public launch; no extra data collected meanwhile. |

# Security & QA audit — M2

> **Historical report from 2026-10-25, updated where a finding was later fixed.** Mentions of 12 problems describe the private collection at that time. It is not a statement that the application is secure.

| | |
|---|---|
| Scope | `main` at `ccb86ea` (Phases 1–2, optional AI assistant, club attendance), plus the fixes listed below (uncommitted working tree) |
| Date | 2026-10-25 |
| Auditor | Separate Claude Code session (requirements conflict X3). It did not write the audited code, but it is the same model family as the author, so it is **not** a substitute for the human review M2 also requires. |
| Method | Read the docs as hypotheses, read every module in `apps/api/app` and the security-relevant parts of `apps/web/src`, wrote probe tests against the real PostgreSQL test database, reproduced the proxy-header issue end to end with the built Next.js server, and ran the dependency audits. |

**This report does not say the application is secure.** Passing tests only show that the
cases someone thought of behave as intended. Sections 4 and 5 list what was and was not
checked.

Severity uses impact on a deployment serving minors: **High** (exploitable remotely,
defeats a stated control), **Medium** (real defect with limited impact or needing a
precondition), **Low** (hardening, robustness, accuracy of documentation), **Info**.

Status: **Fixed** = minimal fix applied here with a regression test. **Open** = reported;
the fix needs a decision or is larger than a minimal change.

## 1. Release blockers (in priority order)

1. **F-01** Client IP is attacker-chosen behind the shipped Next.js/compose setup, so every
   IP-based rate limit (login, registration, join codes) can be bypassed. *Open — needs a
   deployment decision.*
2. **F-02** No per-account login throttle: with F-01, password guessing against any one
   student account is unlimited. *Open — policy decision (lockout vs. guessing).*
3. **F-05** The "publisher is not the author" gate never fires for imported content, which
   is all content today. *Mitigated, not closed* — authors can be linked at import
   (`--author-email`), and with no linked author, rights confirmation requires an audited
   "not an author" attestation. The owner decides whether attestation is acceptable or
   linking becomes mandatory.
4. **F-07** Insecure cookie default with no production guard. *Fixed* — keep the fix.
5. **F-03, F-04** Malformed input causes 500s; CSV export fails for Cyrillic group names.
   *Fixed* — keep the fixes (F-04 breaks the main school workflow for local names).
6. Human review items in §5, in particular content review and a real deployment check.

Everything else can ship as known issues if the owner accepts them.

## 2. Findings (ordered by severity)

### F-01 · High · Spoofable client IP defeats all IP-based rate limits — Open

- **Component:** `apps/api/app/modules/auth/deps.py:client_ip`, `docker-compose.yml`
  (`FORWARDED_ALLOW_IPS: "*"`), `apps/api/Dockerfile`, Next.js rewrite in
  `apps/web/next.config.ts`. Affects `POST /auth/login`, `POST /auth/register`,
  `POST /groups/join` (IP part), `POST /auth/password` is per-user and unaffected.
- **Scenario (reproduced):** built web app (`next start`) with the `/api/*` rewrite pointed at
  an echo server run under the same uvicorn flags as the compose file
  (`--proxy-headers --forwarded-allow-ips=*`).
  `curl -H "X-Forwarded-For: 203.0.113.7" http://web/api/v1/auth/login` → uvicorn reports
  `client=203.0.113.7`. A different header value per request gives a different "client".
  With the Dockerfile default (`--forwarded-allow-ips=127.0.0.1`, web and API on one host)
  the result is the same: Next.js does **not** append the real peer address (it only sets
  `x-forwarded-for` when the header is absent, `next/dist/server/base-server.js`), so the
  rightmost entry is still the attacker's.
- **Expected:** the limiter keys on the real client address. **Actual:** the client picks it.
- **Impact:** login limit 10/15 min per IP+email and 50/15 min per IP, registration 10/h/IP
  and join codes 30/h/IP become unlimited. Combined with F-02 this allows unbounded
  online password guessing against any account, mass registration, and memory growth in
  the limiter (F-08).
- **Minimal fix (deployment):** put a reverse proxy (nginx/Caddy/the hosting load balancer)
  in front of `web` that **overwrites** `X-Forwarded-For` with the peer address, and set
  `FORWARDED_ALLOW_IPS` on the API to the web container only (a fixed compose subnet).
  Do not use `*`. Document it in the deployment guide (M12). Without such a proxy, set
  `FORWARDED_ALLOW_IPS` to a value that trusts nothing — then all users share the web
  container's address, which turns the 50/15 min per-IP login limit into a global limit;
  that is why this needs an owner decision rather than a one-line change here.
- **Regression test:** none in-process (the behaviour lives in uvicorn + Next.js). Add a
  compose-level smoke test to the M1/M12 E2E job: send two logins with different spoofed
  `X-Forwarded-For` values and assert the per-IP counter is shared.

### F-02 · High (with F-01) / Medium (alone) · No per-account login throttle — Fixed 2026-10-31

- **Fix (after this audit):** `login-account:{email}`, 30 attempts an hour, counted the same for
  unknown emails. Accepted trade-off: someone who knows an email can lock it out for up to an
  hour. Admin-issued reset links now exist (docs/SECURITY.md). Tests:
  `tests/test_account_security.py`.

- **Component:** `apps/api/app/modules/auth/router.py:login`.
- **Scenario:** an attacker with many IPs (or with F-01, one IP) tries passwords for
  `student@school…`; each IP gets 10 tries per 15 minutes for that email, forever.
- **Expected (docs/SECURITY.md "brute force"):** guessing a given account is bounded.
  **Actual:** bounded per source address only.
- **Minimal fix:** add `limiter.hit(f"login-email:{email}", limit=30, window_seconds=3600)`
  (numbers to be chosen) and accept that an attacker can then lock a known email out for an
  hour; or add exponential back-off per account. Owner decision because of the lockout
  trade-off for students. Also note there is **no password reset** of any kind (neither
  self-service nor admin), so a locked-out or forgetful student needs database access.
- **Regression test:** 30 failed logins for one email from 30 different `TestClient`
  `client` addresses → the 31st returns `429`.

### F-03 · Medium · NUL bytes and out-of-range numbers cause HTTP 500 — Fixed

- **Component:** every request body string, every string query/path parameter, `offset`.
- **Scenario (reproduced before the fix):** `PATCH /users/me {"display_name": "a\u0000b"}`,
  `POST /submissions` with a NUL in `source_code` or `problem_slug`,
  `POST /orgs/{id}/groups {"name": "x\u0000"}`, `GET /problems?q=%00`,
  `GET /problems/de%00mo`, `GET /problems?offset=100000000000000000000`,
  `GET /submissions?offset=1e20` → `psycopg.DataError` / `NumericValueOutOfRange` → 500.
  `POST /groups/join` with a NUL returned 404 instead of a validation error.
- **Expected:** a 4xx with the error envelope. **Actual:** unhandled exception, 500, stack
  trace in the server log, and a request any anonymous visitor can repeat.
- **Fix:** `RequestModel` rejects NUL in every string field (`app/core/schemas.py`, 422
  `validation_error`); a handler maps `sqlalchemy.exc.DataError` to
  `400 bad_request` without echoing the value (`app/core/errors.py`).
- **Regression tests:** `tests/test_security_audit.py::test_nul_bytes_in_bodies_are_rejected_not_500`,
  `::test_malformed_query_and_path_values_are_client_errors`,
  `::test_nul_byte_in_org_names_is_rejected`.

### F-04 · Medium · Attendance CSV export fails for non-ASCII group names — Fixed

- **Component:** `GET /orgs/{org_id}/groups/{group_id}/attendance.csv`
  (`orgs/router.py`, filename from `orgs/service.py:export_csv`).
- **Scenario (reproduced):** a group named `Олимпиадный кружок 9-Б` (realistic for a
  Kyrgyz or Russian school) → the filename keeps Cyrillic letters (`str.isalnum`) → the
  `Content-Disposition` header cannot be encoded as Latin-1 → 500. The `report.exported`
  audit row is committed *before* the response fails, so the audit log records an export
  that never reached the user.
- **Fix:** ASCII fallback `filename="…"` plus `filename*=UTF-8''…` (RFC 6266/5987).
- **Regression test:** `::test_csv_export_works_for_cyrillic_group_names`.
- **Remaining:** the audit-before-response ordering stays; it is harmless once the response
  cannot fail on the name.

### F-05 · Medium · "Publisher is not the author" is unenforced for imported content — Mitigated

- **Mitigation (after the audit):** `scripts.import_content` requires either
  `--author-email EMAIL` (mandatory whenever the author is known) or an explicit
  `--author-unlinked`; there is no default. `--author-email` links imported problems to the
  author's account (`created_by`), so `reviewer_is_author` and `approver_is_author` apply; a
  different existing link is refused. For problems with no
  linked author, `POST /admin/problems/{slug}/rights/verify` (required before publishing)
  needs `attest_not_author: true` (`422 author_attestation_required` otherwise), and the
  audit entry records `author_identity: "unlinked"`. This is a statement by a trusted admin,
  not an identity check: an admin who is in fact the author can still attest falsely.
  Translation and difficulty reviews still rely on `created_by` alone. Tests:
  `tests/test_provenance.py`.
- **Still open:** owner decision whether to require `--author-email` for every import (or a
  second admin) before a real pilot.

- **Component:** `olympiad/importer.py` never sets `Problem.created_by`;
  `olympiad/service.py:publish_blockers`, `review_difficulty`, `review_translation` compare
  `created_by` with the admin.
- **Scenario:** an admin who wrote `content/problems/x/` (named in `authors_md`) imports it,
  reviews its translation and difficulty, and publishes it. `created_by` is `NULL`, so
  `approver_is_author` / `reviewer_is_author` never trigger.
- **Expected (SECURITY.md, CONTENT_GUIDE):** publishing needs an admin who is not the author.
  **Actual:** the rule only works for rows where tests set `created_by` by hand.
- **Minimal fix (needs design):** have `scripts.import_content` take `--author-email`
  (or map `authors_md` to accounts) and store it in `created_by`; refuse publish when
  `created_by` is `NULL` until then. The second half is a one-line blocker but would block
  publishing of all current content, so it needs owner approval.
- **Regression test:** import a problem with an author account, then assert that account
  gets `403 reviewer_is_author` / `409 publish_blocked [approver_is_author]`; and a problem
  with no recorded author is blocked.
- Related, Low: re-importing an `in_review` problem keeps `difficulty_status = reviewed`
  when the band is unchanged but overwrites the reviewed rationale with the author's text,
  and keeps translation review when only tests, hints or editorials changed.

### F-06 · Medium · AI quotas ignored paid calls that were withheld or failed — Fixed

- **Component:** `ai/service.py:used_today`, `_budget_blocked`.
- **Scenario (reproduced):** a student asks for code at level 1. Each answer is generated
  and paid for, then withheld (`code_withheld`) → recorded as `mode = fallback` → not counted
  by the daily per-user quota or the global daily cap, which only counted `mode = "ai"`.
  The only limit left was 6/min/user (~8,600 calls/day/user). Provider timeouts were
  likewise uncounted. With `LLM_MAX_TOKENS=16000` and the default prices one student could
  spend the whole monthly budget (`AI_MONTHLY_BUDGET_USD=25`) in hours, switching the
  assistant off for everyone.
- **Fix:** every request that reached the provider (all outcomes except `not_configured`,
  `quota_exceeded`, `budget_exhausted`) counts against the per-user quota and the global cap.
- **Regression tests:** `::test_withheld_ai_answers_count_towards_the_daily_quota`,
  `::test_provider_failures_count_towards_the_global_daily_cap`.
- **Remaining (Low):** quota and budget are check-then-act; parallel requests can overshoot
  by a few calls. Timeouts record `cost_usd = 0` although the provider may still bill.

### F-07 · Medium · `SESSION_COOKIE_SECURE` defaults to false with no production guard — Fixed

- **Component:** `app/core/config.py`, `docker-compose.yml` (`${SESSION_COOKIE_SECURE:-false}`).
- **Scenario:** deploy with `APP_ENV=production` and forget the variable → session and CSRF
  cookies are sent over plain HTTP. Also `APP_ENV` itself defaults to `development`, which
  exposes `/api/v1/docs` and the OpenAPI document.
- **Fix:** `Settings` refuses to load when `APP_ENV=production` and the cookie is not secure.
- **Regression test:** `::test_production_refuses_insecure_session_cookies`.
- **Remaining (Low):** no `Strict-Transport-Security` header from either app; a forgotten
  `APP_ENV` still exposes the API docs.

### F-08 · Low · Rate limiter state grows without bound — Fixed

- **Component:** `app/core/rate_limit.py`. Keys contain client-chosen text (emails, IPs) and
  were never removed, so memory grows with every distinct key; with F-01 an attacker
  chooses unlimited keys.
- **Fix:** idle keys (no hit within their window) are swept every 1,024 hits.
- **Regression test:** `::test_rate_limiter_forgets_idle_keys`.
- **Remaining:** per-process state (documented decision A6).

### F-09 · Low · Last org admin could demote themself through `POST /orgs/{id}/members` — Fixed

- **Scenario:** the only org admin posts their own email with `role: teacher` → upsert
  changes the role → organization without an admin (only a platform admin can repair it).
  `DELETE …/members` already had the `last_org_admin` check; the upsert path did not.
- **Fix:** shared `_ensure_not_last_admin` in `orgs/service.py`; `409 last_org_admin`.
- **Regression tests:** `::test_last_org_admin_cannot_be_demoted_through_add_staff`,
  `::test_one_of_two_org_admins_can_still_be_demoted`.
- **Remaining:** two admins removing each other concurrently can still both succeed.

### F-10 · Low · Platform admins can read rosters by assigning themselves — Open (docs fixed)

- `POST /admin/orgs/{id}/admins` accepts the caller's own email; the platform admin then
  reads every roster, attendance record and CSV of that organization. It is audited
  (`org.admin_assigned`), but `docs/SCHOOL_OPERATIONS.md` §3 says platform admins "cannot
  read attendance or rosters". SECURITY.md now says this is procedural. **Fix options:**
  forbid self-assignment (`actor.id == user.id → 400`) and alert on `org.admin_assigned`;
  owner decision. The same endpoint and `POST /orgs/{id}/members` also confirm whether an
  email has an account and return its display name (enumeration by org admins).

### F-11 · Low · AI solution leakage by paraphrase (latent) — Open

- `ai/service.py` always calls `collect_sources(..., include_editorial=True)`, so at levels
  1–3 the reviewed editorial is in the prompt and only the 8-word-shingle overlap check
  stands between it and the student. A paraphrase passes. It is latent today because no
  editorial review workflow exists, so no editorial is ever "reviewed" and none is sent.
  **Fix before enabling editorial review:** include the editorial only at level 4, or
  accept the risk explicitly in proposal 0004 and measure it in the eval set.
- Related heuristics that are easy to bypass (by design, but worth recording): the
  correctness-claim filter misses phrasings such as "this gets AC"; the code detector at
  level 3 misses Python without `def`/`print(`/`import`; `neutralize` does not match
  `< /source>` with a space after `<`. Only a human-reviewed eval set can say how often
  these matter (CLAUDE.md: no accuracy claims without it).

### F-12 · Low · AI suggestions rendered clickable links — Fixed

- `AssistantPanel` rendered the unreviewed `suggestion` through `Markdown`, which keeps
  `<a target="_blank">`. A model steered by the student's question (or by injected text in
  a future source) could put an external link in front of a minor under the platform's
  name. **Fix:** `Markdown links={false}` for AI text; links become plain text.
- **Regression tests:** `apps/web/src/components/olympiad/assistant.test.tsx`
  "never turns links in AI output into clickable links (audit F-12)" (verified to fail
  without the fix); `olympiad.test.tsx` "neutralises script URLs and opens links safely"
  and "can drop links entirely for unreviewed text".

### F-13 · Low · Code drafts survive sign-out on shared computers — Fixed 2026-10-31

- **Fix (after this audit):** sign-out, sign-out everywhere and account deletion clear
  `ks:draft:*` on this device, so drafts are lost at sign-out.

- Drafts are stored in `localStorage` under `ks:draft:<slug>:<language>`, not per user, and
  are not cleared on sign-out. In a school computer lab the next student sees the previous
  student's code. A manual clear exists in the privacy panel. **Minimal fix:** clear
  `ks:draft:*` in `SessionProvider.signOut` (and key drafts by user id). UX decision
  (drafts would be lost on sign-out). **Test:** sign out → keys with the prefix are gone.

### F-14 · Low · Robustness and enumeration in registration — Partly fixed 2026-10-31

- **Fix (after this audit):** registration is one `ON CONFLICT DO NOTHING` insert, so concurrent
  duplicates get `409`, not `500`; the password is hashed in both cases; 5 attempts an hour per
  email. **Still open:** `409 email_taken` confirms an account, until email verification exists.

- Two concurrent registrations with the same email both pass the existence check; the
  second hits the unique index → `IntegrityError` → 500. **Fix:** catch `IntegrityError`
  on commit → `409 email_taken`. Not covered by a test here (needs two connections).
- `409 email_taken` confirms an account exists (rate-limited, but see F-01). Accepted
  trade-off for a student-facing form; record it as a decision.

### F-15 · Low · Slow AI provider can exhaust the API thread pool — Open

- `POST /problems/{slug}/assistant` is a sync route calling the provider with a 30 s timeout
  and `max_retries=1` (up to ~60 s per request). Starlette's default pool has 40 threads; ~40
  students waiting on a slow provider stall every other sync endpoint, including login.
  **Fix:** lower the timeout, `max_retries=0`, or a semaphore (e.g. 8 concurrent provider
  calls, otherwise `provider_unavailable` fallback). Also: exceptions outside the mapped
  `anthropic` classes (e.g. response validation) become a 500.

### F-16 · Low · Session lifetime — Fixed 2026-10-31

- **Fix (after this audit):** 7-day idle timeout (`SESSION_IDLE_HOURS`), a password change
  replaces the current session too, and `POST /auth/logout-all` ends every session.

- 14-day absolute expiry, no idle timeout; `last_seen_at` is recorded but never enforced.
  Password change keeps the current session token (other sessions are revoked, as
  documented). Consider a 7-day idle timeout for shared computers.

### F-17 · Info · DF1 confirmed — Fixed 2026-10-27 (after this audit)

- Give up → read the editorial → accepted submission still counted as **unaided** evidence
  (`olympiad/metrics.py` saw only `hints_revealed_count`). Fixed: a submission created at or
  after the student's `gave_up` editorial unlock is never unaided (it counts as solved with
  help); one created before it still counts. `::test_accept_after_giving_up_is_not_unaided`
  no longer carries its `xfail(strict=True)` mark; `::test_accept_submitted_before_giving_up_stays_unaided`
  and two `test_metrics.py` tests were added. The results table below predates the fix.
- DF2 (stale docstring at `olympiad/models.py:4`) confirmed, not changed.

### F-18 · Info · Other observations

- Level-4 assistant help is unreachable for a problem without an editorial while no judge
  exists (give-up requires an editorial; a solve requires a judge).
- A teacher removed from the organization stays `lead_id` of their groups (only org admins
  can then manage them; the name still shows as lead).
- Join codes are multi-use for 7 days; a student removed by the teacher can rejoin with
  the same code until it is regenerated. Intended, but teachers should be told.
- uvicorn access logs include query strings, so admin user searches (`/admin/users?q=…`)
  put student emails in logs. No other secrets travel in query strings.
- Web CSP keeps `'unsafe-inline'` for scripts (documented; nonce CSP planned).

## 3. Claims checked and found to hold

- Passwords: Argon2id, never echoed in validation errors, unknown email and wrong password
  take the same path; inactive users are rejected at login and at every request.
- Sessions: opaque random tokens hashed at rest, server-side revocation on logout,
  deactivation and password change; replaying a logged-out cookie gives 401.
- CSRF: required on every unsafe authenticated route (all go through
  `get_current_session`); constant-time comparison; `get_optional_user` refuses unsafe
  methods.
- Mass assignment: every request model extends `RequestModel` (`extra="forbid"`);
  registration cannot set a role; `PATCH /users/me` cannot change role or email; admins
  cannot change their own role or deactivate themselves.
- Injection: no string-built SQL found; `LIKE` searches escape wildcards.
- Hidden tests and reference solutions: no route returns hidden test cases or reference
  solution source; submission detail exposes per-group results only.
- Submissions: other users' submissions return 404; no route executes code; with `NoJudge`
  submissions are stored as `not_judged` and the UI labels them.
- Organizations: every org route resolves membership from the URL and checks nested IDs
  against it (existing cross-org tests plus code reading); teachers cannot reach groups they
  do not lead; students see only their own status; CSV cells are escaped against formula
  injection; CSV and summary require "manage".
- Markdown: no raw HTML, no images, `javascript:`/`data:` link targets are neutralised
  (new test).
- AI: prompts contain no names, emails or ids; `ai_requests` stores no text; unpublished
  problems and other users' submissions return 404; level 4 requires solve/give-up.
- Secrets: `.env` is git-ignored; no secret is logged by the application code.

## 4. What was run (exact results)

Environment: Windows 11, Python 3.12.15 (uv), Node at `C:\Program Files\nodejs`, local
PostgreSQL on port 54329 (`kyrgyzstem_test`, wiped by the suite), C++ via the cached
`ziglang` 0.16.0 (`KS_CXX="<zig.exe> c++"`).

Baseline at `ccb86ea`, before any change:

| Check | Result |
|---|---|
| `uv run ruff format --check . && uv run ruff check .` | 74 files already formatted · All checks passed |
| `uv run mypy app scripts tests` | Success: no issues found in 67 source files |
| `uv run pytest --ignore=tests/test_content.py` | 130 passed |
| `npm run lint` / `npm run typecheck` | clean / clean |
| `npm test` | 7 files, 61 tests passed |
| `npm run build` | succeeded (22 routes) |
| New `tests/test_security_audit.py` against unfixed code | 15 failed, 1 passed (the passing one is the control case `test_one_of_two_org_admins_can_still_be_demoted`) |

After the fixes:

| Check | Result |
|---|---|
| `uv run ruff format --check . && uv run ruff check .` | 75 files already formatted · All checks passed |
| `uv run mypy app scripts tests` | Success: no issues found in 68 source files |
| `TEST_DATABASE_URL=… KS_CXX=… uv run pytest` (all, incl. content + C++) | exit 0 · 200 tests: 199 passed, 1 xfailed (F-17/DF1, intended), 0 failed, 0 errors, 0 skipped (all 12 C++ reference checks ran). See the C++ note below |
| `npm run lint` | clean (exit 0) |
| `npm run typecheck` | clean (exit 0) |
| `npm test` | 7 files, 64 tests passed (61 existing + 3 new) |
| `npm run build` | succeeded (exit 0) |
| OpenAPI re-export vs `apps/web/src/lib/api/openapi.json` | identical (no contract drift) |
| `uvx pip-audit -r <uv export --frozen>` (155 packages) | No known vulnerabilities found |
| `npm audit` | found 0 vulnerabilities |

**C++ note (flaky):** in two earlier full runs after the fixes, one C++ reference check each
failed with `CalledProcessError` from the compiler (two different problems; a
different problem each time). Both sources compile cleanly when compiled by hand with the
same command, and the same tests passed in three separate `-k cpp` runs and in the final
full run. The first failure happened just after I stopped an earlier pytest run mid-way,
so a Zig cache lock is the likely cause, but I did not capture the compiler's stderr and
have not proven it. CI uses g++ and is not affected by this Zig set-up.

Not run: `npm run e2e` (needs the full stack; Playwright has never run, see REQUIREMENTS),
`docker compose` (not available in this audit), the configured AI eval (costs money).

## 5. Needs manual or human review

- **Independent human review** of this report and of the code (M2 requires it; this audit
  was done by the same model family that wrote the code).
- **Deployment topology** for F-01: which reverse proxy, what it sets, and a live test.
- **Policy decisions:** F-02 lockout vs. guessing, F-05 author mapping, F-10 platform-admin
  self-assignment, F-13 clearing drafts on sign-out, account recovery (none exists).
- **Content:** the 12 problems and their `generated_with_ai` provenance, translations
  (especially Kyrgyz), and licences — a human reviewer's call, not an automated one.
- **AI assistant:** a human-reviewed eval set before claiming anything about leakage or
  accuracy (F-11 heuristics); provider data-retention terms before enabling it for minors.
- **Legal/consent:** parental consent for minors, retention periods with a real school
  (SCHOOL_OPERATIONS §5).
- **Not covered by this audit:** the future Judge0 integration (Phase 3, nothing to test),
  accessibility, load testing, browser-level testing of the CSP, and the Docker images.

## 6. Files changed by this audit

API: `app/core/schemas.py`, `app/core/errors.py`, `app/core/config.py`,
`app/core/rate_limit.py`, `app/modules/ai/service.py`, `app/modules/orgs/service.py`,
`app/modules/orgs/router.py`, `pyproject.toml` (F811 ignore for fixture imports, as for
`test_ai.py`), new `tests/test_security_audit.py`.
Web: `src/components/Markdown.tsx`, `src/components/olympiad/AssistantPanel.tsx`,
`src/components/olympiad/assistant.test.tsx`, `src/components/olympiad/olympiad.test.tsx`.
Docs: this file, `docs/SECURITY.md`, `docs/API_CONTRACT.md`, `.env.example`.
Nothing is committed.

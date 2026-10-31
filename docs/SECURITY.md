# Security Model

KyrgyzSTEM serves minors. Default to collecting less, exposing less and logging what
administrators do.

## Reporting a vulnerability

Please do not open a public issue. Use the repository's private vulnerability reporting
form (Security tab, "Report a vulnerability") with steps to reproduce. We aim to acknowledge within 7 days.

## Assets and threats

| Asset | Threats | Controls |
|---|---|---|
| Student accounts | Credential stuffing, brute force, session theft | Argon2id hashing; login rate limits per IP, per IP+email and per account (30 attempts an hour, audit F-02; someone who knows an email can therefore lock it out for up to an hour, accepted over unlimited guessing); generic `invalid_credentials`; opaque session tokens hashed at rest; `HttpOnly` + `SameSite=Lax` (+ `Secure` in prod) cookies; 14-day absolute expiry and 7-day idle timeout (`SESSION_IDLE_HOURS`); server-side revocation on logout, sign-out everywhere, password change or reset, and deactivation; a password change replaces the current session too. |
| Account recovery | Takeover through the recovery path | No email is sent (no provider is configured), so recovery is manual: an admin (or an operator, for admins) issues a single-use link, stored hashed, valid `PASSWORD_RESET_TTL_MINUTES` (default 60), audited, and delivered out of band after checking who is asking. The token travels in the URL fragment, which browsers do not send to servers. Using it revokes every session. An admin who issues a link can take over that account, so this is a procedural control; admins cannot use it on themselves or on other admins. |
| Email enumeration | Learning who has an account | Login failures are identical for unknown emails and wrong passwords, and rate limits answer the same for both. Registration hashes the password either way, so timing does not differ. **Open:** `409 email_taken` still confirms an account; only email verification can remove it (F-14). |
| Student data (PII) | Over-collection, leakage via API, leakage to LLM providers | Profile holds only email, display name, locale. Response schemas are explicit allow-lists (`UserPrivate`, `UserAdminView`); no ORM objects are returned directly. AI module *(P4)* sends problem context only — never names, emails or IDs. |
| Hidden test data & reference solutions | Exfiltration through API | Separate tables; response schemas expose only sample tests and per-subtask results; tests assert hidden inputs never appear in responses. Reference solutions have no read endpoint. |
| Judge infrastructure | Arbitrary code execution, resource exhaustion | **No code is ever executed by the web or API process.** Judge0 runs as an isolated service on an internal network with CPU/memory/time/process limits and no outbound network; per-user submission quotas; request timeouts; circuit breaker → `judge_unavailable`. *(P3)* |
| Unreviewed content | AI-drafted or wrong problems reaching students | Imported problems start `in_review`; publishing needs an admin who is not the author, a reviewed translation and difficulty, and a reference solution verified on the current tests. Every review step is audited. |
| AI assistant *(optional)* | Leaking solutions, false correctness claims, prompt injection, personal data to a provider, runaway cost | Retrieval from published problems and reviewed editorials only; untrusted text fenced and tag-neutralized; emails/phone numbers scrubbed; no names/emails/ids in prompts; output validation withholds code/editorial copies below level 4 and removes correctness claims; per-user quota, global cap, monthly budget, timeout; key server-side only; `ai_requests` stores no text (proposal 0004). |
| Content code | `solution.py`/`gen.py` in `content/` are executed by the importer and tests | Treated as code: reviewed in PRs, never uploaded through the web, never run in the request path. |
| Admin powers | Abuse, mistakes | Role changes, (de)activation and password changes write `audit_logs`; admins cannot change their own role or deactivate themselves; audit log is append-only via the API. |
| Organizations *(P5)* | Cross-tenant access | Every org-scoped query filters by `organization_id` derived from the URL **and** verified against the caller's membership; tests cover cross-org access for every route. |

## OWASP checklist (how each is addressed)

* **Broken access control:** authorization in FastAPI dependencies (`require_user`,
  `require_role`) and services; default-deny routers; tests for anonymous / student /
  teacher / admin on every protected route.
* **Integrity:** CHECK constraints and unique indexes back the business rules (verdict only
  when judged, one primary topic, ordinals/stages in range); idempotent writes use
  `INSERT … ON CONFLICT DO NOTHING` so concurrent requests cannot fail.
* **Injection:** SQLAlchemy Core/ORM with bound parameters only; no string-built SQL.
  `LIKE` search escapes `%`/`_`.
* **XSS:** React escapes by default. Markdown is rendered with `react-markdown` without
  raw HTML, with images stripped and an element allow-list (unit-tested). Security headers: `Content-Security-Policy`,
  `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options: DENY`.
* **CSRF:** `SameSite=Lax` cookies + synchronizer token (`X-CSRF-Token`, verified against
  a hash stored on the session, constant-time compare).
* **Auth failures:** see account controls above. Session fixation is impossible since a
  new token is minted at every login.
* **Logging:** structured JSON logs (`app/core/logs.py`) record method, route *template*,
  status, duration and a request id. No bodies, query strings, raw paths, cookies, tokens,
  IPs, emails or user ids; uvicorn's access log (client address + query string) is disabled
  in code; SQL errors hide bound parameters; unhandled errors log a traceback and return
  `500 internal_error` with the request id (tests in `tests/test_ops.py`,
  docs/OPERATIONS.md §3). This also resolves the audit's note (F-18) that admin user
  searches put student emails in access logs.
* **Sensitive data exposure:** secrets only in environment variables; `.env` is
  git-ignored; only `.env.example` is committed. Logs never include passwords, tokens
  or request bodies of auth routes.
* **Mass assignment:** request schemas are separate Pydantic models with
  `extra="forbid"`; role can only change through the admin endpoint.
* **Rate limiting:** auth endpoints; submissions (6/min/user); hint reveals, editorial
  unlocks and self-reports (60/min/user); AI hints when they ship.
  Limitation: with `RATE_LIMIT_BACKEND=memory` (the default) limiter state is per-process:
  run a single API worker. `RATE_LIMIT_BACKEND=postgres` keeps the counts in PostgreSQL
  (`rate_limit_hits`), shared by every instance, and is required on Vercel, where instances
  share no memory and start and stop at will. Each hit takes a per-key advisory lock in its
  own short transaction, so concurrent instances cannot both take the last slot; times come
  from the database clock. Hits use their own fixed connection pool, never the request
  pool, so concurrent authenticated requests cannot exhaust it; a hit that gets no
  connection in time fails with 503 instead of skipping the limit.
  **Open (audit F-01):** IP-keyed limits trust `X-Forwarded-For`. Next.js passes a
  client-supplied header through unchanged, so behind the shipped compose setup a client can
  pick its own IP. A reverse proxy that overwrites the header is required before deployment
  (docs/SECURITY_AUDIT.md). The production compose file now has one (Caddy, `deploy/`), and
  the API trusts the header only from the web container; this stays open until
  `deploy/smoke-test.sh` passes against a real deployment (docs/DEPLOYMENT.md §2).
  With the web app on Vercel, the API instead trusts the header only from an API gateway
  that requires a shared secret and takes the client address from Vercel's own
  `x-forwarded-for` (docs/DEPLOYMENT.md, "Vercel frontend with an API gateway"). Anyone
  holding that secret can choose their rate-limit identity, so it is handled like a
  credential. Not deployed or verified yet.
  With the API itself on Vercel (experiment, docs/DEPLOYMENT.md "Vercel-only deployment"),
  `CLIENT_IP_SOURCE=vercel` takes the client address from `x-forwarded-for`, which Vercel's
  edge overwrites with the client's public IP (it never forwards a client-supplied value),
  and the API has no other ingress. The setting is refused unless `VERCEL=1`, `VERCEL=1`
  requires it, and a request whose header is not exactly one IP address gets 503 instead of
  a shared identity. Checked against Vercel's documentation and the source of its Python
  runtime (`vercel-runtime` 0.24.1, which serves the app through uvicorn on 127.0.0.1); not
  yet verified on a live deployment.

## Data handling

* Seed data is synthetic: `is_synthetic = true`, emails on the reserved `example.com`
  domain, names prefixed `[Synthetic]`. The seed script refuses to run when
  `APP_ENV=production`.
* Code drafts are stored only in the student's browser (`localStorage`) until submitted.
* Judge0 and its token are known to the judge worker only (`JUDGE0_*` is not in the API's
  environment); the API only queues rows (`JUDGE_LANGUAGES`). Production refuses a Judge0
  URL without a token, and URLs with credentials, a query or a fragment. The worker's
  health file holds only an update time and a ready flag.
* Judging path (judge/queue.py, judge/worker.py, judge/runner.py): no log record carries `exc_info` or a
  traceback, exception messages are never logged, and no error reporter that captures local
  variables (for example a Sentry-style SDK with local-variable capture) may be attached:
  frames there hold submitted source, test inputs and outputs, and compiler output. Database
  errors are replaced by a detail-free `QueueDatabaseError`; unforeseen worker errors are
  contained and logged only as the outcome `unexpected` (tests check both).
* Submissions keep judging metadata (migration 0009): the hash of the test set used, an
  attempt counter, a lease time and, for compile errors only, the compiler's message
  (at most 4096 characters). The message is derived from the student's own code, like the
  stored source: shown only to the student and admins, never containing test data. No new
  personal data.
* With the AI assistant enabled, the student's question and, only if they tick "attach", their
  own code are sent to the configured provider; the provider's retention terms apply (owner
  decision before enabling).
* The PostgreSQL rate limiter (`RATE_LIMIT_BACKEND=postgres`) stores, per counted request,
  an HMAC-SHA256 of the limiter key (keyed with `RATE_LIMIT_KEY_SECRET`) and two times. The
  keys themselves contain IP addresses, emails and user ids and are never stored; without
  the secret a hash cannot be linked back to them. Rows are deleted once their window has
  passed (at most one hour), by the limiter itself and by `scripts.purge_retention`. Nothing
  else reads the table.
* Hint usage, give-ups and self-reports are visible only to the student (and admins);
  teacher visibility is an open decision (proposal 0001 §11).
* No birth dates, phone numbers, home addresses, photos or national IDs are collected.
  Adding any new personal field requires updating this document first.
* School operations (docs/SCHOOL_OPERATIONS.md) store only membership, group, session date
  and title, and an attendance status (present, absent, excused) with who marked it. No
  reasons for absence, notes, location or device data. Records are visible to the group's
  teacher, the organization's admins and the student; platform admins see counts only, but
  a platform admin can make themself an org admin (audited `org.admin_assigned`) and then
  read records, so this is a procedural control, not a technical one (audit F-10).
  Organizations are isolated server-side: a non-member gets `404` for every org route, and
  nested IDs are checked against the org in the path (tests in `tests/test_orgs.py`).
* Retention: attendance older than `ATTENDANCE_RETENTION_DAYS` (default 365) is deleted by
  `python -m scripts.purge_retention` (run it on a schedule in a deployment). Leaving or
  being removed from a group deletes that student's attendance there. Join codes are stored
  hashed, expire after `JOIN_CODE_TTL_DAYS` (default 7) and guessing is rate-limited.
* Consent: `users.terms_version` and `users.terms_accepted_at` record which version of the
  documents was accepted at registration, and when. Both are NULL for accounts created
  before migration 0011. No age or birth date is collected; the legal basis for minors is an
  open owner and lawyer decision (docs/LEGAL_REVIEW.md).
* Password reset tokens: `password_reset_tokens` holds a SHA-256 of the token, the account,
  who issued it, and when it was created, expires and was used. Rows go with the account.
* Data export: `GET /users/me/export` returns the signed-in user's own profile, sessions
  (without tokens), submissions with source code, hint and editorial history, self-reports,
  assistant metadata, memberships and attendance. Limited to 5 an hour.
* Account deletion: `POST /users/me/delete` needs the password and removes the account and
  everything keyed to it (database cascades: sessions, submissions, hint and editorial
  history, self-reports, assistant metadata, memberships, attendance). Links on shared
  records are set to NULL. Audit rows keep the account's id, and an IP address where one
  was recorded; how long they stay is an open decision (docs/LEGAL_REVIEW.md). Platform admins and the last
  admin of an organization cannot delete themselves. Database backups and provider logs
  keep deleted data until they expire.

## Secrets

| Variable | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | api | |
| `SESSION_COOKIE_SECURE` | api | `true` in any deployed environment; the API refuses to start with `APP_ENV=production` and `false` |
| `SEED_PASSWORD` | seed script | dev only; seed refuses production. There are no default admin credentials: the first admin registers and is promoted with `scripts.promote_admin` (audited) |
| `POSTGRES_PASSWORD` | db, api (compose) | generated per environment; letters and digits |
| `JUDGE0_URL`, `JUDGE0_AUTH_TOKEN` | api *(P3)* | optional |
| `RATE_LIMIT_KEY_SECRET` | api | required with `RATE_LIMIT_BACKEND=postgres`; 32+ letters, digits, `-` or `_`; rotating it only restarts the current windows |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_EFFORT`, `AI_*` limits | api | optional; see `.env.example` |

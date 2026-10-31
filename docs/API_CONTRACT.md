# API Contract

Base path: `/api/v1`. JSON only. The machine-readable contract is the generated
OpenAPI document at `apps/web/src/lib/api/openapi.json` (regenerate with
`uv run python scripts/export_openapi.py` in `apps/api`). This file is the
human-readable summary and the place where **planned** endpoints are agreed before
anyone implements them. If this file and the OpenAPI document disagree for a shipped
endpoint, the OpenAPI document wins and this file has a bug.

## Conventions

* **Auth:** cookie `ks_session` (HttpOnly, `SameSite=Lax`, `Secure` outside local dev).
* **CSRF:** every `POST`/`PUT`/`PATCH`/`DELETE` made with a session must send header
  `X-CSRF-Token` equal to the `ks_csrf` cookie (readable by JS) issued at login.
  Missing/wrong → `403 csrf_failed`. Login and register are exempt (no session yet).
* **Errors:** every non-2xx response has the shape

  ```json
  { "error": { "code": "snake_case_code", "message": "Human readable", "details": null } }
  ```

  | Status | Codes |
  |---|---|
  | 400 | `bad_request` (also a value the database cannot store, e.g. NUL or an out-of-range offset), `cannot_modify_self` |
  | 401 | `not_authenticated`, `invalid_credentials` |
  | 403 | `forbidden`, `csrf_failed`, `account_disabled`, `editorial_locked`, `reviewer_is_author`, `assistant_level_locked` |
  | 404 | `not_found`, `invalid_join_code` |
  | 409 | `email_taken`, `hint_out_of_order`, `publish_blocked`, `not_published`, `last_org_admin` |
  | 422 | `validation_error` (`details` = list of `{loc, msg, type}`), `source_too_large`, `not_a_member`, `author_attestation_required` |
  | 429 | `rate_limited` (`Retry-After` header set) |
  | 500 | `internal_error` (`details.request_id` equals the `X-Request-ID` response header) |
  | 503 | `service_unavailable` (database unreachable, `Retry-After` set; on Vercel also a request without exactly one client IP in `x-forwarded-for`); `judge_unavailable`, `ai_unavailable` *(planned)* |

* **Request id:** every response carries `X-Request-ID`. A client-supplied `X-Request-ID`
  matching `[A-Za-z0-9._-]{1,64}` is reused, otherwise the API generates one. It appears in
  the API's structured logs; quote it in bug reports.
* **Pagination:** `?limit=` (1–100, default 20) and `?offset=` (≥0). Response:
  `{ "items": [...], "total": n, "limit": l, "offset": o }`.
* **IDs** are UUID strings. **Timestamps** are ISO 8601 UTC.
* **Locales:** `"ky" | "ru" | "en"`.

## Shared schemas

```ts
type Role = "student" | "teacher" | "admin";
type Locale = "ky" | "ru" | "en";

// Returned only to the user themself.
interface UserPrivate {
  id: string; email: string; display_name: string; role: Role;
  preferred_locale: Locale; created_at: string;
}
// Returned to admins.
interface UserAdminView extends UserPrivate { is_active: boolean; is_synthetic: boolean; }
interface SessionInfo { user: UserPrivate; csrf_token: string; expires_at: string; }
interface AuditLogEntry {
  id: string; actor_id: string | null; action: string; target_type: string | null;
  target_id: string | null; details: Record<string, unknown>; created_at: string;
}
```

`password_hash`, session tokens and IP addresses of other users are never serialized.

## Phase 1 — shipped

| Method & path | Auth | Body → Response | Rules |
|---|---|---|---|
| `GET /health` | public | → `{status: "ok", database: "ok" \| "unavailable"}` | Liveness: always 200 while the process answers. |
| `GET /health/ready` | public | → `{status: "ok", database: "ok"}` | Readiness: `503 service_unavailable` (`Retry-After`) when the database does not answer. The judge and the AI provider never affect it. Used by the Compose health checks. |
| `POST /auth/register` | public | `{email, password, display_name, preferred_locale?, accepted_terms: true}` → `201 SessionInfo` + cookies | Always creates a `student`. Password 10–128 chars. `display_name` 1–80 chars, trimmed. Email normalized to lower case. `accepted_terms` must be `true`; the accepted document version is stored. `409 email_taken` (changes nothing). Rate limits: 10 / hour / IP, 5 / hour / email. |
| `POST /auth/login` | public | `{email, password}` → `200 SessionInfo` + cookies | Unknown email and wrong password are indistinguishable (`401 invalid_credentials`, same timing path). Inactive → `403 account_disabled`. Rate limit: 10 / 15 min per IP+email and 50 / 15 min per IP. |
| `POST /auth/logout` | session + CSRF | → `204` | Revokes the session, clears cookies. |
| `GET /auth/session` | session | → `200 SessionInfo` | `401 not_authenticated` if missing/expired/revoked. Re-issues the CSRF token. |
| `POST /auth/password` | session + CSRF | `{current_password, new_password}` → `204` | Wrong current → `401 invalid_credentials`. Revokes all *other* sessions. Audited. Rate limited like login. |
| `POST /auth/logout-all` | session + CSRF | → `204` | Revokes every session of the user, clears cookies. |
| `POST /auth/password/reset` | public | `{token, new_password}` → `204` | Spends a single-use token and revokes every session; does not sign in. Any bad token → `400 invalid_reset_token`. Rate limit: 10 / 15 min / IP. |
| `GET /users/me/export` | session | → `AccountExport` | The caller's own data, no hashes or tokens. Rate limit: 5 / hour / user. |
| `POST /users/me/delete` | session + CSRF | `{password}` → `204` | Permanent; clears cookies; audited. Wrong password → `401 invalid_credentials`. `409 admin_cannot_delete_self`, `409 last_org_admin`. Rate limit: 5 / 15 min / user. |
| `POST /admin/users/{id}/password-reset` | admin + CSRF | → `200 PasswordResetIssued {token, expires_at}` | Token shown once, stored hashed, audited; cancels earlier unused tokens. Self → `400 cannot_modify_self`; another admin → `403 forbidden`; disabled account → `409 account_disabled`. |
| `GET /users/me` | session | → `UserPrivate` | |
| `PATCH /users/me` | session + CSRF | `{display_name?, preferred_locale?}` → `UserPrivate` | Cannot change role or email here. |
| `GET /admin/users` | admin | `?q=&role=&limit=&offset=` → `Page<UserAdminView>` | `q` matches email or display name (case-insensitive substring). |
| `PATCH /admin/users/{id}` | admin + CSRF | `{role?, is_active?}` → `UserAdminView` | Admin cannot change their own role or deactivate themself (`400 cannot_modify_self`). Deactivation revokes all sessions. Audited (`user.role_changed`, `user.deactivated`, `user.reactivated`). |
| `GET /admin/audit-logs` | admin | `?action=&limit=&offset=` → `Page<AuditLogEntry>` | Newest first. Read-only. |

## Phase 2 — shipped (Olympiad core, proposal 0001 C1–C8)

Content endpoints accept `?locale=ky|ru|en` (default: the user's preferred locale, else
`ru`). Missing translations fall back ru → en → ky and the response says so
(`is_fallback`). Problems that are not `published` are visible only to admins and to their
`created_by` user; for everyone else they are indistinguishable from missing (`404`).

| Method & path | Auth | Body → Response | Rules |
|---|---|---|---|
| `GET /topics` | public | → `TopicOut[]` | Localized name/summary, `stage_count`, `prerequisites[] {stage, requires, requires_stage, strength}`. |
| `GET /tags` | public | → `TagOut[]` | |
| `GET /problems` | public | `?q=&topic=&tag=&band=&status=&sort=&locale=&limit=&offset=` → `Page<ProblemSummary>` | `sort=band` (default: band, then slug) or `newest`. `q` matches slug or any title (wildcards literal). `status` filter only for admins. `my_status`: `solved`/`attempted`/`self_reported`/`null`. |
| `GET /problems/{slug}` | public | → `ProblemDetail` | Statement, limits, `samples[]` (sample tests only — hidden tests never leave the API), `groups[]` (subtasks), band + `difficulty_status` + rationale, `license`, `attribution_md`, `authors_md`, `generated_with_ai`, `source {title, author, url, license}` (null for original problems), hint/editorial state for the caller, `judge_available`, and `review {reviewed_locales, rights_verified_at, author_linked, editorial_locales, reviewed_editorial_locales, hint_review: [{locale, total, reviewed}], publish_blockers, blocking_items: [{code, item, locale, ordinal}]}` for admins only. The review counts and `blocking_items` come from the publication gate (see `publish`), so a review without matching audit evidence is not shown as reviewed. |
| `GET /problems/{slug}/hints` | session | → `{total, revealed: HintOut[]}` | Only hints this user already revealed. |
| `POST /problems/{slug}/hints/{ordinal}/reveal` | session + CSRF | → same | In order only (`409 hint_out_of_order`); idempotent; recorded per user, problem and ordinal (all locales). |
| `GET /problems/{slug}/editorial` | session | → `EditorialOut` | `403 editorial_locked` unless unlocked. An accepted submission unlocks it automatically (`unlock_reason: "solved"`). `404` if none. |
| `POST /problems/{slug}/editorial/unlock` | session + CSRF | `{reason: "gave_up"}` → `EditorialOut` | Records the give-up; idempotent. Hint reveals, unlocks and self-reports share a limit of 60 writes/min/user. |
| `POST /problems/{slug}/self-report` | session + CSRF | → `{problem_slug, created_at}` | "Solved offline" claim; idempotent; never counted as evidence. |
| `POST /submissions` | session + CSRF | `{problem_slug, language: "cpp"\|"python", source_code}` → `201 SubmissionDetail` | `source_code` ≤ 64 KiB in UTF-8 (`422 source_too_large`). Rate limit 6/min/user. Snapshots `hints_revealed_count`. Without a judge: `state: "not_judged"`, `verdict: null`. |
| `GET /submissions` | session | `?problem=&limit=&offset=` → `Page<SubmissionSummary>` | Own submissions only; no source code in the list. |
| `GET /submissions/{id}` | owner / admin | → `SubmissionDetail` | Others get `404`. Includes `group_results[]` (per subtask, never per hidden test). |
| `GET /me/progress` | session | → `ProgressOut` | Per topic stage: `unaided_by_band`, `with_hints`, `demonstrated`; `hint_reliance_30d`, `median_attempts_to_accept`, `demonstrated_topics/total_topics`, `pending_not_judged`, `self_reported[]`, `demonstrated_threshold`, `judge_available`. Judged evidence only. |
| `PATCH /admin/problems/{slug}/difficulty` | admin + CSRF | `{band, rationale (10–500)}` → `ProblemDetail` | Sets `difficulty_status: "reviewed"`. Author → `403 reviewer_is_author`. Audited `problem.difficulty_changed` (`from`, `to`, `status_from`, `rationale`); the event also carries `spec_sha256` (time and memory limits, scoring, topics with stages, tags), which the review confirms too. The publication gate uses the latest such event as the record of who confirmed the current band, and only while `spec_sha256` matches the problem (older entries without it do not count). A re-import keeps the reviewed band (including a reviewer's correction), rationale and evidence while the file's band is still the one the review started from and the limits, scoring, topics and tags are unchanged; otherwise it resets to the file's band, `author_estimate` and the file's rationale. |
| `POST /admin/problems/{slug}/translations/{locale}/review` | admin + CSRF | → `ProblemDetail` | Not by the author. Audited `problem.translation_reviewed` with `locale` and `statement_sha256` (SHA-256 of the statement's title, legend, input, output and notes as reviewed). |
| `GET /admin/problems/{slug}/editorials` | admin | → `AdminEditorialOut[]` (`locale`, `body_md`, `body_sha256`, `reviewed`, `reviewed_at`) | Reviewer read of every editorial. Never the student unlock flow: records no unlock, give-up or progress. `404` for an unknown problem. |
| `POST /admin/problems/{slug}/editorials/{locale}/review` | admin + CSRF | `{attest_not_author?: bool}` → `ProblemDetail` | `403 reviewer_is_author` for the linked author; with no linked author `attest_not_author: true` is required (`422 author_attestation_required`). `404` for an unknown problem or a language without an editorial; an unsupported language is `422`. Repeating it re-stamps the review. Does not change publication status; required to publish (see `publish`). Audited `problem.editorial_reviewed` with `locale`, `body_sha256`, `author_identity`, `attested_not_author`. A re-import keeps the review only if the editorial text is unchanged. |
| `GET /admin/problems/{slug}/hints` | admin | → `AdminHintOut[]` (`locale`, `ordinal`, `body_md`, `body_sha256`, `reviewed`, `reviewed_at`), by language then ordinal | Reviewer read. Never the student reveal flow: records no reveal or progress. `404` for an unknown problem. |
| `POST /admin/problems/{slug}/hints/{locale}/{ordinal}/review` | admin + CSRF | `{attest_not_author?: bool}` → `ProblemDetail` | One hint. Same author rule as editorials (`403 reviewer_is_author`; `422 author_attestation_required` with no linked author). `404` for an unknown problem or no hint at that position; `422` for an unsupported language or an ordinal below 1. Audited `problem.hint_reviewed` with `locale`, `ordinal`, `body_sha256`, `author_identity`, `attested_not_author`. Repeating it re-stamps the review. Publication status unchanged; required to publish. |
| `POST /admin/problems/{slug}/hints/{locale}/review` | admin + CSRF | `{attest_not_author?: bool}` → `ProblemDetail` | Every hint of one language, all or nothing in one transaction, one `problem.hint_reviewed` audit event per hint. `404` when the language has no hints. Otherwise as above. |
| `POST /admin/problems/{slug}/rights/verify` | admin + CSRF | `{attest_not_author?: bool}` → `ProblemDetail` | A human confirms the right to publish under the declared license. `403 reviewer_is_author` when the caller is the linked author (`created_by`). With no linked author (`author_linked: false`, e.g. imported content), `attest_not_author: true` is required, else `422 author_attestation_required`; this is an attestation, not an identity check. Audited `problem.rights_verified` with `author_identity` (`linked`/`unlinked`), `attested_not_author`, `verified_at` (the stored confirmation time) and `provenance_sha256` (origin, license, attribution, authors, AI flag, source, linked author). Cleared by a re-import that changes provenance or the linked author. |
| `POST /admin/problems/{slug}/publish` | admin + CSRF | → `ProblemDetail` | The only way to publish; the publication gate runs inside the service, before any write. `409 publish_blocked` with `details` = every distinct blocker code at once, in this order: `already_published`, `missing_license_or_attribution`, `provenance_incomplete` (license or `[source]` fails the content policy in `content.py`), `rights_not_verified` (also when the stored confirmation does not match the latest `problem.rights_verified` event's verifier, `verified_at` and `provenance_sha256`), `author_review_conflict` (rights, a statement, editorial, hint or the difficulty was confirmed or reviewed by the now-linked author), `statement_unreviewed`, `editorial_unreviewed`, `hint_unreviewed` (every existing statement language, editorial and hint needs a valid review; none needed where none exists), `no_verified_reference_solution`, `difficulty_not_reviewed` (also when the latest `problem.difficulty_changed` event is for another band), `no_samples`, `approver_is_author`. A review is valid only when its fields are set, it is not by the linked author, and the latest matching audit event (`problem.translation_reviewed` / `editorial_reviewed` / `hint_reviewed` for that locale and ordinal) has the same actor and the SHA-256 of the current text (`statement_sha256` or `body_sha256`). Reviews audited before these hashes existed do not count and must be repeated. The gate and the status change run under the problem row lock (`SELECT ... FOR NO KEY UPDATE`), which every review, rights, difficulty and import write also takes first, so a concurrent import is either refused (the problem is published) or judged by the gate after it commits. An admin write that cannot get the lock within 5 s fails with `503 service_unavailable` (`Retry-After`) before changing anything; retrying is safe. The per-item list (with locale and ordinal) is `review.blocking_items` on `GET /problems/{slug}`. A refused publish changes nothing and writes no audit entry. Audited `problem.published`. |
| `POST /admin/problems/{slug}/unpublish` | admin + CSRF | → `ProblemDetail` | Back to `in_review`. `409 not_published`. Not gated: always allowed for a published problem. A problem published before the gate stays published until an admin unpublishes it. Audited. |

Submission `state`: `queued | judging | done | not_judged | judge_error`.
`verdict` (only when `done`): `accepted | partial | wrong_answer | time_limit |
memory_limit | runtime_error | compile_error`.

Deferred from the earlier plan: problem authoring over HTTP (`POST/PATCH /problems`)
moves to Phase 4 teacher tools; until then content arrives through
`content/` + `scripts/import_content.py` with pull-request review.

## AI assistant — shipped, optional (proposal 0004)

Off unless `LLM_PROVIDER` and `LLM_API_KEY` are set; when off, requests return the
authored hints (`mode: "fallback"`, `outcome: "not_configured"`).

| Method & path | Auth | Body → Response | Rules |
|---|---|---|---|
| `GET /assistant/status` | session | → `{enabled, model_id, prompt_version, daily_limit, used_today, reliable_locales}` | |
| `POST /problems/{slug}/assistant` | session + CSRF | `{level: 1-4, question?: ≤500, submission_id?}` + `?locale=` → `AssistantAnswer` | Published problems only (`404` otherwise). Level 4 needs a solve or give-up (`403 assistant_level_locked`). `submission_id` must be the caller's own for this problem (`404`). 6/min/user (`429`). Daily quota, global cap and monthly budget return a fallback, not an error; every request that reached the provider counts (withheld answers and provider failures included). |

`AssistantAnswer`: `mode` (`ai` \| `fallback`), `outcome` (`ok`, `not_configured`,
`quota_exceeded`, `budget_exhausted`, `provider_<kind>`, `invalid_output`, `code_withheld`,
`editorial_overlap`), `grounded[] {text, sources[] {id, kind, label}}`, `suggestion`
(AI-generated, unreviewed), `insufficient_sources`, `fallback_hints[]` (authored hints, recorded
as normal reveals), `language` + `language_note`, `flags[]`, `model_id`, `prompt_version`,
`used_today`, `daily_limit`.

**Intentional change to an existing field:** `hints_revealed_count` on submissions created
after an AI answer for the same problem includes those answers, so AI-assisted solves are
never counted as unaided. No change when the assistant is unused.

## School operations — shipped: club attendance (docs/SCHOOL_OPERATIONS.md)

Every `/orgs/{org_id}/...` route requires membership in that organization; otherwise `404`
(never `403`, so IDs of other organizations are not confirmed). Nested IDs must belong to the
organization in the path (`404`). A visible resource the caller may not change returns `403`.
"Manage a group" means: org admin, or the teacher who leads that group.

| Method & path | Who | Body → Response | Rules |
|---|---|---|---|
| `GET /admin/orgs` | platform admin | → `OrgAdminView[]` | Counts only, never rosters |
| `POST /admin/orgs` | platform admin + CSRF | `{name, kind: school\|club\|other}` → `OrgAdminView` (201) | Audited `org.created` |
| `POST /admin/orgs/{org_id}/admins` | platform admin + CSRF | `{email}` → 204 | Audited `org.admin_assigned` |
| `GET /orgs` | session | → `OrgOut[]` | Only the caller's memberships |
| `GET /orgs/{org_id}` | member | → `OrgOut` (`my_role`) | |
| `GET /orgs/{org_id}/members` | org admin | → `MemberOut[]` | |
| `POST /orgs/{org_id}/members` | org admin + CSRF | `{email, role: teacher\|org_admin}` → `MemberOut` (201) | Existing account only; demoting the last org admin → `409 last_org_admin`; audited |
| `DELETE /orgs/{org_id}/members/{user_id}` | org admin + CSRF | → 204 | Deletes their group memberships and attendance here; `409 last_org_admin`; audited |
| `GET /orgs/{org_id}/groups` | member | → `OrgGroupOut[]` | Staff who manage: all they manage; others: groups they belong to |
| `POST /orgs/{org_id}/groups` | teacher, org admin + CSRF | `{name, kind: class\|club}` → `OrgGroupOut` (201) | Creator becomes the lead |
| `GET/PATCH/DELETE /orgs/{org_id}/groups/{group_id}` | visible / manage / manage | `{name}` for PATCH | Delete cascades sessions and attendance; audited |
| `POST /orgs/{org_id}/groups/{group_id}/join-code` | manage + CSRF | → `{code, expires_at}` | 8 chars, 7 days; stored hashed; a new code replaces the old one |
| `POST /groups/join` | session + CSRF | `{code}` → `{org_id, group_id, group_name}` | `404 invalid_join_code`; 10/h/user, 30/h/IP (`429`) |
| `GET /orgs/{org_id}/groups/{group_id}/members` | manage | → `GroupMemberOut[]` | |
| `DELETE /orgs/{org_id}/groups/{group_id}/members/{user_id}` | manage, or the member themself + CSRF | → 204 | Deletes that member's attendance in the group; audited |
| `GET /orgs/{org_id}/groups/{group_id}/sessions` | visible | → `SessionOut[]` | Counts only for managers; members get `my_status` only |
| `POST /orgs/{org_id}/groups/{group_id}/sessions` | manage + CSRF | `{starts_at, title?: ≤80}` → `SessionOut` (201) | |
| `DELETE /orgs/{org_id}/groups/{group_id}/sessions/{session_id}` | manage + CSRF | → 204 | |
| `GET /orgs/{org_id}/groups/{group_id}/sessions/{session_id}/attendance` | manage | → `AttendanceRow[]` | One row per current member, `status` null when unmarked |
| `PUT /orgs/{org_id}/groups/{group_id}/sessions/{session_id}/attendance` | manage + CSRF | `{records: [{user_id, status: present\|absent\|excused}] (1–500)}` → `AttendanceRow[]` | Upsert; `422 not_a_member` for non-members |
| `GET /orgs/{org_id}/groups/{group_id}/summary` | manage | → `GroupSummary` | Aggregates only |
| `GET /orgs/{org_id}/groups/{group_id}/attendance.csv` | manage | → `text/csv` attachment | Cells escaped against formula injection; audited `report.exported` |
| `GET /me/attendance` | session | → `MyAttendanceRow[]` | The caller's own records only |

## Phase 3+ — sketched, to be specified before implementation

* P3: `GET /judge/status` (`available`/`unavailable`, languages); submission polling;
  re-queue of `not_judged` submissions when a judge becomes available.
* P4: `/problem-sets`, `/learning-paths`, `/assignments` (proposal 0002).
* P5: `/orgs/{org_id}/...` — every route is nested under an organization and checks
  membership server-side; reports export CSV with aggregates only.

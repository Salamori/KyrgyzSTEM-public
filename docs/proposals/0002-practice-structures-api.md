# Proposal 0002 (revision 2) — Teaching: organizations, lessons, authoring, problem sets, learning paths, contests

> **Historical design document.** It records decisions made at the time. Counts of problems and tests are out of date, and this public repository ships only the synthetic `sample-sum` package.

Status: **Proposed — needs technical-lead approval. Nothing here is implemented.**
Baseline: commit `c664409` (Phase 2). Date: 2026-10-24.
Supersedes revision 1 (never committed). Builds on proposal 0001 (approved C1–C8).

## 0. Summary

Revision 1 left six gaps. Revision 2 closes them and splits the work into milestones so
that **nothing that needs a real judge is shipped or shown before the judge exists**:

| Milestone | Content | Needs judge? |
|---|---|---|
| **4A** | Organizations, memberships, classes, join codes, teacher scope | No |
| **4B** | Lessons: model, content import, review/publish, pages | No |
| **4C** | Practice sets, checkpoints, assignments, learning paths (completion "pending check" without a judge) | No (results pending) |
| **4D** | Teacher problem and lesson authoring (drafts, review; **publish blocked until the judge verifies the reference solution**) | For publishing |
| **Phase 3** | Judge0 integration (separate proposal 0003) | — |
| **4E** | Timed contests, scoreboards, "verified" mastery, retention, mixed-set recognition | **Yes** |

No existing table loses data, no endpoint is removed or renamed, and the Phase 2 API keeps
its behaviour except for the intentional changes listed in §8.

## 1. The six gaps and how they are resolved

| # | Gap in revision 1 | Resolution in revision 2 |
|---|---|---|
| G1 | Teacher problem authoring missing | §5 — authoring API in the Olympiad module (milestone 4D), same validation and publish gate as repository content; reference solutions are untrusted code and are verified **only by the judge**. |
| G2 | No lesson content model | §4 — `lessons` + translations + linked worked examples, imported from `content/lessons/` and authorable in 4D, same review/publish gate. |
| G3 | Metrics/mastery not scheduled | §7 — explicit definitions: *practised* (Phase 2 `demonstrated`, with the editorial fix), *verified* (new), *retained*, *mixed-set recognition*; scheduled in 4E behind the judge. |
| G4 | Module boundary contradiction | §2 — no new dependency from `olympiad` to `teaching`/`orgs`: contest/checkpoint submissions go through teaching routes; hint/editorial restrictions use a protocol owned by `olympiad` and implemented by `teaching` (dependency inversion), wired in `main.py`; the set↔submission link lives in a teaching-owned table. |
| G5 | Only published problems can be assigned, and none are published | §6.1 — the rule is kept and made explicit at three points (add item, assign, start); unpublishing later marks items *withdrawn*. Milestones 4C/4E are useless until humans review and publish problems: listed as a dependency (§9). |
| G6 | Contests/paths look functional without a judge | §9 — contest endpoints ship only in 4E (after Phase 3); checkpoints and paths show "pending check"; no scoreboard or completion is ever computed from `not_judged` or self-reported work. |

## 2. Module boundaries (resolves G4)

Current rule (`docs/ARCHITECTURE.md`): `olympiad` must not import `orgs`; `core` imports no
module. Revision 2 keeps it and makes the extension mechanism explicit:

```
core ◀── users ◀── audit
  ▲        ▲
  │        ├── olympiad  (problems, lessons, hints, editorials, submissions, progress,
  │        │              authoring; defines protocol PracticeGuard)
  │        ├── orgs      (organizations, memberships, classes)
  │        └── teaching  (problem sets, assignments, paths, contests, participations;
  │                       imports olympiad + orgs; implements PracticeGuard)
  └── judge (JudgeClient protocol; NoJudge now, Judge0 in Phase 3)

main.py composes: app.state.practice_guard = teaching.guard.TeachingGuard()
```

Allowed imports: `olympiad → core, users, audit, judge`; `orgs → core, users, audit`;
`teaching → core, users, audit, olympiad, orgs, judge`. Never `olympiad → teaching|orgs`.
An import-linter style test (`tests/test_architecture.py`) enforces this by scanning imports.

The four places where the domains touch:

1. **Submissions inside a set.** `POST /problem-sets/{id}/submissions` (teaching) checks
   participation, the time window and that the problem is an item, then calls the public
   Olympiad service `create_submission(..., context="checkpoint"|"contest")`. The link is
   stored in teaching's own table `problem_set_submissions(submission_id PK, problem_set_id)`.
   **No column is added to `submissions`** (revision 1 proposed one; dropped).
2. **Hint/editorial restriction during a checkpoint or contest window.** `olympiad/guards.py`
   defines `PracticeGuard.restriction(db, user, problem) -> str | None`. The default
   implementation returns `None` (exactly Phase 2 behaviour). Teaching provides an
   implementation that returns `"in_checkpoint"`/`"in_contest"` while that user has an active
   window containing that problem. Olympiad routes call the guard; they never import teaching.
3. **Teacher access to student submissions.** Exposed only through teaching routes
   (`GET /problem-sets/{id}/submissions…`), which authorize the teacher and then read through
   Olympiad service functions. **`GET /submissions/{id}` stays owner/admin only** (unchanged).
4. **Metrics.** Olympiad metrics read `submissions.context` (practice/checkpoint/contest),
   which it owns. Mixed-set recognition needs `show_topics` from teaching, so it is computed
   in teaching and exposed by a teaching route (§7.4), not by `/me/progress`.

`docs/ARCHITECTURE.md` gets the diagram and the rule "extension points are protocols defined
by the lower module and implemented by the higher one, wired in `main.py`".

## 3. Data model (additive migrations, one per milestone)

| Migration | Tables / changes |
|---|---|
| 0004 (4A) | `organizations(id, name, kind school\|club\|other, created_at)`; `org_memberships(org_id, user_id, role org_admin\|teacher\|student)` unique `(org_id, user_id)`; `classes(id, organization_id, name, join_code_hash, join_code_expires_at)`; `class_members(class_id, user_id, joined_at)` |
| 0005 (4B) | `lessons(id, slug, topic_id, stage, status, created_by, origin, license, attribution_md, authors_md, generated_with_ai, published_at, published_by)` + `lesson_translations(lesson_id, locale, title, body_md, reviewed_by, reviewed_at)` + `lesson_problems(lesson_id, problem_id, ordinal, role worked_example\|try_it)` + `lesson_reads(user_id, lesson_id, read_at)` |
| 0006 (4C) | `problem_sets(id, owner_id, title, mode practice\|checkpoint\|contest, scoring ioi\|icpc, show_topics, duration_min, starts_at, ends_at, created_at)` (CHECK `ends_at > starts_at`); `problem_set_items(problem_set_id, problem_id, ordinal, label)`; `participations(problem_set_id, user_id, started_at, deadline_at, finished_at, is_virtual)`; `problem_set_submissions(submission_id PK, problem_set_id)`; `learning_paths(id, slug, owner_id, status)` + translations; `learning_path_steps(path_id, ordinal, kind lesson\|practice\|checkpoint\|contest, lesson_id, problem_set_id, min_accepted)`; `assignments(id, class_id, target_type set\|path, problem_set_id, path_id, assigned_by, due_at)`; **widen** `submissions.context` CHECK to add `checkpoint` |
| 0007 (4D) | `problems.submitted_for_review_at`; `authoring_limits` are config, not tables |
| 0008 (4E) | `contest_outages(problem_set_id, started_at, ended_at)`; `participations.scoreboard_opt_out` |

Widening the `context` CHECK keeps all rows valid. Its downgrade refuses to run while
`checkpoint` rows exist (no silent data loss).

## 4. Lessons (resolves G2) — milestone 4B, Olympiad module

- One lesson per topic stage at first (more allowed). Body is Markdown with the same
  safe renderer as statements: idea, worked example(s) with explanation, common mistakes,
  "try it" links to published problems.
- Source: `content/lessons/<slug>/lesson.toml` + `body.<locale>.md`, imported by the
  existing importer (new `import_lesson`), verified for structure and links.
- Same provenance fields and **same publish gate as problems**: license + attribution +
  authors, ≥ 1 reviewed translation, approver ≠ author; linked problems must be published.
- Endpoints: `GET /lessons?topic=&stage=&locale=`, `GET /lessons/{slug}`,
  `POST /lessons/{slug}/read` (records a read; **not** evidence of skill),
  admin `POST /admin/lessons/{slug}/translations/{locale}/review`, `…/publish`, `…/unpublish`.
- Initial content: 8 stage-1 lessons for the topics the 12 problems cover, AI-drafted and
  marked for human review (needs your approval, decision D7).

## 5. Teacher problem authoring (resolves G1) — milestone 4D, Olympiad module

| Method & path | Who | Rules |
|---|---|---|
| `POST /problems` | teacher, admin | Creates a `draft` with `created_by` = caller. Metadata validated with the same rules as `content.py` (band, license allow-list, attribution, topics with exactly one primary, groups summing to 100). |
| `PATCH /problems/{slug}` | author (draft/in_review), admin | `409 problem_published` for published problems: unpublish first. |
| `PUT /problems/{slug}/translations/{locale}` | author, admin | Statement sections validated; **editing clears that translation's review** (same as the importer). |
| `PUT /problems/{slug}/hints/{locale}`, `PUT …/editorials/{locale}` | author, admin | Hint counts must match across locales. |
| `PUT /problems/{slug}/tests` | author, admin | JSON groups + tests; ≥ 1 sample; each input ≤ 4 MiB, request ≤ 16 MiB; recomputes `tests_hash`, which **invalidates** earlier verification. Generator-based large tests stay in the `content/` repository workflow. |
| `PUT /problems/{slug}/reference-solutions/{language}` | author, admin | Stored as untrusted text. **Never executed by the API.** Verified only by the judge (Phase 3) against every test; until then the publish gate reports `no_verified_reference_solution`. |
| `POST /problems/{slug}/submit-for-review` | author | `draft → in_review`; audited. |

Review and publishing are the existing admin endpoints, unchanged. Teachers see their own
drafts (already true via `created_by`). Authoring writes are rate-limited (30/min/user) and
every state change is audited. Lessons get the same authoring endpoints under `/lessons`.

## 6. Problem sets, assignments, learning paths — milestone 4C, teaching module

### 6.1 Only reviewed, published content can be assigned (resolves G5)

Checked at three points, each with a test:

1. **Adding an item** to a set or a lesson to a path: the problem/lesson must be `published`
   (`409 not_published`).
2. **Assigning** a set/path to a class: every item must still be published
   (`409 contains_unpublished` with the offending slugs).
3. **Starting** a participation or submitting through a set: withdrawn items are refused.

If an admin later unpublishes a problem (the existing endpoint, unchanged), the set shows the
item as *withdrawn*: it is excluded from completion and scoring, and the teacher sees a
notice. Past submissions stay in history.

### 6.2 Endpoints (all CSRF on unsafe methods; others' resources return 404)

| Method & path | Who | Notes |
|---|---|---|
| `POST/PATCH /problem-sets`, `POST /problem-sets/{id}/items` | teacher, admin (owner) | `mode` = `practice` or `checkpoint` in 4C; **`contest` is rejected until 4E** (`422`). |
| `GET /problem-sets/{id}` | owner, assigned students | Topics hidden when `show_topics=false`. |
| `POST /problem-sets/{id}/start` | assigned student | Creates a participation; checkpoint windows last `duration_min`. |
| `POST /problem-sets/{id}/submissions` | participant | Window and item checks, then Olympiad `create_submission(context=…)`. |
| `GET /problem-sets/{id}/submissions` | owner who teaches the class | Paginated, filter by student. Hint counts per decision D3. Never self-reports. |
| `POST /classes/{id}/assignments` | teacher of that class's org | `{target_type, id, due_at}`; audited. |
| `GET /me/assignments` | student | Own classes only. |
| `GET/POST/PATCH /learning-paths…`, `POST /learning-paths/{slug}/steps` | public read (published); teacher/admin write (owner) | Steps reference published lessons and sets; `contest` steps rejected until 4E. |
| `GET /me/learning-paths/{slug}` | student | Step states: `done`, `pending_check` (submitted, not judged), `todo`, `locked_recommendation` (prerequisite not yet practised; never blocks). |

Completion rules: lesson step → read recorded; practice step → `min_accepted` judged accepts;
checkpoint step → judged accepts made **inside the checkpoint window**. Without a judge,
practice and checkpoint steps stay `pending_check`; self-reports never complete a step.

### 6.3 Organizations and teacher scope — milestone 4A, orgs module

- Admin creates organizations and assigns `org_admin`/`teacher` memberships (audited).
- Students join a class with a **join code** generated by its teacher (stored hashed,
  expiring), instead of teachers adding students by email: no account enumeration and
  teachers never need students' emails. (Decision D2.)
- Authorization: global `teacher` role = may author and create sets; acting on a class requires
  `org_admin`/`teacher` membership in that class's organization. Every org-scoped route resolves
  the organization from the resource **and** checks membership; cross-org access returns 404.

## 7. Learning metrics and the definition of mastery (resolves G3)

All metrics use **judged** submissions only (`state = done`, verdict ≠ `compile_error`).
Self-reports, `not_judged` and `judge_error` never count.

**Unaided accept** (changed, §8 B3): accepted, `hints_revealed_count = 0` at submission time,
**and the editorial had not been unlocked by giving up before that submission.**

| Level (per topic stage *s*) | Definition | Shipped |
|---|---|---|
| **Practised** (`demonstrated` field, name kept for compatibility) | ≥ 2 distinct problems at stage ≥ *s* with an unaided accept | Phase 2 (+ B3 fix in 4C) |
| **Verified** (new `verified` field) | Practised **and** ≥ 1 of those accepts was made in a `checkpoint` or `contest` context (hints and editorial locked, time-boxed) **and** ≥ 1 is at or above the stage's reference band (stage 1 intro, 2 easy, 3 medium, 4 hard) | 4E (needs judge) |
| **Retained** (new `retained`: true/false/null) | An unaided accept on a problem of that topic stage the student had never attempted, ≥ 14 days after their first unaided accept at that stage; `null` until enough data | 4E |

**Mixed-set recognition** (teaching route `GET /me/recognition`): accept rate on problems met
in sets with hidden topics vs. shown topics, same band, ≥ 5 judged problems in each group,
else `null`. Shown as a trend, never as a grade. 4E.

**Teacher view**: per class, the distribution of practised/verified stages and per-assignment
completion. No rankings, no self-reports. Hint detail per decision D3.

Thresholds (2 problems, 14 days, 5 problems) are configuration constants, reported in the
response (`thresholds`), and remain provisional until pilot data exists (decision D4).
`/me/progress` gains `mastery_rule_version` so a future rule change is visible, not silent.

## 8. Preserving Phase 2: what does not change, and intentional changes

Unchanged: all Phase 2 tables' existing columns and rows; every Phase 2 endpoint path, request
and response field; visibility rules; the publish gate; `GET /submissions/{id}` access; the
importer and the 12 problems. All 132 API tests and 34 web tests must keep passing
**unmodified**, except the specific tests named below.

| # | Intentional change | Milestone | Tests required |
|---|---|---|---|
| B1 | Hint `GET`/`reveal` and editorial `GET`/`unlock` return `403 practice_restricted` (`details: "in_checkpoint"\|"in_contest"`) for a user with an active window on that problem | 4C | blocked inside the window; allowed before/after; other users unaffected; other problems unaffected; default guard keeps Phase 2 behaviour |
| B2 | `POST /submissions` (practice route) for a problem in the caller's active window returns `409 use_problem_set_route` | 4C | inside/outside window; other users unaffected |
| B3 | Accepts made after a give-up unlock no longer count as unaided (affects `demonstrated`, `unaided_by_band`, `hint_reliance_30d`) | 4C | gave-up-then-accept is "with help"; accept-then-give-up is unaided; existing progress tests unchanged |
| B4 | `/me/progress` gains `verified`, `retained`, `mastery_rule_version`, `thresholds` (additive) | 4E | field presence; null without data; never counts self-reports or unjudged |
| B5 | `submissions.context` accepts `checkpoint`; `SubmissionSummary` gains `context` (additive) | 4C | migration on populated DB; enum CHECK; downgrade refusal with checkpoint rows |
| B6 | Teachers gain authoring endpoints; their access to existing endpoints is unchanged | 4D | 401/403/404 matrix; teacher cannot edit another teacher's draft or any published problem |
| B7 | Unpublishing a problem used in assignments marks items *withdrawn* (unpublish itself unchanged) | 4C | withdrawn item excluded from completion; starting/submitting refused; history kept |

## 9. Dependencies on the real judge (resolves G6) and recommended order

| Feature | Without a judge | Needs the judge for |
|---|---|---|
| Practice sets, assignments | Work; submissions `not_judged`; completion `pending_check` | Completion |
| Checkpoints | Can be taken (hints/editorial locked); results `pending_check` | Results, *verified* mastery |
| Learning paths | Lessons complete on read; problem steps `pending_check` | Problem-step completion |
| Teacher authoring | Drafts, review, submit for review | Reference-solution verification → **publishing** |
| Contests | **Not shipped** (endpoints and UI arrive in 4E) | Everything |
| Verified / retained / recognition | Not computed | All |
| Accepted-solution statistics, difficulty calibration | Not computed | All (Phase 3) |

A deterministic `FakeJudge` lives **only in `tests/`**: it returns verdicts from a marker in
the source (e.g. `// fake-verdict: accepted`). It is never wired into the app or presented as
real execution; `get_judge()` returns `NoJudge` until Judge0 (Phase 3).

**Recommended order:** commit baseline (done, `c664409`) → **human review/publishing of
some of the 12 problems** (people, can start now) → **4A** → **4B** → **4C** → **4D** →
**Phase 3 (judge, proposal 0003)** → **4E** → rest of Phase 4 (AI hints) → Phase 5 →
Phase 6. Phase 3 may move earlier (right after 4A) if judging matters more to you than
lessons and paths; 4E must always come after it.

## 10. Testing requirements (every milestone)

For each new route: anonymous (401), wrong role (403/404), **other user**, **other teacher**,
**other organization** (404), invalid input (422), happy path. Plus: migrations up/down/up from
an empty database and over a database containing Phase 2 data; the architecture import test;
`/me/progress` regression tests; web unit tests for every new state (loading, empty, error,
unauthorized, success, `pending_check`, *withdrawn*); Playwright flows once CI runs Docker.

## 11. Decisions needed before approval

| # | Decision | Recommendation |
|---|---|---|
| D1 | Approve the module-boundary design in §2 (protocol + teaching-owned link table) | Approve |
| D2 | Students join classes by teacher-generated join code (not by email) | Approve |
| D3 | Teacher visibility of hints: per-submission counts for assigned work, or aggregates only | Per-submission counts for assigned work only; never practice outside assignments |
| D4 | Mastery thresholds (2 problems, 14 days, 5 problems) and the reference band per stage | Approve as provisional, revisit with pilot data |
| D5 | Contest scoreboards: names visible to same-contest participants with opt-out, or anonymous by default | Anonymous by default for minors; teachers see names |
| D6 | Who may review/publish: admins only, or a new global `reviewer` role | Add `reviewer` in 4D so admins are not a bottleneck |
| D7 | Initial lessons: 8 AI-drafted stage-1 lessons for human review, or wait for human authors | Draft them, clearly marked, never self-published |
| D8 | Order: Phase 3 after 4D (recommended) or right after 4A | Your call based on what teachers need first |
| D9 | B3 (editorial give-up excluded from unaided) is a behaviour change to Phase 2 progress | Approve; it fixes a real loophole |

# Release checklist

Use a copy of this list for every release (paste it into the release PR or issue and
tick the boxes there). A box is ticked only when someone **did** the thing, not when it
is expected to work. The first public release and the first pilot with real students
also need every item in "Release blockers".

## 0. Release blockers (open on 2026-10-25)

The platform must not hold real student accounts until each of these is fixed **with a
regression test** and the owner has approved the fix (docs/SECURITY_AUDIT.md).

- [ ] **F-01 (High): spoofable client IP defeats IP rate limits.** Mitigation is designed:
  Caddy overwrites `X-Forwarded-For`, and the API trusts it only from the web container
  (`deploy/`, docs/DEPLOYMENT.md §2). Logic checked locally with a stand-in proxy only.
  Closed when `deploy/smoke-test.sh` passes against the real staging deployment (and the
  CI `proxy-smoke` job is green).
- [ ] **F-02 (High with F-01): no per-account login throttle.** Fixed in code on 2026-10-31
  (30 attempts an hour, manual recovery links). Closed when the owner accepts the lockout
  trade-off and migration 0011 is applied.
- [ ] **F-05 (Medium): the "publisher is not the author" gate is weak for imported
  content.** Mitigated: import with `--author-email` to link the author's account; without a
  link, rights confirmation needs an audited "not an author" attestation (a statement, not an
  identity check). Owner decision: accept attestation, or require linked authors for every
  import before the pilot.
- [ ] Independent **human** security review of the code and of SECURITY_AUDIT.md (the
  audit was done by an AI session).
- [ ] Owner and lawyer decisions: minimum age and guardian consent, privacy notice, retention
  periods, with the pilot school (docs/LEGAL_REVIEW.md lists every decision; the published
  texts are unreviewed drafts).
- [ ] At least one problem reviewed and published by a human reviewer.
- [x] Project license confirmed by the owner: MIT, 2026-10-25 (see section 5).

## 1. Before the release

- [ ] All CI jobs green on the release commit: API, Web, OpenAPI contract drift, E2E,
  proxy smoke, dependency audit.
- [ ] Locally or in CI: `ruff`, `mypy`, `pytest` (only the known `xfail`), `npm run lint`,
  `typecheck`, `test`, `build`. Results pasted into the release PR exactly as reported.
- [ ] `pip-audit` and `npm audit` clean, or each finding documented (docs/DEPENDENCIES.md).
- [ ] Base images rebuilt (`docker compose build --pull`).
- [ ] New migrations reviewed: `downgrade()` works; long table locks avoided; data
  migrations tested on a restored copy on staging.
- [ ] `docs/API_CONTRACT.md`, README and the changelog section of the release notes match
  what ships; known limitations below updated.
- [ ] No secrets, `.env` files, dumps or real personal data in the diff
  (`git diff <previous-tag>..HEAD --stat`, and a scan of new files).
- [ ] Release tagged: `git tag -s vX.Y.Z` (or `-a`), `RELEASE_TAG=vX.Y.Z`.

## 2. Staging

- [ ] Deployed with the production compose file and the new tag.
- [ ] Restore drill: latest production dump restored on staging, smoke test passes
  (OPERATIONS.md §5). Staging copy destroyed afterwards.
- [ ] `bash deploy/smoke-test.sh https://STAGING` passes.
- [ ] Manual check on a phone-sized screen: sign in, problem bank, a problem page, submit,
  history, progress, sign out; teacher attendance; admin review panel.

## 3. Production

- [ ] Announced maintenance window to the school contact if migrations lock tables.
- [ ] `bash deploy/backup.sh`, and the dump copied off-host. Note its file name here:
  `________________`
- [ ] Previous `RELEASE_TAG` noted here for rollback: `________`; current alembic revision:
  `$COMPOSE exec api alembic current` → `________`
- [ ] `$COMPOSE build && $COMPOSE up -d --wait` with the new tag.
- [ ] `bash deploy/smoke-test.sh https://SITE` passes.
- [ ] Logs show no `level: error` for 15 minutes (`$COMPOSE logs --since 15m api`).

## 4. Rollback

Decide quickly: if the smoke test fails or errors appear and the cause is not obvious,
roll back first and investigate on staging.

**A. The release had no new migrations** (the alembic revision noted above is unchanged):

```bash
sed -i 's/^RELEASE_TAG=.*/RELEASE_TAG=<previous tag>/' deploy/.env.production
git checkout <previous tag>
$COMPOSE up -d --wait            # previous images are still on the host
bash deploy/smoke-test.sh https://SITE
```

**B. The release added migrations and they are reversible.** Downgrade **with the new
images** (only they know the new revisions), then switch back as in A:

```bash
$COMPOSE run --rm migrate alembic downgrade <revision noted before the release>
# then the three steps of A
```

**C. A migration is not reversible or data is damaged:** restore the pre-release dump
(`CONFIRM_RESTORE=yes bash deploy/restore.sh <dump>`) after switching back to the previous
tag as in A. Everything written since the backup is lost; tell the school contact.

After any rollback: note what happened in the release issue, and fix forward through a
normal PR.

## 5. License decision

**Decision (2026-10-25): the code stays under the MIT License**, confirmed by the owner
after the dependency license review in docs/DEPENDENCIES.md §4 (no conflict with MIT,
Apache-2.0 or AGPL-3.0; alternatives considered: Apache-2.0 for its explicit patent grant,
AGPL-3.0 to require hosted modifications to be published). Original educational content
keeps its own per-problem license: CC BY 4.0 only for original content whose rights a
reviewer has verified, never applied automatically (docs/CONTENT_GUIDE.md).

## 6. Known limitations (ship as known issues only if the owner accepts them)

Product scope:

- No judge: every submission is `not_judged`; progress has no evidence yet (Phase 3).
- This repository ships only the synthetic `sample-sum` package; no real problems. No
  Kyrgyz problem statements yet (the UI falls back to Russian and says so).
- No email verification. Password recovery is a link issued by an admin or an operator, not
  self-service, because no email provider is configured.
- Kyrgyz and Russian interface strings not reviewed by native speakers.
- Accessibility not formally reviewed; Playwright suite has never run green yet.

Security and operations (see docs/SECURITY_AUDIT.md for details):

- Rate limits are per process with the default `RATE_LIMIT_BACKEND=memory`: exactly one API
  worker and one `api` replica. `RATE_LIMIT_BACKEND=postgres` (required on Vercel) lifts this.
- F-06 remainder: AI quota and budget checks are check-then-act and can overshoot slightly.
- F-10: a platform admin can make themself an org admin and read rosters (audited,
  procedural control only).
- F-14: `email_taken` reveals that an account exists.
- F-15: a slow AI provider can tie up API worker threads (assistant is off by default).
- Web CSP allows `'unsafe-inline'` scripts (nonce CSP planned, AC-6.5).
- Single host, single database, no high availability; backups are nightly, so up to a
  day of data can be lost in a disaster.
- The production compose files have not been verified on a real deployment.

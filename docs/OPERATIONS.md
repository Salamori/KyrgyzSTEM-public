# Operations

Running a deployment: health, logs, failure behaviour, backups and restore, retention,
incidents. Setup is in [DEPLOYMENT.md](DEPLOYMENT.md). Commands below assume

```bash
export COMPOSE="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production"
```

## 1. Health checks

| Endpoint | Meaning | Used by |
|---|---|---|
| `GET /api/v1/health` | Liveness: always `200` while the process answers; body reports `database: ok \| unavailable` | Humans, debugging |
| `GET /api/v1/health/ready` | Readiness: `200` only if the database answers within 5 s, otherwise `503 service_unavailable` with `Retry-After: 30` | Compose health checks, external uptime monitor |

The judge and the AI provider are optional and never affect readiness. Compose health
checks: `db` (`pg_isready`), `api` (readiness), `web` (home page returns 200). Startup
order is db → migrate → api → web → caddy, each waiting for the previous one to be
healthy. `docker compose ps` shows the state.

## 2. Behaviour when a dependency fails

| Failure | What users see | What the system does |
|---|---|---|
| Database down or unreachable | Data views show their error state; API returns `503 service_unavailable` | Connection attempts time out after 5 s; readiness fails, Compose marks `api` unhealthy; the warning `database.unavailable` is logged; API recovers by itself once the database is back (`pool_pre_ping`) |
| API down | Pages load, data views show errors | web keeps serving; Compose restarts `api` (`restart: unless-stopped`) |
| No judge configured (the default: `JUDGE_LANGUAGES` empty) | "Automatic checking is not available yet"; submissions saved as `not_judged` | Nothing to do: this is the designed default |
| Judge0 down or not ready (judging on) | Submissions stay "Queued"; the page keeps checking for up to 10 minutes, then asks to come back later | The worker logs `judge.worker_not_ready` once, claims nothing and backs off; `--check ready` fails; queued jobs keep their attempts. Jobs in flight are released and retried, then end in `judge_error` ("Checking failed — not your fault") |
| Judge worker stopped or crashed | Submissions stay "Queued" | Restart it (`restart: unless-stopped`); jobs it held are requeued when their lease expires (`judge.swept`) |
| Worker started without `JUDGE0_URL` or language ids | Same as above | It exits with status 2 and logs `judge.worker_not_configured`; fix the settings |
| AI provider down, slow, over budget or not configured | Authored hints instead of AI answers | Fallback is recorded in `ai_requests` (metadata only); a provider timeout is 30 s, see audit F-15 for the thread-pool caveat |
| Unexpected bug | `500 internal_error` with a `request_id` | Traceback logged as `request.unhandled_error` with the same `request_id` |

## 3. Logs

The API writes one JSON object per line to stderr (`LOG_FORMAT=json`); Docker keeps them
with rotation (`json-file`, 5 × 20 MB per container in production).

```bash
$COMPOSE logs --since 1h api
$COMPOSE logs api | grep '"level": "error"'
$COMPOSE logs api | grep '"request_id": "<id from a bug report>"'
```

Fields: `ts`, `level`, `logger`, `event`, `request_id`, plus per event:

* `request`: `method`, `route` (the route **template**, e.g. `/api/v1/problems/{slug}`),
  `status`, `duration_ms`. Health probes are logged at debug level only.
* `request.unhandled_error`: `exc_type`, `traceback`.
* `database.unavailable`: `exc_type`.
* `api.configured` (at start): `app_env`, `judge`, `assistant`.
* Judge worker (`kyrgyzstem.judging`, fields `submission` (opaque id), `attempt`,
  `category`, `state`, `verdict`, `duration_ms` only): `judge.worker_started`,
  `judge.worker_ready`, `judge.worker_not_ready`, `judge.worker_backoff`,
  `judge.worker_stopped`, `judge.worker_not_configured`, and per job `judge.claimed`,
  `judge.released`, `judge.failed`, `judge.swept`, `judge.processed`. Never source code,
  tests, compiler output, exception messages or Judge0's URL or token.

**Never logged:** request or response bodies, query strings, raw paths, cookies, session
or CSRF tokens, passwords, IP addresses, emails, user ids. uvicorn's own access log is
switched off in code because it records client addresses and query strings, and SQL
errors are configured to hide bound parameters. Caddy has no access log for the same
reason. Keep it that way: adding a field to a log line that identifies a student needs a
change to docs/SECURITY.md first. Tests in `apps/api/tests/test_ops.py` check this.

Log retention is bounded by rotation (roughly a few days at low traffic). Logs never
leave the host unless you ship them somewhere, and then the same rules apply.

## 4. Backups

**What:** the PostgreSQL database is the only stateful component. `content/` is in git;
Caddy's certificates (`caddy_data`) are re-issued automatically if lost.

**How:** `deploy/backup.sh` (nightly from cron, and always before a release) runs
`pg_dump --format=custom --no-owner --no-privileges` inside the `db` container, verifies
the dump with `pg_restore --list`, writes it as `kyrgyzstem-<UTC time>.dump` with mode 600
into `BACKUP_DIR`, and deletes local dumps older than `KEEP_DAYS` (default 14).

**Off-host copies (owner):** a dump on the same disk as the database is not a backup.
Copy each dump to storage in another location, encrypted (e.g. `age` or `gpg` before
upload, or an encrypted bucket), with its own retention that matches the policy agreed
with the school. Dumps contain student accounts and attendance: treat them as personal
data, restrict access, and **never** commit one to git or attach one to an issue
(`.gitignore` excludes `backups/` and `*.dump`).

## 5. Restore

```bash
bash deploy/backup.sh                                   # a backup of the current state first
CONFIRM_RESTORE=yes bash deploy/restore.sh deploy/backups/kyrgyzstem-YYYYMMDDTHHMMSSZ.dump
bash deploy/smoke-test.sh https://YOUR-SITE
```

`restore.sh` verifies the dump, stops caddy, web and api, drops and recreates the
database, restores with `pg_restore --exit-on-error`, then starts the stack; `migrate`
upgrades an older dump to the running release's schema. Everything written after the
dump was taken is lost. Sessions in the dump are restored too; to force everyone to sign
in again afterwards, see section 7.

To restore **one table or a few rows** instead (e.g. an accidentally deleted group),
restore into a scratch database and copy what you need:

```bash
# single quotes: the variables are expanded inside the db container
$COMPOSE exec -T db sh -c 'createdb -U "$POSTGRES_USER" restore_check'
$COMPOSE exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d restore_check --no-owner' < deploy/backups/<file>.dump
# inspect with psql, copy the rows, then:
$COMPOSE exec -T db sh -c 'dropdb -U "$POSTGRES_USER" restore_check'
```

**Restore drill:** before each release, and at least quarterly, restore the latest
production dump on the **staging host** (never on a developer laptop), run the smoke test,
then destroy the staging copy. Note the date in the release record.

**Verified so far (2026-10-25):** the dump/restore commands, run directly against a local
PostgreSQL 16.2 holding the development database (migration 0005, synthetic seed, 12
imported problems): `pg_dump --format=custom --no-owner --no-privileges` → `pg_restore
--list` (30 table-data entries) → `createdb` + `pg_restore --no-owner --no-privileges
--exit-on-error` into a new database → exact row counts of all 30 tables identical,
`alembic_version` 0005. The Docker wrappers (`deploy/backup.sh`, `deploy/restore.sh`) have
not been run, because Docker was not available.

## 6. Scheduled jobs

| Job | Schedule | Command |
|---|---|---|
| Backup | nightly | `bash deploy/backup.sh` |
| Attendance retention | daily | `$COMPOSE exec -T api python -m scripts.purge_retention` (deletes attendance older than `ATTENDANCE_RETENTION_DAYS`, and expired `rate_limit_hits` rows) |
| Dependency audit | weekly (CI `audit.yml`) | see [DEPENDENCIES.md](DEPENDENCIES.md) |
| Restore drill | each release, at least quarterly | section 5 |

## 7. Incident basics

* **Find a request:** ask for the `request_id` (shown in the error, and the `X-Request-ID`
  header), then grep the logs for it.
* **Sign everyone out** (suspected session theft, after a restore):
  `$COMPOSE exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "UPDATE auth_sessions SET revoked_at = now() WHERE revoked_at IS NULL"'`
* **Disable an account:** admin UI → Users → deactivate (revokes its sessions, audited).
* **Rotate the database password:** change it in PostgreSQL (`ALTER ROLE ... PASSWORD`)
  and in `deploy/.env.production`, then `$COMPOSE up -d --wait`.
* **Rotate the AI key:** replace `LLM_API_KEY`, `$COMPOSE up -d api`; or empty
  `LLM_PROVIDER` to switch the assistant off immediately.
* **Personal-data incident:** stop the leak, preserve logs, inform the school partner and
  follow the notification duties agreed with them and required by Kyrgyz law (owner).

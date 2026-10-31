# Development guide

From a clean checkout to a running app, a database with synthetic data, and green
tests. Production is a different setup: see [DEPLOYMENT.md](DEPLOYMENT.md).

Everything here uses **synthetic data only**. Never load a real database dump, real
student names or real school records into a development environment.

## 1. Prerequisites

| Path | You need |
|---|---|
| A. Docker (simplest) | Docker Engine 24+ with the Compose plugin (`docker compose version`), git |
| B. Without Docker | Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22+ (CI and images use 24), PostgreSQL 16, git |

For the C++ checks in `tests/test_content.py` you also need `g++` or `clang++` on `PATH`,
or a compiler command in `KS_CXX` (e.g. `KS_CXX="zig c++"`). Without one those checks are
skipped, not failed; CI always runs them.

## 2. Configure

```bash
git clone <repository-url> kyrgyzstem && cd kyrgyzstem
cp .env.example .env
```

Edit `.env`:

* `POSTGRES_PASSWORD`: any local value, letters and digits only (it goes into a URL).
  Use the same value inside `DATABASE_URL` if you follow path B.
* `SEED_PASSWORD`: the password the synthetic accounts will get (path A and B).
* Leave `APP_ENV=development` and `SESSION_COOKIE_SECURE=false` for local HTTP.

`.env` is git-ignored. Never commit it, and never put a real secret in `.env.example`.

## 3A. Run with Docker

```bash
docker compose up --build --wait     # db, api (runs migrations on start), web
docker compose exec api python -m scripts.seed            # synthetic accounts + demo club
docker compose exec api python -m scripts.import_content --author-unlinked  # the synthetic sample, as in_review
```

* Web: <http://localhost:3000> · API docs: <http://localhost:8000/api/v1/docs>
* Readiness: `curl http://localhost:8000/api/v1/health/ready` → `{"status":"ok","database":"ok"}`
* Logs: `docker compose logs -f api` (one JSON object per line; set `LOG_FORMAT=text` in
  `.env` for plain text).
* Port 5432 already used on your machine? Set `POSTGRES_HOST_PORT=5433` in `.env`.
* Stop: `docker compose down`. Delete the database too: `docker compose down -v`.

## 3B. Run without Docker

Create two databases: one for the app, one **disposable** one for tests (the test suite
drops and recreates its schema on every run).

```bash
createdb kyrgyzstem
createdb kyrgyzstem_test
```

Then, in two terminals:

```bash
# Terminal 1, from apps/api
uv sync
uv run alembic upgrade head
uv run python -m scripts.seed
uv run python -m scripts.import_content --author-unlinked
uv run uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal 2, from apps/web (proxies /api/* to API_INTERNAL_URL, default http://localhost:8000)
npm ci
npm run dev
```

## 4. Database: initialization and migrations

The schema changes **only** through Alembic migrations in `apps/api/alembic/versions/`.

| Task | Command (from `apps/api`; in Docker prefix with `docker compose exec api`) |
|---|---|
| Create or upgrade the schema | `uv run alembic upgrade head` (the Docker image does this on start) |
| Show the current revision | `uv run alembic current` |
| Roll back one migration | `uv run alembic downgrade -1` |
| New migration after a model change | `uv run alembic revision --autogenerate -m "short message"`, then **read and edit** the file |
| Synthetic data | `uv run python -m scripts.seed` (needs `SEED_PASSWORD`; idempotent) |
| Problems from `content/` | `uv run python -m scripts.import_content --author-email EMAIL \| --author-unlinked [slug ...]` (imports as `in_review`; link the author whenever known, docs/CONTENT_GUIDE.md) |
| Attendance retention purge | `uv run python -m scripts.purge_retention` |

Rules for migrations:

* Alembic does not autogenerate CHECK constraints: add them by hand. `tests/test_meta.py`
  fails when models and migrations disagree (tables, columns, and CHECK names).
* Every migration needs a working `downgrade()`.
* Test it from an empty database: `alembic downgrade base && alembic upgrade head`.

The seed is for development only. It creates `student@`, `student2@`, `teacher@` and
`admin@example.com`, all `is_synthetic=True` with `[Synthetic]` names, and **refuses to
run when `APP_ENV=production`**. There is no default admin account in production: see
DEPLOYMENT.md, "Create the first administrator".

Imported problems stay `in_review`. Publishing is a human reviewer's decision
(docs/CONTENT_GUIDE.md); to look at them as a student locally, sign in as the synthetic
admin and use the review panel on a problem page after actually reading it.

## 5. Run the checks

These are the same checks CI runs (`.github/workflows/ci.yml`).

```bash
# apps/api
uv run ruff format --check . && uv run ruff check .
uv run mypy app scripts tests
TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/kyrgyzstem_test uv run pytest
```

PowerShell: `$env:TEST_DATABASE_URL = "postgresql+psycopg://..."; uv run pytest`.
With Docker, point `TEST_DATABASE_URL` at the compose database on `localhost:5432` after
`docker compose exec db createdb -U kyrgyzstem kyrgyzstem_test`.

```bash
# apps/web
npm run lint && npm run typecheck && npm test && npm run build
npm run e2e     # Playwright; needs the full stack running (section 3A)
```

Expected: every test `passed`, apart from skips that name their reason (for example the
C++ reference checks without a C++ compiler). Anything else needs a look.

## 6. Changing the API contract

1. Change models → new migration (section 4).
2. Change schemas/routes; add tests for anonymous (401), wrong role (403), another user's
   data (404) and the happy path.
3. `uv run python -m scripts.export_openapi` (apps/api), then `npm run gen:api` (apps/web).
4. Update `docs/API_CONTRACT.md`.

CI's *OpenAPI contract drift* job re-exports and fails if `openapi.json` or `schema.d.ts`
in the commit is stale.

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `POSTGRES_PASSWORD` error from compose | `.env` missing or the variable empty |
| API container restarts, `api` unhealthy | Migration failed: `docker compose logs api` |
| `/health/ready` → 503 | Database not reachable from the API (check `DATABASE_URL`) |
| Every API call from the web app fails | API not running, or the web build used another `API_INTERNAL_URL` (rewrites are fixed at build time) |
| pytest: `Set TEST_DATABASE_URL` | Variable not exported in this shell |
| C++ content checks skipped | No compiler found; set `KS_CXX` or install g++ |
| Login works over HTTP locally but not on a server | `SESSION_COOKIE_SECURE=true` requires HTTPS |

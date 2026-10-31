# KyrgyzSTEM

An open-source platform that helps high-school students in Kyrgyzstan prepare for
programming olympiads. The interface is available in Kyrgyz, Russian and English.

> **Status: in development, not production-ready.** A person still needs to review the
> Kyrgyz interface text, the legal drafts and the security work. See
> [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).

## What this repository contains

| Area | State |
|---|---|
| Accounts and roles | Registration, sign-in, sessions, student, teacher and admin roles |
| Problem pages | Statement, samples, step-by-step hints, editorials, saved submissions, progress |
| Editorial workflow | Reviews of rights, statements, hints, editorials and difficulty before publication |
| Schools and clubs | Organizations, groups, join codes, sessions, attendance, CSV export |
| Account safety | Login limits per address and account, admin-issued reset links, idle timeout, sign-out everywhere |
| Privacy | Data export, account deletion, consent at registration |
| AI assistant | Optional and off unless configured; falls back to authored hints |
| Automatic judging | **Not connected.** Queue and worker code exist; submissions are stored as not checked |

**Real problems are not included.** `content/problems/sample-sum` is synthetic demonstration
data, not a real educational problem. It exists only so that the app and the tests run, and
it shows the package format. The problem collection stays private until a person has
confirmed the rights for each problem.

**Legal pages are unreviewed drafts.** `/privacy`, `/terms` and `/minors` show a banner
saying so. [docs/LEGAL_REVIEW.md](docs/LEGAL_REVIEW.md) lists what is undecided.

## Stack

| | |
|---|---|
| Web | Next.js 16, TypeScript, Tailwind CSS 4 (`apps/web`) |
| API | FastAPI, Pydantic 2, SQLAlchemy 2, Alembic (`apps/api`) |
| Database | PostgreSQL |
| Tests | pytest on real PostgreSQL, Vitest, Playwright |
| Contract | OpenAPI exported from FastAPI, typed client generated for the web app |

## Quick start

```bash
cp .env.example .env        # then set POSTGRES_PASSWORD and SEED_PASSWORD
docker compose up --build --wait
docker compose exec api python -m scripts.seed
docker compose exec api python -m scripts.import_content --author-unlinked
```

Open http://localhost:3000. Imported problems start as `in_review`, so they are visible to
admins only until they pass review. Seeded accounts are synthetic and use `@example.com`.
The Docker setup and local development are described in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Checks

```bash
# apps/api: tests need a disposable database; it is wiped on every run
uv run ruff format --check . && uv run ruff check . && uv run mypy app scripts tests
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/kyrgyzstem_test uv run pytest

# apps/web
npm run lint && npm run typecheck && npm test && npm run build
```

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [API contract](docs/API_CONTRACT.md) ·
[Security](docs/SECURITY.md) · [Deployment](docs/DEPLOYMENT.md) ·
[Roadmap](docs/ROADMAP.md) · [Content guide](docs/CONTENT_GUIDE.md) ·
[Contributing](CONTRIBUTING.md)

## License

Code is released under the [MIT License](LICENSE). The MIT License does not cover
educational content: see [content/LICENSE.md](content/LICENSE.md).

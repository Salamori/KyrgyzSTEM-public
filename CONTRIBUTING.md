# Contributing to KyrgyzSTEM

Thank you for helping students in Kyrgyzstan prepare for olympiads. Contributions of
code, problems, translations (especially Kyrgyz), reviews and bug reports are all
welcome. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

**The platform serves minors.** That shapes every rule below: we collect as little data as
possible, never use real student data outside production, and a human reviews anything
students will read.

## Ways to contribute

| You want to… | Start here |
|---|---|
| Report a bug | [Bug report](../../issues/new?template=bug_report.yml). **Security problems: do not open an issue**, see [docs/SECURITY.md](docs/SECURITY.md#reporting-a-vulnerability) |
| Suggest a feature | [Feature request](../../issues/new?template=feature_request.yml); read [docs/ROADMAP.md](docs/ROADMAP.md) first |
| Write or fix a problem | [docs/CONTENT_GUIDE.md](docs/CONTENT_GUIDE.md), then a [content issue](../../issues/new?template=content.yml) or a PR |
| Improve a translation | `apps/web/src/lib/i18n/{ky,ru,en}.ts`; native-speaker review of `ky.ts` is especially valuable |
| Write code | Read the rest of this file |

## Set up and run the checks

[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) takes you from a clean checkout to a running
app (Docker or not), a database with synthetic data, and the test suites. Before opening a
pull request, run what CI runs:

```bash
# apps/api
uv run ruff format . && uv run ruff check . && uv run mypy app scripts tests
TEST_DATABASE_URL=postgresql+psycopg://...kyrgyzstem_test uv run pytest   # disposable DB, wiped
# apps/web
npm run lint && npm run typecheck && npm test && npm run build
```

## Before you write code

* Read [CLAUDE.md](CLAUDE.md) (the project's working rules, for people and AI assistants
  alike), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
  [docs/API_CONTRACT.md](docs/API_CONTRACT.md) and [docs/SECURITY.md](docs/SECURITY.md).
* For anything bigger than a bug fix, open an issue first so we can agree on the
  approach. New endpoints and schema changes are agreed in `docs/API_CONTRACT.md` or a
  proposal in `docs/proposals/` **before** implementation.
* Prefer the simplest thing that works. New dependencies need a reason and a license check
  ([docs/DEPENDENCIES.md](docs/DEPENDENCIES.md)).

## Rules that CI or reviewers will hold you to

* **Authorization is server-side**, in FastAPI dependencies and services. UI role checks
  are cosmetic. Tests cover anonymous (401), wrong role (403), another user's data
  (404/empty) and the happy path, using the `signed_in` / `make_user` fixtures.
* **Never execute submitted code** in the web or API process.
* Request models extend `RequestModel` (`extra="forbid"`); responses use explicit schemas.
  Never return ORM objects or serialize password hashes, tokens or IP addresses.
* **Schema changes need an Alembic migration** (CHECK constraints by hand);
  `tests/test_meta.py` catches drift.
* **Keep the contract in sync:** after API changes run
  `uv run python -m scripts.export_openapi` and `npm run gen:api`, and update
  `docs/API_CONTRACT.md`. CI fails on drift.
* **UI:** every data view handles loading, empty, error, unauthorized and success states;
  every string goes through `t()` with keys in `en.ts`, `ru.ts` and `ky.ts`.
* **Logging:** never log request bodies, tokens, IPs, emails or other student data
  (docs/OPERATIONS.md §3).
* **No new personal-data fields** without updating `docs/SECURITY.md` first.

## Data and content

* **Never commit** credentials, `.env` files, database dumps, real student data, or
  screenshots containing any of these. Seed and test data are synthetic:
  `is_synthetic=True`, `@example.com`, names prefixed `[Synthetic]`.
* Do not invent real people, schools, results, testimonials or metrics, in code, docs or
  demo data.
* Problems and editorials must be original or carry a verified license, with attribution
  (docs/CONTENT_GUIDE.md). If you used an AI tool to draft content, say so
  (`generated_with_ai`). Contributors never publish problems: a human reviewer who is
  not the author does.
* `content/**/*.py` is executed by the importer and tests, so it is reviewed as code.

## Pull requests

* One topic per PR, small enough to review. Fill in the PR template; it is the reviewer's
  checklist.
* Commit messages: imperative summary line (≤ 72 characters), then why. Mention the
  issue (`Fixes #12`).
* CI must be green. A reviewer approves before merging; security-relevant changes
  (auth, sessions, permissions, logging, deployment) need a second look.
* Mark AI-assisted changes in the PR description. You are responsible for every line
  you submit, however it was produced.

## Licensing of contributions

Code contributions are accepted under the repository's license (see `LICENSE`).
Educational content is licensed per problem, by default CC BY 4.0
(docs/CONTENT_GUIDE.md). By opening a pull request you confirm that you have the right to
submit the work under those terms.

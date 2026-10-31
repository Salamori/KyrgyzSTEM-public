# Dependencies: updates, vulnerability checks, licenses

Both apps pin exact versions in lockfiles: `apps/api/uv.lock` and
`apps/web/package-lock.json`. CI installs from the lockfile only (`uv sync --locked`,
`npm ci`), so a build is reproducible until someone changes a lockfile on purpose.

Rule from CLAUDE.md: no new dependency without a reason. Before adding one, check its
license (section 4), maintenance and size, and say why in the PR.

## 1. Vulnerability checks

Run them before every release and whenever a lockfile changes. CI runs the same
commands weekly and on lockfile changes (`.github/workflows/audit.yml`).

```bash
# apps/api: audit the exact locked versions (runtime and dev)
uv export --locked --format requirements-txt --no-emit-project > /tmp/requirements.txt
uvx pip-audit --strict --disable-pip --require-hashes -r /tmp/requirements.txt

# apps/web
npm audit                      # full report
npm audit --audit-level=high   # what CI fails on
```

Docker base images (`python:3.12-slim`, `node:24-alpine`, `postgres:16-alpine`,
`caddy:2-alpine`) get OS security fixes only when rebuilt: rebuild and redeploy at least
monthly, even without code changes. If a scanner is available (e.g. `docker scout cves`
or `trivy image`), run it on the built images before a release.

When an audit finds something:

1. Check whether the vulnerable code path is reachable here (note it in the PR).
2. Upgrade the package (section 2); for a transitive dependency, upgrade the parent or,
   in npm, add an `overrides` entry with a comment.
3. If no fix exists, document the risk and a mitigation in the PR and in
   RELEASE_CHECKLIST.md "Known limitations"; for anything serious, follow
   docs/SECURITY.md (do not open a public issue about an unfixed vulnerability).

**Last run (2026-10-25, this machine):** `pip-audit` on 57 locked packages: *No known
vulnerabilities found*. `npm audit`: *found 0 vulnerabilities*.

## 2. Updating dependencies

Dependabot (`.github/dependabot.yml`) opens grouped weekly PRs for uv and npm, monthly
for GitHub Actions and Docker base images. Treat them like any PR: read the changelog for
breaking changes, let CI run, and never auto-merge.

By hand:

```bash
# apps/api
uv lock --upgrade-package fastapi      # one package
uv lock --upgrade                      # everything within pyproject.toml ranges
uv sync && uv run ruff check . && uv run mypy app scripts tests && uv run pytest

# apps/web
npm outdated
npm install next@latest eslint-config-next@latest   # one package (keep these two together)
npm update                                          # everything within package.json ranges
npm run lint && npm run typecheck && npm test && npm run build
```

Then:

* After a FastAPI or Pydantic upgrade, re-export OpenAPI and regenerate types
  (`uv run python -m scripts.export_openapi`, `npm run gen:api`); CI's contract job
  catches drift.
* After a SQLAlchemy or Alembic upgrade, `tests/test_meta.py` checks models against
  migrations.
* A major upgrade (Next.js, React, FastAPI, SQLAlchemy, Pydantic, Python, Node,
  PostgreSQL) gets its own PR and a staging deployment before production.
* `uv` itself is pinned in `apps/api/Dockerfile` (`ghcr.io/astral-sh/uv:<version>`);
  update it deliberately.
* Upgrading PostgreSQL across major versions needs a dump and restore
  (OPERATIONS.md), not just a new image tag.

## 3. Pinning GitHub Actions

Workflows reference actions by major version tag (`actions/checkout@v4`). For stronger
supply-chain protection, pin them to full commit SHAs once the repository is public;
Dependabot keeps SHA pins updated too.

## 4. License review (2026-10-25)

Method: license metadata of every installed Python distribution (`importlib.metadata`,
runtime and dev) and of every non-dev package in `apps/web/package-lock.json` (144
packages). Metadata can be wrong; check the upstream repository for anything unusual.

| License | Packages | Notes |
|---|---|---|
| MIT, BSD-2/3-Clause, ISC, 0BSD, MIT-0, Unlicense, PSF-2.0, Apache-2.0 | Almost everything: FastAPI, Starlette, uvicorn, SQLAlchemy, Alembic, Pydantic, argon2-cffi, anthropic, email-validator; Next.js, React, react-markdown, openapi-fetch | Permissive; keep copyright notices when redistributing |
| LGPL-3.0 | `psycopg`, `psycopg-binary` (API); `@img/sharp-libvips-*` (optional image library pulled in by Next.js) | Used as separately installed, dynamically loaded libraries, unmodified. No effect on the license of our code. Distributing Docker images means distributing these libraries: keep their license files (pip/npm install them) and do not modify or statically link them |
| MPL-2.0 | `certifi` (CA bundle), `pathspec` (dev tool) | File-level copyleft, applies only if we modify those files |
| CC-BY-4.0 | `caniuse-lite` (browser data used at build time) | Attribution is in the package |

**Result:** no dependency restricts how the project licenses its own code. MIT,
Apache-2.0 and AGPL-3.0 are all compatible with the current dependency set. Images also
contain Debian/Alpine system packages under their own licenses (GPL among them), which is
normal for container images and does not affect the application license.

Project license: MIT (root `LICENSE`), confirmed by the owner on 2026-10-25; the decision is recorded in
RELEASE_CHECKLIST.md. Problem statements and editorials in `content/` are licensed per
problem (SPDX id in `problem.toml`; CC BY 4.0 only for original content whose rights a
reviewer has verified, docs/CONTENT_GUIDE.md).

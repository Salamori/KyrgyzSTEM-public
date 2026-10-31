## What and why

<!-- What changes, and why. Link the issue: Fixes #… -->

## How it was tested

<!-- Commands you ran and their results; manual checks (screen size, role). -->

## Checklist

- [ ] `ruff`, `mypy`, `pytest` (apps/api) and `lint`, `typecheck`, `test`, `build` (apps/web) pass locally, or failures are explained above
- [ ] Authorization enforced server-side; tests cover anonymous (401), wrong role (403), another user's data (404) and the happy path
- [ ] Schema change → Alembic migration with a working downgrade (CHECK constraints by hand)
- [ ] API change → `export_openapi` + `npm run gen:api` re-run, `docs/API_CONTRACT.md` updated
- [ ] UI: loading, empty, error, unauthorized and success states; strings via `t()` in `en`, `ru`, `ky`
- [ ] No secrets, `.env`, dumps or real personal data; nothing new logged about students; new personal-data fields documented in `docs/SECURITY.md` first
- [ ] Content: original or licensed with attribution; AI assistance declared; nothing marked reviewed or published by its author
- [ ] Docs updated to match (README, docs/…)
- [ ] AI-assisted change: said so here, and I have reviewed every line
- [ ] Not applicable items are struck through or noted

# Deployment guide

Status (2026-10-25): **nothing has been deployed yet**, and the Docker setup has not
been run, because the maintainer machine has no Docker and the repository has no CI
remote. The design below has been checked locally only in pieces (section 9). Release
blockers are listed in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md): **do not put real
students on the platform until they are closed.**

## 1. Environments

| | Development | Staging | Production |
|---|---|---|---|
| Purpose | Writing and testing code | Rehearsing a release, restore drills, smoke tests | Real students, after the pilot is approved |
| Stack | `docker-compose.yml` or `uv`/`npm` directly | `deploy/docker-compose.prod.yml` | `deploy/docker-compose.prod.yml` |
| `APP_ENV` | `development` | `production` | `production` |
| HTTPS / `SESSION_COOKIE_SECURE` | HTTP / `false` | HTTPS (Caddy) / `true` | HTTPS (Caddy) / `true` |
| Data | Synthetic seed only | **Synthetic only** (created through the UI, or the dev seed run against a dev DB; never a copy of production) | Real accounts |
| Seed (`SEED_PASSWORD`) | Yes | No, the seed refuses `APP_ENV=production` | No |
| API docs `/api/v1/docs` | On | Off | Off |
| Ports open | localhost only | 80, 443 (plus SSH, restricted) | 80, 443 (plus SSH, restricted) |
| Secrets | `.env` | Own `deploy/.env.production`, **different** from production | `deploy/.env.production` |
| Backups | Not needed | Restore drill before every release | Nightly, off-host copies |

Staging and production use the same compose file and the same images, so what passes
on staging is what ships. Use separate hosts (or at least separate Docker projects,
volumes, domains and secrets).

## 2. Production topology

```
 internet ──443──▶ caddy ──▶ web (Next.js :3000) ──▶ api (FastAPI :8000) ──▶ db (PostgreSQL 16)
                 TLS, HSTS,     network: edge+backend    network: backend+egress   network: backend
                 overwrites                                (egress = AI provider)   (internal, no internet)
                 X-Forwarded-For
```

* Only **caddy** publishes ports. web, api and db have no published ports.
* `backend` is an `internal` Docker network: the database has no route to the internet
  and is reachable only by `web`, `migrate` and `api`. The web container never queries it.
* The API runs **one uvicorn worker**: rate-limit state lives in process memory
  (docs/SECURITY.md). Do not scale `api` to several replicas or workers until the limiter
  moves to a shared store.
* Migrations run in the one-shot `migrate` service before `api` starts.
* Judging is opt-in (`--profile judge`): the `judge-worker` service and a Judge0 server
  on a separate VM (section 7). Without it every submission is stored as `not_judged`.

### Client IP addresses and audit F-01

Rate limits on login, registration and join codes are keyed by client IP. Next.js passes
a client-supplied `X-Forwarded-For` through unchanged, so without a proxy that
**overwrites** that header, every client can claim any IP and the limits do nothing
(audit F-01). The production compose file closes this as follows:

1. Caddy sets `X-Forwarded-For` to the address of the peer that connected
   (`header_up X-Forwarded-For {remote_host}` in `deploy/Caddyfile`), discarding the
   client's value.
2. web forwards that header to the API.
3. uvicorn trusts `X-Forwarded-For` only from the web container's fixed address
   (`--forwarded-allow-ips=172.29.58.10`), never `*`.

Rules that keep it closed:

* Never publish the web or API port, and never add another hop between Caddy and web.
* If a cloud load balancer or CDN terminates connections in front of Caddy,
  `{remote_host}` becomes the load balancer's address and all users share one rate-limit
  identity. In that case configure Caddy's
  [`trusted_proxies`](https://caddyserver.com/docs/caddyfile/options#trusted-proxies) with
  the balancer's ranges and use `{client_ip}` instead of `{remote_host}`, then re-run the
  smoke test.
* `deploy/smoke-test.sh` checks this on every deployment (section 6).

F-01 stays a release blocker until the smoke test has passed against a real deployment
of this setup. Only the logic has been checked locally (section 9).

## 3. Host preparation (once, manual)

These are owner tasks. Nothing in the repository does them.

1. **Host:** a Linux VM with Docker Engine and the Compose plugin. Size is not measured
   yet; start small (e.g. 2 vCPU, 4 GB RAM, 40 GB disk), monitor, and adjust.
2. **OS hardening:** automatic security updates, SSH keys only, firewall allowing
   22 (from your IPs only), 80 and 443.
3. **Domain:** DNS `A`/`AAAA` records for the site name pointing at the host. Caddy gets
   a Let's Encrypt certificate automatically once ports 80/443 are reachable.
4. **Checkout:** `git clone` the repository to e.g. `/srv/kyrgyzstem`, check out the
   release tag.
5. **Secrets:** `cp .env.example deploy/.env.production`, then set at least
   `POSTGRES_PASSWORD` (generated, letters and digits), `SITE_ADDRESS`, `RELEASE_TAG`,
   `BACKUP_DIR`. Keep `SEED_PASSWORD` empty. `chmod 600 deploy/.env.production`.
   `APP_ENV` and `SESSION_COOKIE_SECURE` are forced by the compose file.
6. **Backups:** an off-host, encrypted destination for dumps (section 8 of
   [OPERATIONS.md](OPERATIONS.md)), and the cron entries below.
7. **Security contact:** publish an address in `docs/SECURITY.md` ("Reporting a
   vulnerability") and in `CODE_OF_CONDUCT.md`.

## 4. First deployment

From the repository root on the host:

```bash
export COMPOSE="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production"
$COMPOSE build
$COMPOSE up -d --wait            # db → migrate → api → web → caddy
bash deploy/smoke-test.sh https://YOUR-SITE
$COMPOSE exec api python -m scripts.import_content --author-email EMAIL   # author known
$COMPOSE exec api python -m scripts.import_content --author-unlinked        # no account to link; reviewers attest
```

**Create the first administrator.** There are no default credentials. The future admin
registers through the site (choosing their own password), then:

```bash
$COMPOSE exec api python -m scripts.promote_admin their-email@example.org
```

This is recorded in the audit log (`user.role_changed`, `via: cli`). Promote later
admins from the admin UI instead. Problems become visible to students only after a
human reviewer publishes them (docs/CONTENT_GUIDE.md); the publisher must not be the
author. For imported content this is enforced only when the import linked the author's
account (`--author-email`); otherwise the reviewer attests it (audit F-05, docs/CONTENT_GUIDE.md).

**Scheduled jobs** (crontab of a user allowed to run docker):

```cron
15 2 * * *  cd /srv/kyrgyzstem && bash deploy/backup.sh >> /var/log/kyrgyzstem-backup.log 2>&1
45 2 * * *  cd /srv/kyrgyzstem && docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production exec -T api python -m scripts.purge_retention >> /var/log/kyrgyzstem-purge.log 2>&1
```

## 5. Deploying a new release

Follow [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). In short:

```bash
bash deploy/backup.sh                                   # always, before migrations
git fetch --tags && git checkout vX.Y.Z
sed -i 's/^RELEASE_TAG=.*/RELEASE_TAG=vX.Y.Z/' deploy/.env.production
$COMPOSE build && $COMPOSE up -d --wait                 # migrate runs first
bash deploy/smoke-test.sh https://YOUR-SITE
```

Images are tagged with `RELEASE_TAG`, so the previous release's images stay on the host
for rollback (RELEASE_CHECKLIST.md, "Rollback"). Prune old images only after a release
has run cleanly for a while (`docker image ls kyrgyzstem-*`).

## 6. Smoke test

`deploy/smoke-test.sh BASE_URL` checks readiness through the proxy, the security headers,
HSTS (for HTTPS), that `/api/v1/docs` is disabled, and F-01: it sends 11 failed logins for
a random non-existent `@example.com` address, each with a different spoofed
`X-Forwarded-For`, and expects a `429` by the 11th. The CI job `proxy-smoke` runs the
same script against the production compose file over plain HTTP.

### Vercel frontend with an API gateway (not deployed, not verified)

If the web app runs on Vercel, there is no `web` container for the API to trust, Vercel has
no fixed outbound addresses (on Hobby), and the API must not be reachable directly. This
host then runs only an API gateway, the API and the database:

```
browser ─▶ Vercel (web, apps/web/src/proxy.ts) ─HTTPS─▶ caddy gateway ─▶ api ─▶ db
```

* **Client address.** Vercel overwrites `x-forwarded-for` with the client's public IP
  (it does not forward client-supplied values; Vercel docs, "Request headers").
  `proxy.ts` (`apps/web/src/lib/apiGateway.ts`) copies that value into `X-KS-Client-IP`,
  removes any `x-ks-*` header the browser sent, and adds the secret `X-KS-Gateway-Auth`.
  It refuses (503) unless it runs on Vercel (`VERCEL=1`), the gateway URL is a bare
  `https://` origin, the secret is 32+ letters, digits, `-` or `_`, and the client IP is a
  single valid address.
* **Gateway.** `deploy/Caddyfile.gateway` answers 403 to everything outside `/api/*` and to
  every request without the exact secret or without an IP-shaped `X-KS-Client-IP`; for the
  rest it sets `X-Forwarded-For` from `X-KS-Client-IP` and strips both `x-ks-*` headers.
* **API.** Trusts `X-Forwarded-For` only from caddy's fixed address on the internal
  `gateway` network (`--forwarded-allow-ips=172.29.60.10`), never `*`. caddy is not on
  the `backend` network, so it cannot reach the database.
* **What the secret does not do.** It authenticates the caller as this Vercel project; the
  client address is trustworthy only because Vercel sets it. Anyone with the secret can
  choose their rate-limit identity: keep it only in Vercel (as a Sensitive variable) and in
  `deploy/.env.production` (mode 600), never log it, and rotate it (set the new value on
  the host, then in Vercel and redeploy; requests fail closed with 403 in between).
* **Cookies and CSRF** are unchanged: the browser still talks only to its own origin.

```bash
export COMPOSE="docker compose -f deploy/docker-compose.prod.yml -f deploy/docker-compose.gateway.yml --env-file deploy/.env.production"
$COMPOSE build && $COMPOSE up -d --wait                  # caddy, migrate, api, db; no web
GATEWAY_URL=https://API-HOST bash deploy/smoke-test.sh https://VERCEL-SITE
```

`deploy/.env.production` additionally needs `API_GATEWAY_SECRET`; `SITE_ADDRESS` is the
gateway's own hostname. In Vercel set `API_GATEWAY_URL` (`https://API-HOST`) and
`API_GATEWAY_SECRET` for the environments that should use it. Without `API_GATEWAY_URL`
the web app behaves exactly as before (local, Docker, self-hosted).

### Vercel-only deployment (experiment; not production-ready)

Both apps run in one Vercel project with [Vercel Services](https://vercel.com/docs/services)
(Beta): the root `vercel.json` builds `apps/web` (Next.js) and `apps/api` (FastAPI as a
Python Function, `app.main:app`) and routes `/api/*` to the API, everything else to the web
app, on one domain. Cookies and CSRF are unchanged (same origin, no CORS); `proxy.ts` and the
gateway are not involved (Vercel routes `/api/*` before Next.js sees it). No VPS; the
database is an external PostgreSQL (Vercel no longer offers its own: use a Marketplace
provider such as Neon).

```
browser ─▶ Vercel (one domain) ─┬─ /api/* ─▶ FastAPI function ─▶ managed PostgreSQL
                                └─ /*     ─▶ Next.js
```

What the code does for this mode:

* **Rate limits** are shared through PostgreSQL (`RATE_LIMIT_BACKEND=postgres`, table
  `rate_limit_hits`, migration 0010): function instances share no memory and come and go,
  so the in-memory limiter would neither add up across instances nor survive a cold start.
  Keys are stored only as an HMAC (`RATE_LIMIT_KEY_SECRET`), hits are serialised per key
  with an advisory lock, and expired rows are deleted (docs/SECURITY.md).
* **Client address** (audit F-01): `CLIENT_IP_SOURCE=vercel` reads `x-forwarded-for`, which
  Vercel's edge overwrites with the client's public IP; anything but exactly one IP gets
  503. The API refuses to start with `VERCEL=1` unless both settings are set, and refuses
  `CLIENT_IP_SOURCE=vercel` anywhere else. Checked against Vercel's documentation and the
  source of `vercel-runtime` 0.24.1, not on a live deployment.
* **Connections:** each instance has two pools: the request pool (`DATABASE_POOL_SIZE` +
  `DATABASE_MAX_OVERFLOW`) and the limiter's own fixed pool (`RATE_LIMIT_POOL_SIZE`, no
  overflow). They are separate because an authenticated request still holds its request
  connection when it is rate-limited; from one shared pool, concurrent requests (Vercel runs
  several per Python instance) could each hold one connection and wait for a second until
  the pool timeout. Limiter hits never wait on the request pool, so their pool always
  drains; a hit that cannot get a connection within `RATE_LIMIT_POOL_TIMEOUT_SECONDS` fails
  with 503 rather than skip the limit. One instance opens at most the sum of the three
  settings. If the provider's pooled endpoint (PgBouncer in transaction mode) rejects
  prepared statements, set `DATABASE_PREPARED_STATEMENTS=false`; the limiter's
  transaction-level advisory locks work in transaction mode.
* **Migrations** are not run by Vercel (there is no start command). Run them, and the
  content import, from a trusted machine against the database's direct (unpooled) URL.

**Use a separate Vercel project for this experiment.** Root Directory is a project setting,
not a per-branch or per-environment one, so changing it on an existing project affects its
next Production deployment.

Setup (owner):

1. Decide whether the Hobby plan's "non-commercial, personal use only" terms fit; otherwise
   Pro. Over the Hobby allotment, features pause (up to 30 days) instead of billing.
2. Create the PostgreSQL database (synthetic data only for Preview; never a copy of
   production), in or near the Functions region. Note its pooled and direct URLs.
3. From `apps/api`, against the direct URL:
   `DATABASE_URL=… uv run alembic upgrade head`, then
   `uv run python -m scripts.import_content --author-email EMAIL` when content is cleared
   for that environment.
4. Create a **new** Vercel project from the repository: Root Directory = repository root
   (the services configuration lives in the root `vercel.json`), a Production Branch that is
   never merged casually, and the Functions region of your choice.
5. Environment variables of the new project, per environment (Preview first), as Sensitive
   where secret: `DATABASE_URL` (pooled URL, `postgresql+psycopg://…`),
   `APP_ENV=production`, `SESSION_COOKIE_SECURE=true`, `CLIENT_IP_SOURCE=vercel`,
   `RATE_LIMIT_BACKEND=postgres`, `RATE_LIMIT_KEY_SECRET` (generated, 32+ letters, digits,
   `-` or `_`; a different value per environment), `DATABASE_POOL_SIZE=2`,
   `DATABASE_MAX_OVERFLOW=3`, `RATE_LIMIT_POOL_SIZE=2`, `RATE_LIMIT_POOL_TIMEOUT_SECONDS=5`
   (at most 7 connections per instance). Leave `API_GATEWAY_URL`, `JUDGE_LANGUAGES` and
   `LLM_*` unset.
6. Deploy a Preview and check: `/api/v1/health/ready` is 200, the problem list loads,
   anonymous `/api/v1/auth/session` is 401, and 11 failed logins for one address give a
   429 (`deploy/smoke-test.sh https://PREVIEW-URL`; also confirms the spoofed
   `X-Forwarded-For` is ignored).
7. Schedule `python -m scripts.purge_retention` (or a Vercel Cron Job calling an
   authenticated route, not written) for attendance retention.

Not covered: judging (the worker is a long-running process and Judge0 needs its own VM),
and whether Vercel installs the `dev` dependency group (larger bundle, not a failure).

## 7. Judging: worker and Judge0 (opt-in; not deployed, not verified)

Without judging (the default: `JUDGE_LANGUAGES` empty) the platform works and every
submission is stored as `not_judged`. With it:

```
 api ──(queues rows)──▶ db ◀──(claims, stores)── judge-worker ──private network, TLS──▶ Judge0 VM
 (no Judge0 access,                              (backend + judge                    (its own PostgreSQL
  no Judge0 token)                                 networks only)                      and Redis)
```

* **Only the worker talks to Judge0.** The API never contacts it and never gets
  `JUDGE0_*` settings: it only queues submissions in `JUDGE_LANGUAGES` (set it to exactly
  the languages the worker has ids for). The worker (`python -m scripts.judge_worker`, the
  `judge-worker` service, same image as the API) never runs submitted code; tests run on the
  Judge0 server only. It refuses to start (exit 2) without `JUDGE0_URL` and a language id.
* **Judge0 on its own VM.** Judge0 runs student code in privileged containers: treat the VM
  as untrusted and disposable. No KyrgyzSTEM data, secrets or database on it; Judge0 keeps
  its own PostgreSQL and Redis there, never published and never KyrgyzSTEM's database.
* **Network.** Judge0's API (port 2358) listens on the private network only, never on a
  public interface. VM firewall: allow 2358 from the app host's private address only, SSH
  from the admin network only, deny everything else inbound; deny outbound from the VM
  except OS/package updates during maintenance windows. Student programs get no network
  (`ENABLE_NETWORK=false`, `ALLOW_ENABLE_NETWORK=false`; the worker also sends
  `enable_network=false`). On the app host, allow only the `judge` Docker subnet to reach
  the Judge0 address, so the API (on `egress`) cannot, for example:

  ```
  # /etc/iptables rules (DOCKER-USER chain), JUDGE0_IP = the VM's private address
  iptables -I DOCKER-USER -d JUDGE0_IP -p tcp --dport 2358 -j DROP
  iptables -I DOCKER-USER -s 172.29.59.0/24 -d JUDGE0_IP -p tcp --dport 2358 -j ACCEPT
  ```

* **TLS and token.** Across any network you do not fully control, put TLS in front of
  Judge0 on the VM (for example Caddy with an internal CA) and use an `https://` URL. Set
  `AUTHN_TOKEN` on Judge0 and the same value as `JUDGE0_AUTH_TOKEN` for the worker, from
  the deployment secrets only (never in git, images, logs or the web bundle); production
  refuses a Judge0 URL without a token, and URLs with credentials or a query.
* **Limits.** Per test the worker sends CPU, wall time, memory and output limits from the
  problem (`JUDGE0_*_FACTOR_*`, `JUDGE0_MAX_OUTPUT_BYTES`); Judge0's maxima, process limit
  and queue size are set on the VM (`deploy/judge0/judge0.conf.overrides.example`). The
  worker bounds every request (`JUDGE0_TIMEOUT_SECONDS`, also for slowly trickling
  responses), every wait (`JUDGE0_MAX_WAIT_SECONDS`), response size
  (`JUDGE0_MAX_RESPONSE_BYTES`) and the whole submission (`JUDGE_MAX_JUDGING_SECONDS`,
  enforced at every heartbeat, so it can be overrun by at most one poll interval plus one
  request timeout). A test still running on Judge0 at that point is not cancelled there
  (`ENABLE_SUBMISSION_DELETE` stays off); it ends under Judge0's own wall-time limit.
* **Pin versions.** Judge0 CE **v1.13.1** (2024-04-18, the latest release when checked on
  2026-10-27; it fixes CVE-2024-28185, CVE-2024-28189 and CVE-2024-29021, so never run an
  older one). Its `docker-compose.yml` uses `judge0/judge0:latest` and publishes `2358:2358`
  on every interface: before the first start, change both images to
  `judge0/judge0:1.13.1@sha256:<digest you verified>` and the port to
  `<VM private IP>:2358:2358` (Docker's published ports bypass host firewalls such as
  UFW, so binding to the private address matters), and pin `postgres:16.2` and
  `redis:7.2.4` by digest too. Record the digests. Upgrades: test on a
  staging VM with the security checks below first; keep the previous release's archive
  and configuration for rollback (Judge0's database holds only transient submissions, so
  rebuilding the VM is a valid rollback). Patch the VM's OS and Docker regularly.
* **Backups.** KyrgyzSTEM's verdicts live in KyrgyzSTEM's database (section 4 backups);
  Judge0's database needs no backup.
* **Health.** The worker writes `JUDGE_WORKER_HEALTH_FILE`; `--check alive` (the compose
  healthcheck: the loop runs) and `--check ready` (Judge0 answered `GET /languages` with
  every configured id at the last probe) are separate. The API's readiness ignores the
  judge: a judge outage must not take the site down. While Judge0 is not ready the worker
  claims nothing, so queued submissions wait instead of using up their attempts.
* **Shutdown.** `docker compose stop judge-worker` sends SIGTERM: no new claims, a job in
  flight may finish within `JUDGE_SHUTDOWN_GRACE_SECONDS`, then it is released
  (`stop_grace_period` is longer). A killed worker's job is requeued when its lease
  (`JUDGE_LEASE_SECONDS`) expires.

### Deployment checklist (owner; none of this has been done)

1. Provision a dedicated VM for Judge0: **Ubuntu 22.04 LTS** (the release's tested OS),
   x86-64, at least 2 vCPU, 4 GB RAM (more if problems allow 2048 MB), 20 GB disk; a
   private network shared with the app host; no other workload or data. Judge0's sandbox
   (isolate) needs **cgroups v1**: set `systemd.unified_cgroup_hierarchy=0` in
   `GRUB_CMDLINE_LINUX` in `/etc/default/grub`, run `update-grub`, reboot, and check
   `stat -fc %T /sys/fs/cgroup` prints `tmpfs` (not `cgroup2fs`). Install Docker Engine and
   the Compose plugin; enable unattended security updates. Firewall as above.
2. Download `judge0-v1.13.1.zip` from the GitHub release; apply the compose changes above
   (image tags and digests, private port binding) and
   `deploy/judge0/judge0.conf.overrides.example` (checked against v1.13.1's keys); set
   `AUTHN_TOKEN`, `POSTGRES_PASSWORD` and `REDIS_PASSWORD` (all required, all secrets);
   TLS in front if the network is not fully trusted. Start as the release says (`db` and
   `redis` first, then the rest).
3. Read the language ids from `GET /languages` on that instance; set
   `JUDGE0_LANGUAGE_ID_CPP` / `JUDGE0_LANGUAGE_ID_PYTHON`. Never guess them.
4. In `deploy/.env.production`: `JUDGE0_URL`, `JUDGE0_AUTH_TOKEN`, the ids, and
   `JUDGE_LANGUAGES` with the same languages. Add the app host's DOCKER-USER rules.
5. Staging first: `docker compose --profile judge up -d --wait`, then
   `docker compose exec judge-worker python -m scripts.judge_worker --check ready`.
6. Run the security checks below on staging, then the end-to-end scenarios
   (accepted, wrong answer, compile error, runtime error, time limit, memory limit,
   partial score, Judge0 down) with synthetic problems only.

### Security checks still to run on a live instance (not done)

* A submission cannot reach the network (DNS, HTTP, raw sockets), the worker, the app
  host or Judge0's own database and Redis.
* CPU, wall time, memory, process/thread, output and disk limits each stop a program and
  map to the expected verdict. The status ids match v1.13.1's source; how a memory-limit
  kill is reported is not documented (v1.13.1 has no memory status: a killed program is a
  signal, usually status 12), so the `memory_limit` inference in `judge/judge0.py` must
  be confirmed, or replaced by `runtime_error`, from these checks.
* Fork bombs and large files are contained; the VM stays responsive.
* Requests without the token, or from the API container, are refused.
* Judge0 refuses `enable_network=true` and limits above its maxima.

## 8. Optional AI assistant

Off unless `LLM_PROVIDER` and `LLM_API_KEY` are set. Before enabling it for minors: the
provider's data-retention terms (owner decision, docs/SECURITY.md), a human-reviewed
evaluation set (CLAUDE.md), and budget limits (`AI_*`). If the provider is down or the
budget is used up, students get authored hints instead. The API reaches the provider
through the `egress` network.

## 9. What has and has not been verified

| Item | Status |
|---|---|
| `docker compose up` (dev) and the production compose file | **Not run.** No Docker on the maintainer machine. Both files validate against the Compose JSON schema. First run will be the CI `e2e` and `proxy-smoke` jobs, or a staging host |
| Caddy configuration | **Not run** |
| F-01 design | Checked locally without Docker: API (production flags, `--forwarded-allow-ips=127.0.0.1`) + production Next.js build. Smoke test against web directly: **FAIL** (spoofing works, as the audit found). Through a small test proxy that overwrites `X-Forwarded-For` like the Caddyfile does: **PASS** (429 on the 11th attempt). Caddy itself was not exercised |
| Backup and restore commands | `pg_dump --format=custom` / `pg_restore` round trip on local PostgreSQL 16.2: all 30 tables' row counts identical (OPERATIONS.md). The `docker compose exec` wrappers in `deploy/*.sh` are not run |
| Judge worker and Judge0 | **Written, not run.** `judge-worker` service and Judge0 settings exist; `docker compose config` validates the production file with the `judge` profile. The worker loop is unit-tested with fakes; the Judge0 adapter only against `httpx.MockTransport`. No Judge0 instance was ever contacted; sandbox security is **unverified** |
| A real deployment, uptime, load | None. No users, no benchmarks |

## 10. Manual production steps (owner)

1. Choose hosting; provision the VM, firewall, OS updates, SSH policy (section 3).
2. Register the domain and set DNS.
3. Generate secrets; create `deploy/.env.production` (mode 600); store a copy in a
   password manager.
4. Decide on F-02 (account lockout policy) and F-05 (content author mapping), and let them
   be fixed with tests (RELEASE_CHECKLIST.md).
5. Run staging first; pass the smoke test there (closes F-01 once verified).
6. Set up off-host encrypted backup storage and the cron jobs; do one restore drill.
7. Create the first admin with `scripts.promote_admin`.
8. Human review and publication of problems.
9. Publish a security contact; publish the privacy notice, consent and retention terms
   agreed with the pilot school (docs/SCHOOL_OPERATIONS.md).
10. External uptime monitoring of `https://YOUR-SITE/api/v1/health/ready`.
11. If enabling AI: provider account, key, budget, retention review.

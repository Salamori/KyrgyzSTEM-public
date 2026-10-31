#!/usr/bin/env bash
# Replace the production database with a dump made by deploy/backup.sh
# (docs/OPERATIONS.md#restore). DESTRUCTIVE: everything written since the dump is lost.
#
#   CONFIRM_RESTORE=yes deploy/restore.sh deploy/backups/kyrgyzstem-20261025T021500Z.dump
#
# Take a fresh backup first (deploy/backup.sh) so the restore itself can be undone.
set -euo pipefail

dump="${1:?usage: CONFIRM_RESTORE=yes restore.sh DUMP_FILE}"
[[ -f "$dump" ]] || { echo "no such file: $dump" >&2; exit 1; }
[[ "${CONFIRM_RESTORE:-}" == yes ]] || {
  echo "Refusing: set CONFIRM_RESTORE=yes to replace the database with $dump" >&2
  exit 1
}

ENV_FILE="${ENV_FILE:-deploy/.env.production}"
compose=(docker compose -f deploy/docker-compose.prod.yml --env-file "$ENV_FILE")

"${compose[@]}" exec -T db pg_restore --list < "$dump" > /dev/null  # readable dump?

echo "stopping web and api"
"${compose[@]}" stop caddy web api

echo "recreating the database"
"${compose[@]}" exec -T db sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
     -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" \
     -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\""'

echo "restoring $dump"
"${compose[@]}" exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges --exit-on-error' \
  < "$dump"

# `migrate` brings an older dump up to the running release's schema before api starts.
echo "starting the stack"
"${compose[@]}" up -d --wait
echo "restore finished; run deploy/smoke-test.sh against the site"

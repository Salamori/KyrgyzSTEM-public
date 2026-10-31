#!/usr/bin/env bash
# Dump the production database to $BACKUP_DIR (docs/OPERATIONS.md#backups).
# Run from the repository root, e.g. nightly from cron:
#   15 2 * * * cd /srv/kyrgyzstem && deploy/backup.sh >> /var/log/kyrgyzstem-backup.log 2>&1
#
# The dump contains student accounts and attendance: it is personal data. It is written
# with mode 600; copy it to encrypted off-host storage and never into the repository.
set -euo pipefail

ENV_FILE="${ENV_FILE:-deploy/.env.production}"
BACKUP_DIR="${BACKUP_DIR:-$(grep -E '^BACKUP_DIR=' "$ENV_FILE" | cut -d= -f2- || true)}"
BACKUP_DIR="${BACKUP_DIR:-deploy/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
compose=(docker compose -f deploy/docker-compose.prod.yml --env-file "$ENV_FILE")

umask 077
mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/kyrgyzstem-$(date -u +%Y%m%dT%H%M%SZ).dump"

# Custom format: compressed, restorable table by table with pg_restore.
"${compose[@]}" exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > "$file.partial"

# A dump that pg_restore cannot list is not a backup.
"${compose[@]}" exec -T db pg_restore --list < "$file.partial" > /dev/null
mv "$file.partial" "$file"
echo "backup written: $file ($(wc -c < "$file") bytes)"

# Local rotation only; off-host copies follow their own retention.
find "$BACKUP_DIR" -name 'kyrgyzstem-*.dump' -type f -mtime +"$KEEP_DAYS" -print -delete

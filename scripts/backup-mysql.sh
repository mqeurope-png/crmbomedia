#!/usr/bin/env bash
# Dump the production MySQL database to a gzipped SQL file and rotate old
# backups. Designed to run on the IONOS host as root or a user that can talk
# to docker, e.g. from cron:
#
#   0 3 * * * /opt/crmbomedia/scripts/backup-mysql.sh >> /var/log/crm-backup.log 2>&1
#
# Environment overrides:
#   BACKUP_DIR       Directory for backup files. Default: /var/backups/crmbomedia
#   RETENTION_DAYS   Days to keep backups. Default: 14
#   COMPOSE_FILE     Compose file. Default: docker-compose.prod.yml
#   ENV_FILE         Env file. Default: .env.production
#
# Reads MYSQL_ROOT_PASSWORD and MYSQL_DATABASE from ENV_FILE.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/crmbomedia}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be defined in $ENV_FILE}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE must be defined in $ENV_FILE}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/crm-$TIMESTAMP.sql.gz"
TMP_FILE="$OUT_FILE.partial"

# --single-transaction gives a consistent dump on InnoDB without locking writes.
# Use root credentials so triggers/routines/events are dumpable.
docker compose -f "$COMPOSE_FILE" exec -T db \
  mysqldump \
    --user=root \
    --password="$MYSQL_ROOT_PASSWORD" \
    --single-transaction \
    --quick \
    --routines \
    --triggers \
    --events \
    --default-character-set=utf8mb4 \
    "$MYSQL_DATABASE" \
  | gzip --best > "$TMP_FILE"

if [ ! -s "$TMP_FILE" ]; then
  echo "Backup is empty, aborting." >&2
  rm -f "$TMP_FILE"
  exit 1
fi

mv "$TMP_FILE" "$OUT_FILE"
chmod 600 "$OUT_FILE"
echo "Backup written: $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"

# Rotate old backups.
find "$BACKUP_DIR" -type f -name 'crm-*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete

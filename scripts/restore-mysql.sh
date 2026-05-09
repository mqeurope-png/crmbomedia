#!/usr/bin/env bash
# Restore the production MySQL database from a gzipped mysqldump file.
#
# WARNING: This OVERWRITES the current contents of the target database.
# Stop the api container first if you want a clean restore:
#
#   docker compose -f docker-compose.prod.yml stop api
#   ./scripts/restore-mysql.sh /var/backups/crmbomedia/crm-20260507T030000Z.sql.gz
#   docker compose -f docker-compose.prod.yml start api
#
# Environment overrides:
#   COMPOSE_FILE     Compose file. Default: docker-compose.prod.yml
#   ENV_FILE         Env file. Default: .env.production
#   ASSUME_YES       If set to 1, skip the interactive confirmation.
#
# Reads MYSQL_ROOT_PASSWORD and MYSQL_DATABASE from ENV_FILE.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <backup-file.sql.gz>" >&2
  exit 1
fi

BACKUP_FILE="$1"
if [ ! -s "$BACKUP_FILE" ]; then
  echo "Backup file does not exist or is empty: $BACKUP_FILE" >&2
  exit 1
fi

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

if [ "${ASSUME_YES:-0}" != "1" ]; then
  echo "About to restore '$MYSQL_DATABASE' from $BACKUP_FILE."
  echo "This will overwrite ALL data in the database."
  read -r -p "Type RESTORE to continue: " confirm
  if [ "$confirm" != "RESTORE" ]; then
    echo "Aborted."
    exit 1
  fi
fi

# Pipe the decompressed dump straight into the db container's mysql client.
gunzip -c "$BACKUP_FILE" \
  | docker compose -f "$COMPOSE_FILE" exec -T db \
      mysql \
        --user=root \
        --password="$MYSQL_ROOT_PASSWORD" \
        --default-character-set=utf8mb4 \
        "$MYSQL_DATABASE"

echo "Restore completed from $BACKUP_FILE"
echo "Tip: run 'docker compose -f $COMPOSE_FILE restart api' to reset cached connections."

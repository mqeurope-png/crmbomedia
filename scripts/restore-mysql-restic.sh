#!/usr/bin/env bash
# Restore the production MySQL database from a restic snapshot stored in
# IONOS HiDrive.
#
# Usage:
#   sudo bash scripts/restore-mysql-restic.sh                  # interactive
#   sudo bash scripts/restore-mysql-restic.sh latest           # latest daily
#   sudo bash scripts/restore-mysql-restic.sh <snapshot-id>    # specific
#   sudo bash scripts/restore-mysql-restic.sh latest --dry-run # simulate
#
# Configuration is read from .env.production (App root) and
# /etc/crmbo/backup.env (restic creds). The script:
#   1. Lists / accepts a snapshot id.
#   2. Asks for explicit `RESTORE` confirmation.
#   3. Restores the snapshot into /tmp/restore-<ts>/.
#   4. Stops api+frontend so no writes happen during the import.
#   5. Pipes the .sql.gz through gunzip into the db container's mysql client.
#   6. Restarts api+frontend.
#   7. Removes the temp directory.

set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/crmbo}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env.production}"
BACKUP_ENV="${BACKUP_ENV:-/etc/crmbo/backup.env}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
TMP_DIR="${TMP_DIR:-/tmp}"

DRY_RUN=false
SNAPSHOT=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *) SNAPSHOT="$arg" ;;
  esac
done

cd "$APP_ROOT"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: env file not found: $ENV_FILE" >&2
  exit 1
fi
if [ ! -f "$BACKUP_ENV" ]; then
  echo "ERROR: backup env file not found: $BACKUP_ENV (run setup-restic-hidrive.sh)" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
# shellcheck disable=SC1090
source "$BACKUP_ENV"
set +a

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set in $ENV_FILE}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE must be set in $ENV_FILE}"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set in $BACKUP_ENV}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD must be set in $BACKUP_ENV}"

# Snapshot picker -----------------------------------------------------------

if [ -z "$SNAPSHOT" ]; then
  echo "Available daily snapshots:"
  echo
  restic snapshots --tag daily --compact
  echo
  read -r -p "Enter snapshot id (8-char prefix), 'latest', or blank to abort: " SNAPSHOT
fi

if [ -z "$SNAPSHOT" ]; then
  echo "No snapshot selected. Aborted." >&2
  exit 1
fi

echo
echo "Snapshot:   $SNAPSHOT"
echo "Database:   $MYSQL_DATABASE"
echo "App root:   $APP_ROOT"
echo "Compose:    $COMPOSE_FILE"

if [ "$DRY_RUN" = true ]; then
  echo
  echo "[dry-run] Would execute:"
  echo "  1. restic restore $SNAPSHOT --target $TMP_DIR/restore-<ts>"
  echo "  2. docker compose -f $COMPOSE_FILE stop api frontend"
  echo "  3. gunzip -c <dump> | docker compose exec -T db mysql ... $MYSQL_DATABASE"
  echo "  4. docker compose -f $COMPOSE_FILE start api frontend"
  echo "  5. rm -rf $TMP_DIR/restore-<ts>"
  echo
  echo "[dry-run] No changes made."
  exit 0
fi

echo
echo "WARNING: this REPLACES every row in '$MYSQL_DATABASE' with the snapshot contents."
read -r -p "This will REPLACE all data in the running MySQL container. Type RESTORE to confirm: " CONFIRM
if [ "$CONFIRM" != "RESTORE" ]; then
  echo "Aborted (confirmation not given)."
  exit 1
fi

# Restore -------------------------------------------------------------------

RESTORE_DIR="$TMP_DIR/restore-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RESTORE_DIR"
chmod 700 "$RESTORE_DIR"

cleanup() {
  rm -rf "$RESTORE_DIR"
}
trap cleanup EXIT

echo
echo "[1/4] restic restore $SNAPSHOT → $RESTORE_DIR"
restic restore "$SNAPSHOT" --target "$RESTORE_DIR"

DUMP_FILE="$(find "$RESTORE_DIR" -type f -name '*.sql.gz' | head -1)"
if [ -z "$DUMP_FILE" ] || [ ! -s "$DUMP_FILE" ]; then
  echo "ERROR: could not locate non-empty .sql.gz inside the restored snapshot" >&2
  exit 2
fi
echo "    found: $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"

echo
echo "[2/4] Stopping api + frontend so no writes hit the db during import"
docker compose -f "$COMPOSE_FILE" stop api frontend

echo
echo "[3/4] Loading dump into MySQL container"
gunzip -c "$DUMP_FILE" \
  | docker compose -f "$COMPOSE_FILE" exec -T db \
      mysql \
        -u root \
        -p"$MYSQL_ROOT_PASSWORD" \
        --default-character-set=utf8mb4 \
        "$MYSQL_DATABASE"

echo
echo "[4/4] Restarting api + frontend"
docker compose -f "$COMPOSE_FILE" start api frontend

echo
echo "Restore complete from snapshot=$SNAPSHOT."
echo "Tip: run 'docker compose -f $COMPOSE_FILE restart api' if cached connections behave oddly."

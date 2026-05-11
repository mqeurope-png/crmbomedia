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

# Read a single key from a dotenv-style file, stripping a wrapping pair of
# quotes if present. See backup-mysql-restic.sh for the rationale.
load_env_var() {
  local key="$1" file="$2"
  [ -f "$file" ] || return 0
  local raw
  raw="$(grep -m1 "^${key}=" "$file" 2>/dev/null || true)"
  [ -n "$raw" ] || return 0
  raw="${raw#${key}=}"
  case "$raw" in
    \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
    \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
  esac
  printf '%s' "$raw"
}

# Honour an operator-exported COMPOSE_FILE (e.g. the Plesk override pattern
# "docker-compose.prod.yml:docker-compose.plesk.yml"): docker compose reads it
# natively; passing it to -f as a literal path fails.
if [ -n "${COMPOSE_FILE:-}" ]; then
  COMPOSE_ARGS=()
  COMPOSE_DISPLAY="docker compose"
else
  COMPOSE_ARGS=(-f docker-compose.prod.yml)
  COMPOSE_DISPLAY="docker compose -f docker-compose.prod.yml"
fi

cd "$APP_ROOT"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: env file not found: $ENV_FILE" >&2
  exit 1
fi
if [ ! -f "$BACKUP_ENV" ]; then
  echo "ERROR: backup env file not found: $BACKUP_ENV (run setup-restic-hidrive.sh)" >&2
  exit 1
fi

MYSQL_ROOT_PASSWORD="$(load_env_var MYSQL_ROOT_PASSWORD "$ENV_FILE")"
MYSQL_DATABASE="$(load_env_var MYSQL_DATABASE "$ENV_FILE")"
export MYSQL_ROOT_PASSWORD MYSQL_DATABASE

set -a
# shellcheck disable=SC1090
source "$BACKUP_ENV"
set +a

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set in $ENV_FILE}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE must be set in $ENV_FILE}"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set in $BACKUP_ENV}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD must be set in $BACKUP_ENV}"

# Resolve restic with a defensive fallback.
if [ -z "${RESTIC_BIN:-}" ] || [ ! -x "${RESTIC_BIN}" ]; then
  RESTIC_BIN="$(command -v restic 2>/dev/null || true)"
  if [ -z "$RESTIC_BIN" ]; then
    for candidate in /usr/local/bin/restic /usr/bin/restic; do
      if [ -x "$candidate" ]; then
        RESTIC_BIN="$candidate"
        break
      fi
    done
  fi
fi
if [ -z "${RESTIC_BIN:-}" ] || [ ! -x "$RESTIC_BIN" ]; then
  echo "ERROR: restic binary not found. Run setup-restic-hidrive.sh or install restic." >&2
  exit 1
fi

# Snapshot picker -----------------------------------------------------------

if [ -z "$SNAPSHOT" ]; then
  echo "Available daily snapshots:"
  echo
  "$RESTIC_BIN" snapshots --tag daily --compact
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
echo "Compose:    $COMPOSE_DISPLAY"
echo "restic:     $RESTIC_BIN"

if [ "$DRY_RUN" = true ]; then
  echo
  echo "[dry-run] Would execute:"
  echo "  1. $RESTIC_BIN restore $SNAPSHOT --target $TMP_DIR/restore-<ts>"
  echo "  2. $COMPOSE_DISPLAY stop api frontend"
  echo "  3. gunzip -c <dump> | $COMPOSE_DISPLAY exec -T db mysql ... $MYSQL_DATABASE"
  echo "  4. $COMPOSE_DISPLAY start api frontend"
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
echo "[1/4] $RESTIC_BIN restore $SNAPSHOT → $RESTORE_DIR"
"$RESTIC_BIN" restore "$SNAPSHOT" --target "$RESTORE_DIR"

DUMP_FILE="$(find "$RESTORE_DIR" -type f -name '*.sql.gz' | head -1)"
if [ -z "$DUMP_FILE" ] || [ ! -s "$DUMP_FILE" ]; then
  echo "ERROR: could not locate non-empty .sql.gz inside the restored snapshot" >&2
  exit 2
fi
echo "    found: $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"

echo
echo "[2/4] Stopping api + frontend so no writes hit the db during import"
docker compose "${COMPOSE_ARGS[@]}" stop api frontend

echo
echo "[3/4] Loading dump into MySQL container"
gunzip -c "$DUMP_FILE" \
  | docker compose "${COMPOSE_ARGS[@]}" exec -T db \
      mysql \
        -u root \
        -p"$MYSQL_ROOT_PASSWORD" \
        --default-character-set=utf8mb4 \
        "$MYSQL_DATABASE"

echo
echo "[4/4] Restarting api + frontend"
docker compose "${COMPOSE_ARGS[@]}" start api frontend

echo
echo "Restore complete from snapshot=$SNAPSHOT."
echo "Tip: run '$COMPOSE_DISPLAY restart api' if cached connections behave oddly."

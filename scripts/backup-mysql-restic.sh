#!/usr/bin/env bash
# Off-site backup of the production MySQL container.
#
# Flow:
#   mysqldump (inside the db container)
#     -> gzip --best
#     -> /tmp/crmbo-<db>-<ts>.sql.gz
#     -> restic backup --tag daily
#     -> rclone:hidrive: (WebDAV to IONOS HiDrive)
#     -> restic forget --prune (keep 7d + 4w + 12m)
#
# Configuration:
#   * .env.production (App root)        : MYSQL_ROOT_PASSWORD, MYSQL_DATABASE
#                                         (read with a small parser; do NOT
#                                         `source` it — values may contain
#                                         spaces or other shell-sensitive
#                                         characters quoted for Compose).
#   * /etc/crmbo/backup.env (root, 600) : RESTIC_REPOSITORY, RESTIC_PASSWORD,
#                                         RESTIC_BIN, RCLONE_BIN, optional
#                                         BACKUP_FAILURE_WEBHOOK.
#
# Cron invocation (installed by setup-restic-hidrive.sh):
#   0 3 * * * root . /etc/crmbo/backup.env && \
#     /opt/crmbo/scripts/backup-mysql-restic.sh >> /var/log/crmbo-backup.log 2>&1

set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/crmbo}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env.production}"
BACKUP_ENV="${BACKUP_ENV:-/etc/crmbo/backup.env}"
TMP_DIR="${TMP_DIR:-/tmp}"

log() {
  printf '%s [backup] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

notify_failure() {
  local message="$1"
  if [ -n "${BACKUP_FAILURE_WEBHOOK:-}" ]; then
    local escaped
    escaped="${message//\"/\\\"}"
    curl -sS --max-time 10 -X POST "$BACKUP_FAILURE_WEBHOOK" \
      -H 'Content-Type: application/json' \
      -d "{\"host\":\"$(hostname)\",\"error\":\"$escaped\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
      >/dev/null 2>&1 || log "Webhook notification failed."
  fi
}

on_error() {
  local exit_code=$?
  local message="Backup failed with exit code $exit_code (line ${BASH_LINENO[0]:-?})"
  log "ERROR: $message"
  notify_failure "$message"
  # Best-effort cleanup of partial files in /tmp.
  rm -f "${DUMP_FILE:-}" 2>/dev/null || true
  exit "$exit_code"
}
trap on_error ERR

# Read a single key from a dotenv-style file, stripping a wrapping pair of
# double or single quotes if present. Empty string when missing. This avoids
# `source` blowing up on values like `SMTP_FROM_NAME=CRMBO Media CRM`.
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

# `docker compose` understands $COMPOSE_FILE (colon-separated list, layered
# overrides) when no -f is given. If the operator already exports
# COMPOSE_FILE for the Plesk reverse-proxy layout, the literal path passed
# to -f breaks compose. Build the argv accordingly.
if [ -n "${COMPOSE_FILE:-}" ]; then
  COMPOSE_ARGS=()
else
  COMPOSE_ARGS=(-f docker-compose.prod.yml)
fi

cd "$APP_ROOT"

# Load env. .env.production for MySQL creds (via parser), /etc/crmbo/backup.env
# for restic creds (we control that file's format, safe to source).
if [ ! -f "$ENV_FILE" ]; then
  log "ERROR: env file not found: $ENV_FILE"
  exit 1
fi
if [ ! -f "$BACKUP_ENV" ]; then
  log "ERROR: backup env file not found: $BACKUP_ENV (run setup-restic-hidrive.sh)"
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

# Resolve restic with a defensive fallback: the env file written by
# setup-restic-hidrive.sh ships an absolute RESTIC_BIN, but older installs
# may not have it. Look it up via $PATH and the standard install paths.
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
  log "ERROR: restic binary not found. Run setup-restic-hidrive.sh or install restic."
  exit 1
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="$TMP_DIR/crmbo-${MYSQL_DATABASE}-${TIMESTAMP}.sql.gz"

START_TS="$(date +%s)"

log "=== Backup run started ==="
log "Database: $MYSQL_DATABASE"
log "Repository: $RESTIC_REPOSITORY"
log "restic: $RESTIC_BIN"
log "compose args: ${COMPOSE_ARGS[*]:-<from \$COMPOSE_FILE>}"

log "Step 1/3: mysqldump (single-transaction) into $DUMP_FILE"
docker compose "${COMPOSE_ARGS[@]}" exec -T db \
  mysqldump \
    -u root \
    -p"$MYSQL_ROOT_PASSWORD" \
    --single-transaction \
    --routines \
    --triggers \
    --default-character-set=utf8mb4 \
    "$MYSQL_DATABASE" \
  | gzip --best > "$DUMP_FILE"

if [ ! -s "$DUMP_FILE" ]; then
  log "ERROR: dump file is empty: $DUMP_FILE"
  rm -f "$DUMP_FILE"
  exit 2
fi

DUMP_SIZE_BYTES="$(stat -c%s "$DUMP_FILE")"
DUMP_SIZE_HUMAN="$(du -h "$DUMP_FILE" | cut -f1)"
log "mysqldump complete: size=$DUMP_SIZE_HUMAN ($DUMP_SIZE_BYTES bytes)"

log "Step 2/3: restic backup --tag daily"
# --json emits one JSON object per progress event; the last "summary" line has
# the snapshot_id we want to surface.
BACKUP_LOG="$("$RESTIC_BIN" backup --tag daily --host "$(hostname)" --json "$DUMP_FILE")"

SNAPSHOT_ID="$(
  echo "$BACKUP_LOG" \
    | grep -o '"snapshot_id":"[a-f0-9]*"' \
    | tail -1 \
    | cut -d'"' -f4
)"
log "Snapshot stored: ${SNAPSHOT_ID:-<unknown>}"

log "Step 3/3: applying retention (keep-daily 7, keep-weekly 4, keep-monthly 12)"
"$RESTIC_BIN" forget \
  --prune \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 12 \
  --tag daily \
  2>&1 | sed 's/^/    /'

rm -f "$DUMP_FILE"

DURATION=$(($(date +%s) - START_TS))
log "=== Backup OK: snapshot=$SNAPSHOT_ID size=$DUMP_SIZE_HUMAN duration=${DURATION}s ==="

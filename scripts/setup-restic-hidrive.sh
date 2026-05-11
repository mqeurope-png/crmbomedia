#!/usr/bin/env bash
# Interactive, idempotent setup of off-site MySQL backups using
# restic + rclone + IONOS HiDrive (WebDAV).
#
# Run this ONCE on the VPS after installing the stack:
#   sudo bash /opt/crmbo/scripts/setup-restic-hidrive.sh
#
# The script:
#   1. Ensures rclone and restic are installed.
#   2. Prompts for HIDRIVE_USER / HIDRIVE_PASS / HIDRIVE_PATH /
#      RESTIC_PASSWORD / BACKUP_FAILURE_WEBHOOK (the last is optional).
#   3. Writes /root/.config/rclone/rclone.conf with a [hidrive] WebDAV remote.
#   4. Writes /etc/crmbo/backup.env (root, 0600) with the restic credentials.
#   5. Tests rclone connectivity to HiDrive.
#   6. Initializes the restic repository if it doesn't exist, otherwise
#      verifies it by listing snapshots.
#   7. Installs /etc/cron.d/crmbo-backup with the daily backup at 03:00 UTC
#      and the monthly integrity check at 04:00 UTC on the 1st.
#   8. Prints a summary and the manual verification commands.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/crmbo}"

# --- dependency installers ---------------------------------------------------

ensure_rclone() {
  if command -v rclone >/dev/null 2>&1; then
    echo "[ok] rclone already installed: $(rclone version 2>/dev/null | head -1)"
    return
  fi
  echo "[install] rclone"
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y rclone && return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y rclone && return
  fi
  echo "[install] rclone: falling back to upstream installer"
  curl -fsSL https://rclone.org/install.sh | bash
}

ensure_restic() {
  if command -v restic >/dev/null 2>&1; then
    echo "[ok] restic already installed: $(restic version 2>/dev/null | head -1)"
    return
  fi
  echo "[install] restic"
  if command -v dnf >/dev/null 2>&1; then
    # AlmaLinux / RHEL / Rocky: restic ships in EPEL.
    dnf install -y epel-release 2>/dev/null || true
    if dnf install -y restic; then
      return
    fi
  fi
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y restic && return
  fi

  echo "[install] restic: falling back to upstream binary release"
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64) arch=amd64 ;;
    aarch64) arch=arm64 ;;
    *) echo "[error] Unsupported architecture: $arch" >&2; exit 1 ;;
  esac
  local version="0.17.3"
  local tmp
  tmp="$(mktemp)"
  curl -fsSL \
    "https://github.com/restic/restic/releases/download/v${version}/restic_${version}_linux_${arch}.bz2" \
    -o "${tmp}.bz2"
  bunzip2 -c "${tmp}.bz2" > /usr/local/bin/restic
  rm -f "${tmp}.bz2" "${tmp}"
  chmod +x /usr/local/bin/restic
  echo "[ok] restic ${version} installed at /usr/local/bin/restic"
}

# --- interactive prompts -----------------------------------------------------

prompt_value() {
  # prompt_value VARNAME "prompt text" [default]
  local var="$1" text="$2" default="${3-}"
  local current="${!var-}"
  if [ -n "$current" ]; then
    echo "[ok] $var already set in environment (using existing value)"
    return
  fi
  local input
  if [ -n "$default" ]; then
    read -r -p "$text [$default]: " input
    input="${input:-$default}"
  else
    read -r -p "$text: " input
  fi
  if [ -z "$input" ]; then
    echo "[error] $var cannot be empty" >&2
    exit 1
  fi
  printf -v "$var" '%s' "$input"
  export "$var"
}

prompt_secret() {
  local var="$1" text="$2"
  local current="${!var-}"
  if [ -n "$current" ]; then
    echo "[ok] $var already set in environment (using existing value)"
    return
  fi
  local input
  read -r -s -p "$text: " input
  echo
  if [ -z "$input" ]; then
    echo "[error] $var cannot be empty" >&2
    exit 1
  fi
  printf -v "$var" '%s' "$input"
  export "$var"
}

# --- main --------------------------------------------------------------------

echo "=== CRMBO off-site backup setup (restic + rclone + IONOS HiDrive) ==="
echo

ensure_rclone
ensure_restic

echo
prompt_value HIDRIVE_USER "IONOS HiDrive username (full email / login)"
prompt_secret HIDRIVE_PASS "IONOS HiDrive password"
prompt_value HIDRIVE_PATH "HiDrive folder for backups" "bocrm"
prompt_secret RESTIC_PASSWORD "Restic encryption password (KEEP A COPY — without it backups are unrecoverable)"

WEBHOOK_DEFAULT="<none>"
prompt_value BACKUP_FAILURE_WEBHOOK "Webhook URL to call on failure (or '<none>')" "$WEBHOOK_DEFAULT"
if [ "$BACKUP_FAILURE_WEBHOOK" = "$WEBHOOK_DEFAULT" ]; then
  BACKUP_FAILURE_WEBHOOK=""
fi

# --- rclone config -----------------------------------------------------------

mkdir -p /root/.config/rclone
RCLONE_CONF=/root/.config/rclone/rclone.conf

HIDRIVE_PASS_OBSCURED="$(rclone obscure "$HIDRIVE_PASS")"
WEBDAV_URL="https://webdav.hidrive.strato.com/users/${HIDRIVE_USER}/${HIDRIVE_PATH}/"

TMP_CONF="$(mktemp)"
if [ -f "$RCLONE_CONF" ]; then
  # Strip any existing [hidrive] block so re-runs replace instead of duplicate.
  awk '
    BEGIN {in_block=0}
    /^\[hidrive\][[:space:]]*$/ {in_block=1; next}
    /^\[[^]]+\][[:space:]]*$/ && in_block==1 {in_block=0}
    in_block==0 {print}
  ' "$RCLONE_CONF" > "$TMP_CONF"
fi
cat >> "$TMP_CONF" <<EOF
[hidrive]
type = webdav
url = $WEBDAV_URL
vendor = other
user = $HIDRIVE_USER
pass = $HIDRIVE_PASS_OBSCURED
EOF
mv "$TMP_CONF" "$RCLONE_CONF"
chmod 600 "$RCLONE_CONF"
chown root:root "$RCLONE_CONF"
echo "[ok] rclone config written → $RCLONE_CONF"

# --- backup env --------------------------------------------------------------

mkdir -p /etc/crmbo
BACKUP_ENV=/etc/crmbo/backup.env
cat > "$BACKUP_ENV" <<EOF
# Generated by setup-restic-hidrive.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# DO NOT commit. Read by /opt/crmbo/scripts/backup-mysql-restic.sh and
# /opt/crmbo/scripts/restore-mysql-restic.sh.
RESTIC_REPOSITORY=rclone:hidrive:
RESTIC_PASSWORD=$RESTIC_PASSWORD
BACKUP_FAILURE_WEBHOOK=$BACKUP_FAILURE_WEBHOOK
EOF
chmod 600 "$BACKUP_ENV"
chown root:root "$BACKUP_ENV"
echo "[ok] $BACKUP_ENV written (root:root, 0600)"

# --- connectivity check ------------------------------------------------------

echo
echo "[verify] rclone lsd hidrive: ..."
if rclone lsd hidrive: >/dev/null 2>&1; then
  echo "[ok] rclone can reach HiDrive"
else
  echo "[error] rclone could not list hidrive:. Re-check HIDRIVE_USER/HIDRIVE_PASS." >&2
  exit 1
fi

# --- restic init / verify ----------------------------------------------------

export RESTIC_REPOSITORY="rclone:hidrive:"
export RESTIC_PASSWORD

if restic snapshots --no-lock >/dev/null 2>&1; then
  echo "[ok] Restic repository already initialized; snapshots are accessible"
else
  echo "[init] Initializing new restic repository at $RESTIC_REPOSITORY ..."
  restic init
  echo "[ok] Restic repository initialized"
fi

# --- cron --------------------------------------------------------------------

CRON_FILE=/etc/cron.d/crmbo-backup
cat > "$CRON_FILE" <<'EOF'
# CRMBO Media off-site backups — generated by setup-restic-hidrive.sh.
# Edit via the setup script (rerun is idempotent) or replace this file.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Daily mysqldump + restic upload @ 03:00 UTC
0 3 * * * root . /etc/crmbo/backup.env && /opt/crmbo/scripts/backup-mysql-restic.sh >> /var/log/crmbo-backup.log 2>&1

# Monthly structural integrity check @ 04:00 UTC on day 1
0 4 1 * * root . /etc/crmbo/backup.env && restic check >> /var/log/crmbo-backup.log 2>&1
EOF
chmod 644 "$CRON_FILE"
chown root:root "$CRON_FILE"
echo "[ok] cron installed at $CRON_FILE"

touch /var/log/crmbo-backup.log
chmod 640 /var/log/crmbo-backup.log
chown root:adm /var/log/crmbo-backup.log 2>/dev/null \
  || chown root:root /var/log/crmbo-backup.log

# --- summary -----------------------------------------------------------------

cat <<EOF

=== Setup complete ===

  Repository : rclone:hidrive:
  WebDAV URL : $WEBDAV_URL
  Daily      : 03:00 UTC ($APP_ROOT/scripts/backup-mysql-restic.sh)
  Monthly    : 04:00 UTC on the 1st (restic check)
  Log        : /var/log/crmbo-backup.log
  Cron       : $CRON_FILE
  Env file   : $BACKUP_ENV

Manual verification:
  bash $APP_ROOT/scripts/test-backup-hidrive.sh
  . $BACKUP_ENV && restic snapshots
  . $BACKUP_ENV && restic stats

>> STORE THESE IN A PASSWORD MANAGER (irrecoverable if lost):
   - RESTIC_PASSWORD       (the encryption key)
   - HIDRIVE_USER          ($HIDRIVE_USER)
   - HIDRIVE_PASS          (HiDrive account password)

EOF

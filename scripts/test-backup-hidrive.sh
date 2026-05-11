#!/usr/bin/env bash
# End-to-end verification of the off-site backup pipeline.
# Run on demand (NOT through cron) to confirm restic + HiDrive are healthy.
#
# Steps:
#   1. Run the daily backup script directly (writes a snapshot now).
#   2. List all snapshots in the repository.
#   3. Print the repository stats (logical + restore size).
#   4. Suggest the dry-run restore command as a final smoke test.

set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/crmbo}"
BACKUP_ENV="${BACKUP_ENV:-/etc/crmbo/backup.env}"

if [ ! -f "$BACKUP_ENV" ]; then
  echo "ERROR: $BACKUP_ENV not found. Run setup-restic-hidrive.sh first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$BACKUP_ENV"
set +a

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY missing in $BACKUP_ENV}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD missing in $BACKUP_ENV}"

cat <<EOF
=== CRMBO backup verification ===

Repository: $RESTIC_REPOSITORY
App root:   $APP_ROOT

EOF

echo "Step 1/3: running backup-mysql-restic.sh ..."
echo "-----------------------------------------------------------------"
"$APP_ROOT/scripts/backup-mysql-restic.sh"
echo "-----------------------------------------------------------------"

echo
echo "Step 2/3: snapshots in the repository:"
restic snapshots --tag daily --compact

echo
echo "Step 3/3: repository statistics:"
restic stats

cat <<EOF

=== Verification finished ===

Next manual check (recommended): dry-run restore plan
  $APP_ROOT/scripts/restore-mysql-restic.sh latest --dry-run

Quarterly (every 3 months): run a deep integrity verification
  . $BACKUP_ENV && restic check --read-data-subset 5%

EOF

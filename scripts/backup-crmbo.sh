#!/usr/bin/env bash
# Sprint Backup. Dump completo MySQL + .env.production, lo cifra con
# GPG simétrica, lo rota a 3 archivos, y opcionalmente lo replica a
# Google Drive vía rclone.
#
# Diseñado para correr desde cron cada 72 h:
#   0 3 */3 * * /opt/crmbomedia/scripts/backup-crmbo.sh \
#               >> /var/log/crmbo-backup.log 2>&1
#
# El script TAMBIÉN se invoca desde el worker RQ cuando un admin
# pulsa "Crear backup ahora" en /admin/backups. La diferencia: el
# wrapper Python (`app.backups.service.run_backup`) escribe la row
# en la tabla `backups`. Este script NO toca la BD — se limita a
# producir el binario + imprimir una línea STATS|... al final que
# el wrapper parsea.
#
# Environment overrides:
#   BACKUP_DIR                       Default: /var/backups/crmbo
#   BACKUP_RETAIN                    Default: 3 (rotación FIFO)
#   BACKUP_ENCRYPTION_PASSPHRASE     Required (≥ 32 chars random).
#   ENV_FILE                         Default: /opt/crmbo/.env.production
#   COMPOSE_FILE                     Default: docker-compose.prod.yml
#   COMPOSE_DIR                      Default: dirname(ENV_FILE)
#   RCLONE_REMOTE                    Default: drive:CRMBO_Backups
#                                    (vacío = saltar push a Drive)
#   MYSQL_HOST                       Default: 127.0.0.1 (si no usas
#                                    docker compose pásalo aquí)
#   USE_DOCKER                       1 = usa `docker compose exec db`,
#                                    0 = mysqldump directo. Default: 1.
#
# Lee MYSQL_ROOT_PASSWORD del ENV_FILE (forma transparente al cron).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/crmbo}"
BACKUP_RETAIN="${BACKUP_RETAIN:-3}"
ENV_FILE="${ENV_FILE:-/opt/crmbo/.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
COMPOSE_DIR="${COMPOSE_DIR:-$(dirname "$ENV_FILE")}"
RCLONE_REMOTE="${RCLONE_REMOTE:-drive:CRMBO_Backups}"
USE_DOCKER="${USE_DOCKER:-1}"

log() { echo "[backup-crmbo $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

abort() {
  log "ERROR: $*"
  # Final stats line para que el wrapper Python detecte fallo aun
  # cuando el script muere en mitad. status=failed indica que el
  # output (filepath/size/drive_url) puede estar vacío.
  echo "STATS|status=failed|error=$*"
  exit 1
}

if [ ! -f "$ENV_FILE" ]; then
  abort "ENV_FILE no existe: $ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set in $ENV_FILE}"
: "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE must be set in $ENV_FILE}"

# Sanity: passphrase razonable (al menos 16 chars). Una passphrase
# corta convierte el cifrado en seguridad teatral.
if [ "${#BACKUP_ENCRYPTION_PASSPHRASE}" -lt 16 ]; then
  abort "BACKUP_ENCRYPTION_PASSPHRASE demasiado corta (<16 chars)."
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
WORK_DIR="$(mktemp -d -t crmbo-backup-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

DB_DUMP="$WORK_DIR/db.sql"
ENV_COPY="$WORK_DIR/env.production"
TAR_FILE="$WORK_DIR/backup_${TIMESTAMP}.tar.gz"
GPG_FILE="$TAR_FILE.gpg"
FINAL_FILE="$BACKUP_DIR/$(basename "$GPG_FILE")"

# 1. mysqldump --all-databases --routines --triggers --single-transaction.
log "1/8 mysqldump → $DB_DUMP"
if [ "$USE_DOCKER" = "1" ]; then
  (cd "$COMPOSE_DIR" && docker compose -f "$COMPOSE_FILE" exec -T db \
    mysqldump \
      --user=root \
      --password="$MYSQL_ROOT_PASSWORD" \
      --all-databases \
      --routines \
      --triggers \
      --single-transaction \
      --quick \
      --default-character-set=utf8mb4) > "$DB_DUMP" \
    || abort "mysqldump (docker) falló"
else
  mysqldump \
    --host="${MYSQL_HOST:-127.0.0.1}" \
    --user=root \
    --password="$MYSQL_ROOT_PASSWORD" \
    --all-databases \
    --routines \
    --triggers \
    --single-transaction \
    --quick \
    --default-character-set=utf8mb4 \
    > "$DB_DUMP" || abort "mysqldump falló"
fi
[ -s "$DB_DUMP" ] || abort "Dump SQL vacío"

# 2. Copia .env.production. Conserva el modo 600 del original.
log "2/8 copy env → $ENV_COPY"
cp "$ENV_FILE" "$ENV_COPY"
chmod 600 "$ENV_COPY"

# 3. tar -czf.
log "3/8 tar → $TAR_FILE"
tar -czf "$TAR_FILE" -C "$WORK_DIR" db.sql env.production \
  || abort "tar falló"

# 4. gpg --symmetric --cipher-algo AES256.
log "4/8 gpg encrypt → $GPG_FILE"
# `--passphrase` por argumento es lo único compatible con --batch sin
# tener un agente GPG en VPS. La passphrase no entra en el process
# tree porque pasamos por env var, no por línea de comandos.
gpg --batch --yes --no-tty \
    --symmetric --cipher-algo AES256 \
    --passphrase-fd 3 \
    --output "$GPG_FILE" \
    "$TAR_FILE" 3<<<"$BACKUP_ENCRYPTION_PASSPHRASE" \
  || abort "gpg falló"
[ -s "$GPG_FILE" ] || abort "Archivo cifrado vacío"

# 5. Mover a $BACKUP_DIR.
log "5/8 move → $FINAL_FILE"
mv "$GPG_FILE" "$FINAL_FILE"
chmod 600 "$FINAL_FILE"

SIZE_BYTES="$(stat -c%s "$FINAL_FILE")"
log "   size=$SIZE_BYTES bytes ($(du -h "$FINAL_FILE" | cut -f1))"

# 6. Rotación FIFO: conservar solo $BACKUP_RETAIN más recientes.
log "6/8 rotate retain=$BACKUP_RETAIN"
mapfile -t OLD_FILES < <(
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'backup_*.tar.gz.gpg' \
    -printf '%T@ %p\n' \
    | sort -rn \
    | awk -v n="$BACKUP_RETAIN" 'NR>n {print $2}'
)
for old in "${OLD_FILES[@]}"; do
  log "   rm $old"
  rm -f "$old"
done

# 7. Push a Drive vía rclone (opcional; si rclone no existe o el
#    remote está vacío, saltamos sin fallar).
DRIVE_URL=""
if [ -n "$RCLONE_REMOTE" ] && command -v rclone >/dev/null 2>&1; then
  log "7/8 rclone copy → $RCLONE_REMOTE/"
  if rclone copy "$FINAL_FILE" "$RCLONE_REMOTE/" --quiet; then
    # rclone link puede devolver la URL pública del archivo; si el
    # remote no soporta links (carpeta no compartida) silenciamos.
    DRIVE_URL="$(rclone link "$RCLONE_REMOTE/$(basename "$FINAL_FILE")" 2>/dev/null || true)"
  else
    log "   WARN: rclone copy falló — backup queda solo local"
  fi
else
  log "7/8 rclone skip (sin remote o sin binario instalado)"
fi

# 8. STATS para el wrapper Python. Una sola línea, fácil de parsear.
log "8/8 done"
echo "STATS|status=success|filename=$(basename "$FINAL_FILE")|filepath=$FINAL_FILE|size_bytes=$SIZE_BYTES|drive_url=$DRIVE_URL"

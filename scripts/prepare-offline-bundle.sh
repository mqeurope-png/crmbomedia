#!/usr/bin/env bash
# prepare-offline-bundle.sh — genera el zip offline de 234 MB para
# desplegar BoHub CRM en un VPS sin (o con poco) internet.
#
# Ejecútalo en TU máquina (no en el VPS). Necesita:
#   - git
#   - python3 + pip
#   - node + npm
#   - zip
#
# Output: bohub-offline-install.zip (~234 MB) listo para copiar al
# VPS y usar con bootstrap-server-offline.sh.
#
# Uso:
#   bash prepare-offline-bundle.sh [--out /path/to/output.zip]

set -euo pipefail

OUT="${PWD}/bohub-offline-install.zip"
REPO_URL="https://github.com/mqeurope-png/crmbomedia.git"
WORK_DIR="$(mktemp -d -t bohub-bundle.XXXXXX)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --repo-url) REPO_URL="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 1 ;;
  esac
done

COL_BLUE='\033[1;34m'; COL_GREEN='\033[1;32m'; COL_RED='\033[1;31m'; COL_RESET='\033[0m'
section() { printf "\n${COL_BLUE}==> %s${COL_RESET}\n" "$*"; }
ok()      { printf "${COL_GREEN}✓ %s${COL_RESET}\n" "$*"; }
fail()    { printf "${COL_RED}✗ %s${COL_RESET}\n" "$*" >&2; exit 1; }

cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

section "Pre-flight"
for cmd in git python3 npm zip; do
  command -v "$cmd" >/dev/null || fail "Falta $cmd. Instálalo y reintenta."
done
python3 -m pip --version >/dev/null || fail "pip no disponible en python3"
ok "Dependencias en orden"

section "Clonando repo en $WORK_DIR/source"
git clone --depth 1 "$REPO_URL" "$WORK_DIR/source"
ok "Repo clonado"

section "Pre-descargando wheels Python"
mkdir -p "$WORK_DIR/wheels"
python3 -m pip download --quiet --no-cache-dir \
  -r "$WORK_DIR/source/backend/requirements.txt" \
  --dest "$WORK_DIR/wheels"
ok "$(ls "$WORK_DIR/wheels" | wc -l) wheels descargadas"

section "Instalando node_modules del frontend"
( cd "$WORK_DIR/source/frontend" && npm ci --no-audit --no-fund )
( cd "$WORK_DIR/source/frontend" && tar -czf "$WORK_DIR/frontend-node_modules.tar.gz" node_modules )
ok "node_modules tarballed ($(du -h "$WORK_DIR/frontend-node_modules.tar.gz" | cut -f1))"

section "Empaquetando source code"
# Limpia node_modules del directorio para que el tarball del source
# no lo duplique.
rm -rf "$WORK_DIR/source/frontend/node_modules"
rm -rf "$WORK_DIR/source/.git"
( cd "$WORK_DIR" && tar --exclude="__pycache__" --exclude="*.pyc" \
                       --exclude=".pytest_cache" --exclude=".ruff_cache" \
                       --exclude=".next" --exclude=".venv" \
                       -czf crmbomedia-source.tar.gz source && \
  mv source/scripts/bootstrap-server-offline.sh . 2>/dev/null || true && \
  mv source/INSTALL-OFFLINE.md . 2>/dev/null || true )
ok "Source tarballed"

section "Recolectando configs de despliegue"
cp "$WORK_DIR/source/docker-compose.prod.yml" "$WORK_DIR/"
cp "$WORK_DIR/source/docker-compose.plesk.yml" "$WORK_DIR/"
cp "$WORK_DIR/source/.env.production.example" "$WORK_DIR/"
cp "$WORK_DIR/source/scripts/bootstrap-server-offline.sh" "$WORK_DIR/" 2>/dev/null \
  || fail "Falta scripts/bootstrap-server-offline.sh en el repo"
cp "$WORK_DIR/source/INSTALL-OFFLINE.md" "$WORK_DIR/" 2>/dev/null \
  || fail "Falta INSTALL-OFFLINE.md en el repo"
mkdir -p "$WORK_DIR/deploy/nginx"
cp -r "$WORK_DIR/source/deploy/nginx/." "$WORK_DIR/deploy/nginx/"
chmod +x "$WORK_DIR/bootstrap-server-offline.sh"
ok "Configs listos"

section "Construyendo zip final"
rm -rf "$WORK_DIR/source"  # ya está dentro del tarball
( cd "$WORK_DIR" && zip -rq "$OUT" . )
ok "Zip generado: $OUT ($(du -h "$OUT" | cut -f1))"

cat <<EOF

────────────────────────────────────────────────────────────────
✓ Bundle listo: $OUT
────────────────────────────────────────────────────────────────

Siguiente paso (en el VPS):

  scp $OUT root@VPS:/tmp/
  ssh root@VPS
  mkdir /tmp/bohub-offline && cd /tmp/bohub-offline
  unzip /tmp/$(basename "$OUT")
  sudo bash bootstrap-server-offline.sh --domain crm.tudominio.com

Para modo 100% air-gapped (sin acceso a Docker Hub desde el VPS),
ver "Nivel 2" en INSTALL-OFFLINE.md.
EOF

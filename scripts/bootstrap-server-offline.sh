#!/usr/bin/env bash
# bootstrap-server-offline.sh — Despliegue de BoHub CRM en un VPS
# usando los assets pre-empaquetados del bundle offline.
#
# Asume el archivo ZIP/TAR descomprimido en /tmp/bohub-offline/ con:
#   - crmbomedia-source.tar.gz        (código fuente)
#   - wheels/                          (Python wheels)
#   - frontend-node_modules.tar.gz     (node_modules pre-instalado)
#   - docker-compose.prod.yml
#   - docker-compose.plesk.yml
#   - .env.production.example
#   - deploy/nginx/
#
# Opcional para 100% air-gapped:
#   - docker-base-images.tar           (5 imágenes base salvadas con
#                                       `docker save`)
#
# Uso:
#   sudo bash bootstrap-server-offline.sh --domain crm.tudominio.com \
#       [--bundle-dir /tmp/bohub-offline] [--air-gapped]
#
# El --air-gapped hace `docker load` de docker-base-images.tar antes
# del build. Sin --air-gapped se asume internet outbound a Docker Hub.

set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/crmbo"
BACKUP_DIR="/var/backups/crmbo"
UPLOADS_DIR="/opt/crmbo/uploads/email-templates"
SCRIPTS_DIR="/opt/crmbo/scripts"
DOMAIN=""
USE_PLESK=false
AIR_GAPPED=false
INSTALL_TLS=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle-dir)    BUNDLE_DIR="$2"; shift 2 ;;
    --install-dir)   INSTALL_DIR="$2"; shift 2 ;;
    --domain)        DOMAIN="$2"; shift 2 ;;
    --air-gapped)    AIR_GAPPED=true; INSTALL_TLS=false; shift ;;
    --with-plesk)    USE_PLESK=true; INSTALL_TLS=false; shift ;;
    --no-tls)        INSTALL_TLS=false; shift ;;
    -h|--help)       sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 1 ;;
  esac
done

COL_BLUE='\033[1;34m'; COL_GREEN='\033[1;32m'; COL_YELLOW='\033[1;33m'
COL_RED='\033[1;31m';  COL_RESET='\033[0m'
section()  { printf "\n${COL_BLUE}==> %s${COL_RESET}\n" "$*"; }
ok()       { printf "${COL_GREEN}✓ %s${COL_RESET}\n" "$*"; }
warn()     { printf "${COL_YELLOW}! %s${COL_RESET}\n" "$*"; }
fail()     { printf "${COL_RED}✗ %s${COL_RESET}\n" "$*" >&2; exit 1; }
prompt()   { local label="$1" var="$2" default="${3:-}"; local val
             read -r -p "$label${default:+ [$default]}: " val
             eval "$var=\"\${val:-$default}\""; }
prompt_secret() {
  local label="$1" var="$2" val
  read -r -s -p "$label: " val; echo
  eval "$var=\"$val\""
}

section "Pre-flight"
[[ $EUID -eq 0 ]] || fail "Requiere root (sudo bash $0)"
[[ -d "$BUNDLE_DIR" ]] || fail "Bundle dir no existe: $BUNDLE_DIR"
for f in crmbomedia-source.tar.gz wheels docker-compose.prod.yml; do
  [[ -e "$BUNDLE_DIR/$f" ]] || fail "Falta en bundle: $f"
done
ok "Bundle: $BUNDLE_DIR"

section "Docker"
if ! command -v docker >/dev/null; then
  if [[ "$AIR_GAPPED" == "true" ]]; then
    fail "Docker no instalado y modo air-gapped activo. Instálalo a mano."
  fi
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  if [[ "$AIR_GAPPED" == "true" ]]; then
    fail "Docker Compose v2 no instalado y modo air-gapped activo."
  fi
  apt-get update -qq && apt-get install -y docker-compose-plugin
fi
ok "Docker $(docker --version) + Compose $(docker compose version --short)"

if [[ "$AIR_GAPPED" == "true" ]]; then
  section "Cargando imágenes base air-gapped"
  if [[ -f "$BUNDLE_DIR/docker-base-images.tar" ]]; then
    docker load -i "$BUNDLE_DIR/docker-base-images.tar"
    ok "Imágenes base cargadas"
  else
    fail "--air-gapped pero falta $BUNDLE_DIR/docker-base-images.tar"
  fi
fi

section "Carpetas del host"
mkdir -p "$INSTALL_DIR" "$BACKUP_DIR" "$UPLOADS_DIR" "$SCRIPTS_DIR"
chmod 755 "$INSTALL_DIR"; chmod 750 "$BACKUP_DIR"

section "Extrayendo código fuente"
if [[ -d "$INSTALL_DIR/.git" ]] || [[ -f "$INSTALL_DIR/docker-compose.prod.yml" ]]; then
  warn "$INSTALL_DIR ya contiene una instalación previa. NO sobreescribimos."
  warn "Si quieres reinstalar: para los containers y borra $INSTALL_DIR antes."
else
  tar -xzf "$BUNDLE_DIR/crmbomedia-source.tar.gz" -C /tmp
  cp -a /tmp/crmbomedia/. "$INSTALL_DIR/"
  rm -rf /tmp/crmbomedia
  ok "Repo extraído en $INSTALL_DIR"
fi

# Copia los scripts al path bind-mount esperado por worker-sync.
cp -a "$INSTALL_DIR/scripts/." "$SCRIPTS_DIR/"
chmod +x "$SCRIPTS_DIR"/*.sh 2>/dev/null || true

section "Wheels Python en bundle"
mkdir -p "$INSTALL_DIR/offline-wheels"
cp -a "$BUNDLE_DIR/wheels/." "$INSTALL_DIR/offline-wheels/"
ok "Wheels copiados ($(ls "$INSTALL_DIR/offline-wheels" | wc -l) archivos)"

section "node_modules pre-instalado"
if [[ -f "$BUNDLE_DIR/frontend-node_modules.tar.gz" ]]; then
  tar -xzf "$BUNDLE_DIR/frontend-node_modules.tar.gz" \
    -C "$INSTALL_DIR/frontend/"
  ok "node_modules extraído"
else
  warn "frontend-node_modules.tar.gz no encontrado. npm install correrá en build."
fi

section ".env.production"
ENV_FILE="$INSTALL_DIR/.env.production"
if [[ -f "$ENV_FILE" ]]; then
  warn ".env.production ya existe — lo dejamos intacto"
else
  prompt "Dominio público del CRM" DOMAIN "${DOMAIN:-crm.tudominio.com}"
  prompt "Email del admin inicial" ADMIN_EMAIL "admin@${DOMAIN#*.}"
  prompt_secret "Password del admin inicial (mínimo 12 chars)" ADMIN_PWD
  prompt_secret "Password MySQL user crm" MYSQL_PWD
  prompt_secret "Password MySQL root (para backups host)" MYSQL_ROOT_PWD
  prompt "Anthropic API key (vacío para skip IA)" ANTHROPIC_KEY ""
  prompt "SMTP host" SMTP_HOST "smtp.ionos.es"
  prompt "SMTP user" SMTP_USER "noreply@${DOMAIN#*.}"
  prompt_secret "SMTP password" SMTP_PWD

  SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null \
    || head -c 36 /dev/urandom | base64 | tr -d '/+= ')
  FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
    || head -c 32 /dev/urandom | base64)

  cp "$INSTALL_DIR/.env.production.example" "$ENV_FILE"
  sed -i \
    -e "s|^NEXT_PUBLIC_API_BASE_URL=.*|NEXT_PUBLIC_API_BASE_URL=https://$DOMAIN|" \
    -e "s|^FRONTEND_BASE_URL=.*|FRONTEND_BASE_URL=https://$DOMAIN|" \
    -e "s|^CORS_ORIGINS=.*|CORS_ORIGINS=https://$DOMAIN|" \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET_KEY|" \
    -e "s|^DEFAULT_ADMIN_EMAIL=.*|DEFAULT_ADMIN_EMAIL=$ADMIN_EMAIL|" \
    -e "s|^DEFAULT_ADMIN_PASSWORD=.*|DEFAULT_ADMIN_PASSWORD=$ADMIN_PWD|" \
    -e "s|^MYSQL_PASSWORD=.*|MYSQL_PASSWORD=$MYSQL_PWD|" \
    -e "s|^MYSQL_ROOT_PASSWORD=.*|MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PWD|" \
    -e "s|^INTEGRATION_SECRETS_KEY=.*|INTEGRATION_SECRETS_KEY=$FERNET_KEY|" \
    -e "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$ANTHROPIC_KEY|" \
    -e "s|^SMTP_HOST=.*|SMTP_HOST=$SMTP_HOST|" \
    -e "s|^SMTP_USER=.*|SMTP_USER=$SMTP_USER|" \
    -e "s|^SMTP_PASSWORD=.*|SMTP_PASSWORD=$SMTP_PWD|" \
    -e "s|^SMTP_FROM=.*|SMTP_FROM=$SMTP_USER|" \
    "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "Generado $ENV_FILE (modo 600)"
fi

section "Build de imágenes Docker"
cd "$INSTALL_DIR"
COMPOSE_FILES=("-f" "docker-compose.prod.yml")
[[ "$USE_PLESK" == "true" ]] && COMPOSE_FILES+=("-f" "docker-compose.plesk.yml")

# En air-gapped no usamos --pull (las base ya están cargadas).
BUILD_FLAGS=()
[[ "$AIR_GAPPED" != "true" ]] && BUILD_FLAGS+=("--pull")
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" build "${BUILD_FLAGS[@]}"
ok "Imágenes construidas"

section "Stack arriba"
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" up -d db redis
for _ in $(seq 1 30); do
  status="$(docker compose --env-file .env.production "${COMPOSE_FILES[@]}" ps -q db \
            | xargs -r docker inspect -f '{{.State.Health.Status}}' 2>/dev/null || true)"
  [[ "$status" == "healthy" ]] && break
  sleep 3
done
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" up -d
ok "Servicios levantados"

section "Migraciones + admin inicial"
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" exec -T api alembic upgrade head
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" exec -T api python -m app.db.init_db
ok "BD lista"

if [[ "$INSTALL_TLS" == "true" && -n "$DOMAIN" ]]; then
  section "TLS Let's Encrypt"
  command -v certbot >/dev/null || apt-get install -y certbot
  docker compose --env-file .env.production -f docker-compose.prod.yml stop nginx
  certbot certonly --standalone -d "$DOMAIN" --agree-tos --no-eff-email \
    --register-unsafely-without-email --non-interactive \
    || warn "certbot falló — configura HTTPS a mano"
  docker compose --env-file .env.production -f docker-compose.prod.yml start nginx
fi

section "Resumen"
echo "Repo:         $INSTALL_DIR"
echo ".env:         $ENV_FILE"
echo "Compose:      ${COMPOSE_FILES[*]}"
echo "Login:        https://${DOMAIN:-localhost} con $ADMIN_EMAIL"
ok "Bootstrap offline completado"

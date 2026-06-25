#!/usr/bin/env bash
# bootstrap-server.sh — Despliegue de BoHub CRM en un VPS Ubuntu/Debian
# limpio, idempotente y reproducible.
#
# Uso:
#   sudo bash bootstrap-server.sh [--repo-url URL] [--domain crm.tudominio.com] \
#                                  [--no-tls] [--with-plesk]
#
# Pasos que ejecuta (cada uno verifica si ya está hecho y lo salta):
#   1. Comprueba SO + privilegios.
#   2. Instala Docker + Docker Compose v2 vía apt/get.docker.com.
#   3. Crea estructura de carpetas en /opt/crmbo/, /var/backups/crmbo/.
#   4. Clona (o actualiza) el repo en /opt/crmbo/.
#   5. Genera `.env.production` interactivo con secretos seguros.
#   6. Build de imágenes Docker.
#   7. Sube el stack + ejecuta `alembic upgrade head` + crea admin.
#   8. (Opcional) Configura certbot + Nginx para TLS automático.
#
# Para detalles ver INSTALL.md.

set -euo pipefail

# ---------------------------------------------------------------------
# Defaults + flags
# ---------------------------------------------------------------------
REPO_URL="https://github.com/mqeurope-png/crmbomedia.git"
INSTALL_DIR="/opt/crmbo"
BACKUP_DIR="/var/backups/crmbo"
UPLOADS_DIR="/opt/crmbo/uploads/email-templates"
SCRIPTS_DIR="/opt/crmbo/scripts"
DOMAIN=""
INSTALL_TLS=true
USE_PLESK=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)       REPO_URL="$2"; shift 2 ;;
    --install-dir)    INSTALL_DIR="$2"; shift 2 ;;
    --domain)         DOMAIN="$2"; shift 2 ;;
    --no-tls)         INSTALL_TLS=false; shift ;;
    --with-plesk)     USE_PLESK=true; INSTALL_TLS=false; shift ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------
# Helpers de output
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# 1. Pre-checks
# ---------------------------------------------------------------------
section "Pre-flight checks"

if [[ $EUID -ne 0 ]]; then
  fail "Este script requiere root. Re-lanza con: sudo bash $0"
fi

if ! grep -qiE 'ubuntu|debian' /etc/os-release 2>/dev/null; then
  warn "SO no validado (testeado en Ubuntu 22.04+/24.04 y Debian 12)."
  prompt "¿Continuar de todas formas? (y/N)" CONTINUE "n"
  [[ "$CONTINUE" =~ ^[yY]$ ]] || exit 1
fi
ok "Root + SO compatible"

# ---------------------------------------------------------------------
# 2. Docker + Docker Compose
# ---------------------------------------------------------------------
section "Docker"

if ! command -v docker >/dev/null; then
  warn "Docker no instalado. Instalando vía get.docker.com…"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
  ok "Docker instalado"
else
  ok "Docker ya instalado ($(docker --version))"
fi

if ! docker compose version >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y docker-compose-plugin
fi
ok "Docker Compose v2 disponible ($(docker compose version --short))"

# ---------------------------------------------------------------------
# 3. Estructura de carpetas
# ---------------------------------------------------------------------
section "Carpetas del host"
mkdir -p "$INSTALL_DIR" "$BACKUP_DIR" "$UPLOADS_DIR" "$SCRIPTS_DIR"
chmod 755 "$INSTALL_DIR"
chmod 750 "$BACKUP_DIR"   # los backups cifrados quedan menos accesibles
ok "Directorios: $INSTALL_DIR, $BACKUP_DIR, $UPLOADS_DIR, $SCRIPTS_DIR"

# ---------------------------------------------------------------------
# 4. Clonar o actualizar el repo
# ---------------------------------------------------------------------
section "Repositorio CRM"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  ok "Repo ya clonado en $INSTALL_DIR — haciendo git pull"
  git -C "$INSTALL_DIR" pull --ff-only
else
  if ! command -v git >/dev/null; then
    apt-get install -y git
  fi
  git clone "$REPO_URL" "$INSTALL_DIR"
  ok "Repo clonado desde $REPO_URL"
fi

# Copia de los scripts al path bind-mount esperado por worker-sync.
cp -a "$INSTALL_DIR/scripts/." "$SCRIPTS_DIR/"
chmod +x "$SCRIPTS_DIR"/*.sh 2>/dev/null || true

# ---------------------------------------------------------------------
# 5. .env.production
# ---------------------------------------------------------------------
section ".env.production"
ENV_FILE="$INSTALL_DIR/.env.production"

if [[ -f "$ENV_FILE" ]]; then
  warn ".env.production ya existe. Lo dejamos intacto."
  warn "Para regenerar: borrarlo a mano y volver a ejecutar el bootstrap."
else
  ok "Generando .env.production interactivo…"
  # Defaults sensatos donde aplique
  prompt "Dominio público del CRM" DOMAIN "${DOMAIN:-crm.tudominio.com}"
  prompt "Email del admin inicial" ADMIN_EMAIL "admin@${DOMAIN#*.}"
  prompt_secret "Password del admin inicial (mínimo 12 chars)" ADMIN_PWD
  prompt_secret "Password del usuario MySQL crm" MYSQL_PWD
  prompt_secret "Password root MySQL (sólo para backups host)" MYSQL_ROOT_PWD
  prompt "Anthropic API key (vacío para deshabilitar IA)" ANTHROPIC_KEY ""
  prompt "SMTP host (relay para emails CRM)" SMTP_HOST "smtp.ionos.es"
  prompt "SMTP user (mailbox)" SMTP_USER "noreply@${DOMAIN#*.}"
  prompt_secret "SMTP password" SMTP_PWD

  # Generables automáticos.
  SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
    2>/dev/null || \
    docker run --rm python:3.11-slim sh -c "pip install -q cryptography && python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")

  cp "$INSTALL_DIR/.env.production.example" "$ENV_FILE"
  # Sustitución in-place de los placeholders.
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

# ---------------------------------------------------------------------
# 6. Build de imágenes
# ---------------------------------------------------------------------
section "Build de imágenes Docker"
cd "$INSTALL_DIR"

COMPOSE_FILES=("-f" "docker-compose.prod.yml")
if [[ "$USE_PLESK" == "true" ]]; then
  COMPOSE_FILES+=("-f" "docker-compose.plesk.yml")
  ok "Override Plesk activado (puerto interno 127.0.0.1:8080)"
fi

docker compose --env-file .env.production "${COMPOSE_FILES[@]}" build --pull
ok "Imágenes construidas"

# ---------------------------------------------------------------------
# 7. Arrancar stack + migraciones + admin
# ---------------------------------------------------------------------
section "Arrancando stack"
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" up -d db redis
ok "MySQL + Redis iniciados — esperando healthy…"

# Espera activa hasta que MySQL esté healthy (máx 90 s).
for _ in $(seq 1 30); do
  if [[ "$(docker compose --env-file .env.production "${COMPOSE_FILES[@]}" \
            ps -q db | xargs docker inspect -f '{{.State.Health.Status}}' 2>/dev/null)" == "healthy" ]]; then
    break
  fi
  sleep 3
done

docker compose --env-file .env.production "${COMPOSE_FILES[@]}" up -d
ok "api + frontend + worker-sync + worker-workflows + nginx levantados"

section "Migraciones + admin inicial"
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" \
  exec -T api alembic upgrade head
ok "Alembic upgrade head OK"

# El init_db es idempotente: si el admin ya existe (re-ejecución del
# bootstrap) no toca nada.
docker compose --env-file .env.production "${COMPOSE_FILES[@]}" \
  exec -T api python -m app.db.init_db
ok "Admin inicial garantizado ($ADMIN_EMAIL)"

# ---------------------------------------------------------------------
# 8. TLS automático (certbot)
# ---------------------------------------------------------------------
if [[ "$INSTALL_TLS" == "true" && -n "$DOMAIN" ]]; then
  section "TLS automático con certbot"
  if ! command -v certbot >/dev/null; then
    apt-get install -y certbot
  fi
  warn "Vamos a pedir un cert para $DOMAIN. Asegúrate de que el DNS"
  warn "ya apunta a este servidor + el puerto 80 está abierto."
  prompt "¿Continuar? (y/N)" CONTINUE_TLS "y"
  if [[ "$CONTINUE_TLS" =~ ^[yY]$ ]]; then
    # Parar nginx temporalmente para que certbot use standalone:80.
    docker compose --env-file .env.production -f docker-compose.prod.yml stop nginx
    certbot certonly --standalone -d "$DOMAIN" --agree-tos --no-eff-email \
      --register-unsafely-without-email --non-interactive || \
      warn "certbot falló — revisa DNS/firewall y re-ejecuta a mano."
    docker compose --env-file .env.production -f docker-compose.prod.yml start nginx
    ok "Cert pedido (revisa /etc/letsencrypt/live/$DOMAIN/)"
  else
    warn "TLS saltado. Configura HTTPS a mano antes de exponer el CRM."
  fi
fi

# ---------------------------------------------------------------------
# Final
# ---------------------------------------------------------------------
section "Resumen"
echo "Repo:           $INSTALL_DIR"
echo ".env:           $ENV_FILE"
echo "Backups:        $BACKUP_DIR"
echo "Uploads:        $UPLOADS_DIR"
echo "Compose files:  ${COMPOSE_FILES[*]}"
echo
echo "Comprobaciones:"
echo "  docker compose ${COMPOSE_FILES[*]} ps"
echo "  docker compose ${COMPOSE_FILES[*]} logs -f api worker-sync worker-workflows"
echo
echo "Login: https://${DOMAIN:-localhost} con $ADMIN_EMAIL"
ok "Bootstrap completado"

# Despliegue en IONOS (producción)

Guía mínima para llevar CRMBO Media CRM a un VPS o Cloud Server de IONOS con HTTPS, base de datos persistente y backups verificables. No incluye conectores externos: el objetivo es que la base productiva sea estable antes de añadir AgileCRM, Brevo, Freshdesk o FactuSOL.

## 1. Requisitos previos

- VPS IONOS con Ubuntu 22.04 LTS (o equivalente).
- Acceso SSH como usuario con `sudo`.
- Dominio apuntando al servidor con un registro `A` (y opcional `AAAA`) en IONOS DNS, p. ej. `crm.tudominio.com`.
- Puertos TCP 80 y 443 abiertos en el firewall de IONOS y en `ufw`/`iptables` del host. Los puertos 3306 (MySQL) y 6379 (Redis) deben estar **cerrados** públicamente.

## 2. Preparar el host

Como root o con `sudo`:

```bash
apt update && apt upgrade -y
apt install -y ca-certificates curl gnupg ufw certbot

# Docker Engine + Compose plugin
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Crea un usuario de despliegue (opcional pero recomendado):

```bash
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
```

## 3. Clonar el repositorio

```bash
sudo mkdir -p /opt/crmbomedia
sudo chown deploy:deploy /opt/crmbomedia
sudo -iu deploy
git clone https://github.com/mqeurope-png/crmbomedia.git /opt/crmbomedia
cd /opt/crmbomedia
```

## 4. Configurar variables de entorno

```bash
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

Genera secretos fuertes para `SECRET_KEY`, `DEFAULT_ADMIN_PASSWORD`, `MYSQL_PASSWORD` y `MYSQL_ROOT_PASSWORD`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

`.env.production` está en `.gitignore` y no debe subirse jamás al repo.

## 5. Configurar Nginx

Sustituye el dominio en las plantillas:

```bash
mkdir -p deploy/nginx/conf.d
sed 's/CRM_DOMAIN/crm.tudominio.com/g' \
  deploy/nginx/conf.d/bootstrap.conf.example \
  > deploy/nginx/conf.d/bootstrap.conf
```

`bootstrap.conf` solo sirve durante la primera emisión del certificado.

## 6. Bootstrap de Let's Encrypt

Antes de arrancar la pila completa, hay que conseguir el primer certificado. Hay dos opciones; usa **Opción A** salvo que ya estés sirviendo otro sitio en el puerto 80.

### Opción A — certbot standalone (recomendada para primer despliegue)

```bash
sudo systemctl stop nginx 2>/dev/null || true
sudo certbot certonly --standalone \
  -d crm.tudominio.com \
  --email tu-email@tudominio.com \
  --agree-tos --no-eff-email
```

Esto deja los certificados en `/etc/letsencrypt/live/crm.tudominio.com/`.

### Opción B — webroot (para renovar sin parar Nginx)

Útil para renovaciones futuras una vez la pila esté en marcha:

```bash
sudo certbot certonly --webroot \
  -w /var/lib/docker/volumes/crmbomedia_certbot_webroot/_data \
  -d crm.tudominio.com \
  --email tu-email@tudominio.com \
  --agree-tos --no-eff-email
```

## 7. Activar la configuración Nginx definitiva

Una vez existe `/etc/letsencrypt/live/crm.tudominio.com/fullchain.pem`:

```bash
rm deploy/nginx/conf.d/bootstrap.conf
sed 's/CRM_DOMAIN/crm.tudominio.com/g' \
  deploy/nginx/conf.d/app.conf.example \
  > deploy/nginx/conf.d/app.conf
```

`app.conf` y `bootstrap.conf` están en `.gitignore` (sufijo `.conf` dentro de `deploy/nginx/conf.d/` se gestiona por las plantillas `.example`).

## 8. Arrancar la pila

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api
```

El servicio `api` ejecuta `alembic upgrade head` y `python -m app.db.init_db` al arrancar, así que la base se crea automáticamente y se siembra el usuario admin definido en `.env.production`.

Comprueba:

- `https://crm.tudominio.com/` → frontend Next.js.
- `https://crm.tudominio.com/api/health` → `{"status":"ok",...}`.
- `https://crm.tudominio.com/api/docs` → Swagger.

## 9. Renovación automática del certificado

`certbot` instala un timer systemd (`certbot.timer`) que renueva dos veces al día. Para que Nginx recargue tras renovar, añade un hook:

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/usr/bin/env bash
cd /opt/crmbomedia
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

Verifica el ciclo:

```bash
sudo certbot renew --dry-run
```

## 10. Backups MySQL

El script `scripts/backup-mysql.sh` hace `mysqldump --single-transaction` desde el host, lo comprime y lo rota.

Prueba manual:

```bash
cd /opt/crmbomedia
./scripts/backup-mysql.sh
ls -lh /var/backups/crmbomedia
```

Añade a cron (como `root` o el usuario `deploy` con acceso a docker):

```cron
# /etc/cron.d/crmbomedia-backup
0 3 * * * deploy /opt/crmbomedia/scripts/backup-mysql.sh >> /var/log/crm-backup.log 2>&1
```

Variables:

- `BACKUP_DIR` (por defecto `/var/backups/crmbomedia`).
- `RETENTION_DAYS` (por defecto `14`).

Para protección frente a fallo total del host, copia los backups a almacenamiento externo (S3, IONOS HiDrive, otro servidor) con `rclone` o `rsync`.

## 11. Restauración

```bash
cd /opt/crmbomedia
docker compose -f docker-compose.prod.yml stop api
./scripts/restore-mysql.sh /var/backups/crmbomedia/crm-YYYYMMDDTHHMMSSZ.sql.gz
docker compose -f docker-compose.prod.yml start api
```

El script pide confirmación interactiva (`RESTORE`) salvo que se invoque con `ASSUME_YES=1`.

**Prueba de restauración**: una vez al mes, restaura el backup más reciente en un host staging o en un volumen alternativo y valida que la app arranca y los logins funcionan. Sin esta prueba el backup no es de fiar.

## 12. Operaciones del día a día

```bash
# Ver estado
docker compose -f docker-compose.prod.yml ps

# Logs en vivo
docker compose -f docker-compose.prod.yml logs -f api nginx

# Aplicar cambios de código (después de git pull)
docker compose -f docker-compose.prod.yml up -d --build

# Reiniciar un único servicio
docker compose -f docker-compose.prod.yml restart api

# Acceder a un shell en el contenedor api
docker compose -f docker-compose.prod.yml exec api bash
```

## 13. Checklist mínimo antes de abrir al público

- [ ] `SECRET_KEY` y todas las contraseñas regeneradas (no son las del `.example`).
- [ ] `DEFAULT_ADMIN_PASSWORD` cambiada inmediatamente tras el primer login.
- [ ] `https://crm.tudominio.com` carga con candado verde y `Strict-Transport-Security`.
- [ ] `nmap -p 3306,6379 servidor` desde fuera devuelve filtered/closed.
- [ ] Backup manual ejecutado y restaurado con éxito en staging.
- [ ] Cron de backup activo y archivo aparece al día siguiente.
- [ ] `certbot renew --dry-run` finaliza sin errores.
- [ ] Logs de `api` y `nginx` rotan (configurado en compose: 10 MB × 5).
- [ ] Sentry u otro colector de errores conectado (pendiente — fuera del alcance de esta entrega).

# Instalación de BoHub CRM en un VPS nuevo

Despliega el CRM completo (backend FastAPI + frontend Next.js + MySQL 8 +
Redis 7 + Nginx) en un servidor Ubuntu/Debian limpio con un solo comando.

## Requisitos mínimos del servidor

- **SO**: Ubuntu 22.04 / 24.04 LTS o Debian 12.
- **RAM**: 4 GB (8 GB recomendados para syncs grandes).
- **CPU**: 2 vCPU.
- **Disco**: 40 GB SSD (incluye imágenes Docker + datos + backups).
- **Red**: IP pública con puertos 80 + 443 abiertos.
- **DNS**: registro A apuntando al servidor (ej. `crm.tudominio.com → 1.2.3.4`).

Lo único que necesitas instalar a mano antes del bootstrap es `curl` (suele
venir preinstalado).

## Quickstart

```bash
# 1. Como root en el VPS limpio:
curl -fsSL https://raw.githubusercontent.com/mqeurope-png/crmbomedia/main/scripts/bootstrap-server.sh \
  -o /tmp/bootstrap.sh

# 2. Lanzar el bootstrap (te pedirá los secretos durante la ejecución):
sudo bash /tmp/bootstrap.sh --domain crm.tudominio.com

# 3. Tras ~5-10 min ya tendrás:
#    - Docker + Docker Compose instalados.
#    - Repo clonado en /opt/crmbo/.
#    - .env.production generado con secretos seguros (Fernet + JWT).
#    - Imágenes Docker construidas.
#    - Stack levantado y migraciones aplicadas.
#    - Admin inicial creado.
#    - (Opcional) Certificado TLS de Let's Encrypt.

# 4. Login en https://crm.tudominio.com con el admin que configuraste.
```

### Flags del bootstrap

| Flag | Para qué |
|---|---|
| `--domain crm.tudominio.com` | Dominio público que se usará en CORS + URLs |
| `--repo-url URL` | Repo alternativo (fork privado, mirror interno) |
| `--install-dir /opt/crmbo` | Carpeta de instalación (por defecto `/opt/crmbo`) |
| `--no-tls` | No pide cert Let's Encrypt (lo configuras a mano) |
| `--with-plesk` | Override para hosts con Plesk en 80/443 — el nginx interno se publica en `127.0.0.1:8080` |

## Qué hace el script paso a paso

1. **Pre-flight**: verifica root + SO compatible.
2. **Docker**: instala Docker Engine vía `get.docker.com` + plugin Compose v2 si faltan.
3. **Carpetas**: crea `/opt/crmbo/`, `/opt/crmbo/uploads/email-templates/`, `/opt/crmbo/scripts/`, `/var/backups/crmbo/` con permisos 750.
4. **Repo**: clona o actualiza el código en `/opt/crmbo/`.
5. **`.env.production`**: te pregunta interactivamente:
   - Dominio público.
   - Email + password del admin inicial.
   - Password MySQL del user `crm` + root.
   - SMTP (host + user + pass).
   - Anthropic API key (opcional para IA).
   Y autogenera:
   - `SECRET_KEY` JWT (48 chars random).
   - `INTEGRATION_SECRETS_KEY` Fernet (44 chars base64).
   El archivo queda con permisos `600` para que solo root lo lea.
6. **Build**: `docker compose build --pull` de las 5 imágenes (db, redis, api+workers, frontend, nginx).
7. **Up**: levanta MySQL + Redis, espera healthy, levanta el resto, corre `alembic upgrade head`, garantiza el admin inicial (`init_db.py` es idempotente).
8. **TLS**: corre `certbot certonly --standalone -d <dominio>` para emitir el cert Let's Encrypt y reinicia nginx.

Todos los pasos son **idempotentes**: si re-ejecutas el script con el mismo dominio,
detecta que `.env.production` ya existe (y NO lo regenera), salta la instalación
de Docker si ya está, y se limita a `docker compose up -d`.

## Estructura post-instalación

```
/opt/crmbo/                       # repo clonado
├── .env.production               # secretos (modo 600)
├── docker-compose.prod.yml
├── docker-compose.plesk.yml
├── backend/                      # código FastAPI
├── frontend/                     # código Next.js
├── deploy/nginx/                 # config nginx
├── scripts/                      # scripts de backup, restore
└── uploads/email-templates/      # imágenes inline de emails

/var/backups/crmbo/               # backups cifrados (modo 750)
/etc/letsencrypt/                 # certs TLS gestionados por certbot
```

## Operaciones diarias

```bash
cd /opt/crmbo

# Ver estado
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# Logs en vivo
docker compose --env-file .env.production -f docker-compose.prod.yml \
  logs -f api worker-sync worker-workflows

# Reiniciar tras pull del repo
git pull
docker compose --env-file .env.production -f docker-compose.prod.yml \
  up -d --build --force-recreate api worker-sync worker-workflows frontend
docker compose --env-file .env.production -f docker-compose.prod.yml \
  exec api alembic upgrade head

# Backup manual (también hay cron automático cada 72h en el worker-sync)
docker compose --env-file .env.production -f docker-compose.prod.yml \
  exec api python -c "from app.workers.jobs import enqueue_sync_job; \
    from app.db.session import get_session; \
    next(get_session()); print('backup enqueued')"
```

## Restaurar desde backup

Ver `docs/backup-restore.md` en el repo: stop api+workers, restore SQL,
volver a levantar.

## Renovación TLS

Certbot programa un cron en `/etc/cron.d/certbot` que renueva los certs
automáticamente cada 60 días. Para forzar manualmente:

```bash
sudo certbot renew
docker compose -f docker-compose.prod.yml restart nginx
```

## Desinstalar

```bash
cd /opt/crmbo
docker compose --env-file .env.production -f docker-compose.prod.yml down -v
sudo rm -rf /opt/crmbo /var/backups/crmbo
sudo certbot delete  # si se quiere quitar el cert
```

## Troubleshooting

- **El backend no arranca con "INTEGRATION_SECRETS_KEY required"** → el `.env.production` no se generó correctamente. Borra el archivo y re-ejecuta el bootstrap.
- **`docker compose ps` muestra db unhealthy** → revisa `docker compose logs db`. Los más comunes: MYSQL_ROOT_PASSWORD vacío o el volumen `mysql_data` huérfano de una instalación previa con otra password (`docker volume rm crmbo_mysql_data` y reinicia).
- **Worker no procesa workflows** → confirma que `worker-workflows` está corriendo y que `workflows:dispatch` no acumula jobs huérfanos (`docker compose exec redis redis-cli LLEN rq:queue:workflows:dispatch`).
- **TLS fail en certbot** → verifica DNS + firewall + que nginx no esté escuchando en :80 cuando lanzas `certbot --standalone`.

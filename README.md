# CRMBO Media — CRM central e integraciones

Base técnica inicial para construir una plataforma CRM propia con conectores hacia AgileCRM, Brevo, Freshdesk y FactuSOL/DELSOL.

El repositorio sigue el roadmap de `roadmap_crm_integraciones.md`: la app propia es el centro, los sistemas externos son conectores y el modelo de datos interno no depende de proveedores.

> Estado: MVP técnico. No está listo para producción ni contiene integraciones externas reales.

## Stack

- **Frontend:** Next.js + React + TypeScript.
- **Backend:** FastAPI + SQLAlchemy + Pydantic.
- **Base de datos:** MySQL 8.
- **Migraciones:** Alembic.
- **Colas/cache:** Redis preparado para workers futuros.
- **Orquestación local:** Docker Compose.
- **CI:** GitHub Actions.

## Estructura

```text
backend/                  API FastAPI, modelos, migraciones y tests
backend/app/api/          Rutas HTTP
backend/app/core/         Configuración y errores comunes
backend/app/db/           Sesiones de base de datos
backend/app/models/       Modelos SQLAlchemy
backend/app/repositories/ Acceso a datos
backend/app/services/     Casos de uso futuros
backend/app/integrations/ Placeholders de conectores externos
backend/app/workers/      Workers futuros
frontend/                 Interfaz web Next.js
docs/                     Especificación, seguridad y desarrollo
scripts/                  Utilidades locales
```



## GitHub Codespaces

Puedes probar el proyecto sin preparar tu máquina local usando GitHub Codespaces:

1. En GitHub, abre el repositorio y pulsa **Code → Codespaces → Create codespace on main**.
2. Espera a que Codespaces construya el contenedor. La configuración de `.devcontainer/devcontainer.json` instala Python 3.12, Node.js 20 y Docker-in-Docker, y ejecuta automáticamente:

   ```bash
   ./scripts/setup.sh
   ```

3. Si necesitas repetir la instalación manualmente, ejecuta:

   ```bash
   ./scripts/setup.sh
   ```

4. Ejecuta las comprobaciones de desarrollo:

   ```bash
   ./scripts/dev-check.sh
   ```

5. Arranca MySQL, Redis, backend y frontend con Docker Compose:

   ```bash
   docker compose up --build
   ```

6. Abre los puertos reenviados desde la pestaña **Ports** de Codespaces:

   - `3000`: frontend Next.js.
   - `8000`: API FastAPI y OpenAPI en `/docs`.

La configuración usa Docker-in-Docker para que `docker compose up --build` funcione dentro de Codespaces. Si tu organización deshabilita Docker-in-Docker, usa los comandos locales documentados en la sección de desarrollo y levanta MySQL/Redis con servicios externos o un Codespace con permisos de Docker habilitados.

## Puesta en marcha rápida

1. Prepara dependencias locales:

   ```bash
   ./scripts/setup.sh
   ```

2. Ejecuta la validación completa de desarrollo:

   ```bash
   ./scripts/dev-check.sh
   ```

3. Arranca todo el stack local:

   ```bash
   docker compose up --build
   ```

## Variables de entorno

Copia `.env.example` a `.env` antes de usar Docker Compose.

```bash
cp .env.example .env
```

Variables principales:

- `APP_NAME`: nombre mostrado por la API.
- `ENVIRONMENT`: entorno (`development`, `staging`, `production`).
- `DATABASE_URL`: URL SQLAlchemy de MySQL (driver `pymysql`).
- `REDIS_URL`: URL Redis para colas/cache futuras.
- `CORS_ORIGINS`: orígenes permitidos para el frontend.
- `SECRET_KEY`: secreto local; debe cambiarse antes de producción.
- `ACCESS_TOKEN_EXPIRE_MINUTES`: duración del JWT en minutos.
- `DEFAULT_ADMIN_EMAIL`: email del usuario admin creado al arrancar Docker si no existe.
- `DEFAULT_ADMIN_PASSWORD`: contraseña inicial del admin; cámbiala en entornos reales.
- `NEXT_PUBLIC_API_BASE_URL`: URL pública que usa el frontend para llamar al backend.

No commits secretos ni API keys reales.

## Arranque con Docker

```bash
docker compose up --build
```

Servicios por defecto:

- Frontend: <http://localhost:3000>
- API: <http://localhost:8000>
- Swagger/OpenAPI: <http://localhost:8000/docs>

Comandos útiles:

```bash
docker compose down
docker compose down -v
```

`docker compose down -v` elimina el volumen local de MySQL.

## Desarrollo backend sin Docker

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.db.init_db
python -m pytest
uvicorn app.main:app --reload
```

### Migraciones

```bash
cd backend
alembic upgrade head
alembic revision --autogenerate -m "message"
```

La primera migración crea el modelo CRM actual: empresas, contactos, notas, tareas, referencias externas y logs de sincronización.

## Desarrollo frontend sin Docker

```bash
cd frontend
npm install
npm run build
npm run lint
npm run dev
```

## Checks locales

```bash
./scripts/dev-check.sh
```

`./scripts/check.sh` se mantiene como alias de `./scripts/dev-check.sh`. El script ejecuta lint/compile/tests de backend y build/lint de frontend. Si faltan dependencias, falla con un mensaje indicando cómo instalarlas.

## Autenticación y permisos

El backend usa tokens JWT firmados con `SECRET_KEY`. El entrypoint de Docker ejecuta `python -m app.db.init_db` para crear un admin inicial si no existe.

Roles mínimos:

- `viewer`: solo lectura.
- `user`: lectura y creación de notas/tareas.
- `manager`: creación/edición/desactivación de contactos y empresas.
- `admin`: permisos de manager más gestión de usuarios y consulta de auditoría.

## API actual

- `GET /api/health`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/change-password`
- `POST /api/auth/password-reset/request`
- `POST /api/auth/password-reset/confirm`
- `POST /api/users`
- `GET /api/users`
- `PATCH /api/users/{user_id}`
- `PATCH /api/users/{user_id}/password`
- `PATCH /api/users/{user_id}/deactivate`
- `PATCH /api/users/{user_id}/reactivate`
- `GET /api/audit-logs`
- `GET /api/audit-logs/export?format=csv|json`
- `POST /api/companies`
- `GET /api/companies?q=&skip=&limit=`
- `PATCH /api/companies/{company_id}`
- `PATCH /api/companies/{company_id}/deactivate`
- `POST /api/contacts`
- `GET /api/contacts?q=&skip=&limit=`
- `GET /api/contacts/{contact_id}`
- `PATCH /api/contacts/{contact_id}`
- `PATCH /api/contacts/{contact_id}/deactivate`
- `GET /api/contacts/{contact_id}/notes`
- `POST /api/contacts/{contact_id}/notes`
- `GET /api/contacts/{contact_id}/tasks`
- `POST /api/contacts/{contact_id}/tasks`

Reglas relevantes:

- El email se normaliza en minúsculas y debe ser único.
- Duplicar un email devuelve `409 Conflict`.
- Payloads inválidos devuelven `422 Validation Error`.
- Recursos inexistentes devuelven `404 Not Found`.
- Las notas y tareas solo pueden crearse para contactos existentes.
- Contactos y empresas se desactivan con soft-delete (`is_active = false`).
- Las acciones relevantes generan entradas en `audit_logs`.
- La recuperación de contraseña está stubbeada: no envía email real y devuelve token en la respuesta para pruebas.
- La exportación de auditoría permite CSV y JSON mediante endpoint protegido de admin.

## Limitaciones conocidas

- No hay integración real con AgileCRM, Brevo, Freshdesk ni FactuSOL.
- No hay webhooks Brevo ni constructor de campañas.
- No hay auditoría completa ni cifrado de API keys persistidas.
- La UI es una interfaz CRM mínima para validar la base técnica.

## Despliegue en IONOS (producción)

La pila productiva está separada del entorno de desarrollo. Resumen:

```bash
# En el VPS IONOS (Ubuntu 22.04)
git clone https://github.com/mqeurope-png/crmbomedia.git /opt/crmbomedia
cd /opt/crmbomedia
cp .env.production.example .env.production && chmod 600 .env.production
nano .env.production   # generar SECRET_KEY y contraseñas reales

# Bootstrap del certificado Let's Encrypt
sudo certbot certonly --standalone -d crm.tudominio.com

# Configurar Nginx con tu dominio
sed 's/CRM_DOMAIN/crm.tudominio.com/g' \
  deploy/nginx/conf.d/app.conf.example \
  > deploy/nginx/conf.d/app.conf

# Arrancar la pila
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Ficheros relevantes:

- `docker-compose.prod.yml`: pila productiva (db, redis, api, frontend, nginx). MySQL y Redis sin puertos publicados; solo Nginx expone 80/443.
- `deploy/nginx/`: configuración Nginx + plantillas de vhost (`app.conf.example`, `bootstrap.conf.example`).
- `.env.production.example`: plantilla de variables. `.env.production` está en `.gitignore`.
- `scripts/backup-mysql.sh`: dump comprimido con `mysqldump --single-transaction` y rotación.
- `scripts/restore-mysql.sh`: restauración interactiva desde un `.sql.gz`.
- `docs/deployment-ionos.md`: guía completa paso a paso (firewall, certbot, hooks de renovación, cron de backups, checklist).

### Backups

Hay dos capas. La capa local (rotación en disco del VPS) se cubre con los scripts iniciales:

```bash
./scripts/backup-mysql.sh                       # manual, dump local
# /etc/cron.d/crmbomedia-backup
0 3 * * * deploy /opt/crmbomedia/scripts/backup-mysql.sh >> /var/log/crm-backup.log 2>&1
```

Para la capa off-site (cifrada con restic, subida a IONOS HiDrive vía rclone/WebDAV), ver
[`docs/backups-and-restore.md`](docs/backups-and-restore.md). El setup es:

```bash
sudo bash /opt/crmbo/scripts/setup-restic-hidrive.sh   # idempotente, instala cron diario
sudo bash /opt/crmbo/scripts/test-backup-hidrive.sh    # verificación end-to-end manual
```

El cron diario (`/etc/cron.d/crmbo-backup`) hace `mysqldump → gzip → restic backup → HiDrive` a las 03:00 UTC y aplica retención `--keep-daily 7 --keep-weekly 4 --keep-monthly 12`.

### Restauración

Capa local:

```bash
docker compose -f docker-compose.prod.yml stop api
./scripts/restore-mysql.sh /var/backups/crmbomedia/crm-YYYYMMDDTHHMMSSZ.sql.gz
docker compose -f docker-compose.prod.yml start api
```

Capa off-site (desde HiDrive):

```bash
sudo bash /opt/crmbo/scripts/restore-mysql-restic.sh latest --dry-run  # simula
sudo bash /opt/crmbo/scripts/restore-mysql-restic.sh latest             # ejecuta
```

## Requisitos cubiertos por la pila productiva

- HTTPS mediante Nginx + Let's Encrypt (TLS 1.2/1.3, HSTS).
- MySQL y Redis no expuestos públicamente (sin `ports:` en compose prod).
- `SECRET_KEY` y credenciales fuera del repo (`.env.production` gitignored).
- Volumen MySQL persistente (`mysql_data`).
- Backups con `mysqldump --single-transaction` y restore documentado.
- Healthchecks en db, redis, api, frontend y nginx.
- Logs rotados (`json-file`, 10 MB × 5).

## Pendiente para hardening de producción

- 2FA para administradores.
- Cifrado de API keys de conectores en reposo.
- Sentry u otro colector de errores.
- Verificación periódica del backup mediante restauración en staging.
- Respeto estricto de consentimientos y bajas antes de cualquier integración de marketing.

## Próximos hitos recomendados

1. Gestión completa de usuarios desde UI.
2. Permisos de exportación y auditoría ampliada.
3. Recuperación/cambio de contraseña.
4. Conector AgileCRM de solo lectura en entorno de pruebas.
5. Conector Brevo sin campañas, respetando bajas y consentimientos.

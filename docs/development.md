# Desarrollo local

## Objetivo

Mantener el MVP ejecutable, testeable y fácil de extender sin integrar todavía proveedores externos.


## GitHub Codespaces

El repositorio incluye `.devcontainer/devcontainer.json` con Python 3.12, Node.js 20, Docker-in-Docker y reenvío de puertos `8000` y `3000`.

Flujo recomendado:

```bash
./scripts/setup.sh
./scripts/dev-check.sh
docker compose up --build
```

Abre el puerto `3000` para el frontend y `8000` para FastAPI. La UI de OpenAPI vive en `http://localhost:8000/api/docs` (Swagger), `http://localhost:8000/api/redoc` (ReDoc) y el schema en `http://localhost:8000/api/openapi.json`; en producción el reverse proxy enruta solo `/api/*` al backend, así que estas rutas conviven con el frontend en el mismo dominio. MySQL y Redis se levantan desde `docker-compose.yml` al ejecutar Docker Compose.

## Backend

Instalación local:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.db.init_db
python -m pytest
```

El backend usa MySQL 8 como base objetivo. Los tests usan SQLite en memoria porque los modelos actuales son compatibles y permite pruebas rápidas sin levantar servicios externos.

## Worker de integraciones

El stack incluye un servicio `worker` (RQ sobre Redis) que ejecuta los jobs de los conectores externos. En `docker-compose.yml` y `docker-compose.prod.yml` el contenedor `worker` reutiliza la imagen `crmbomedia-api:latest`; basta con un `docker compose build` para que tanto `api` como `worker` arranquen con el código actualizado.

Para depurar en local sin Docker (Redis local en `localhost:6379`):

```bash
cd backend
source .venv/bin/activate
export REDIS_URL=redis://localhost:6379/0
rq worker --url $REDIS_URL agilecrm:sync_contacts brevo:push_contact
```

Inspeccionar colas y jobs vivos:

```bash
rq info --url redis://localhost:6379/0
```

Arquitectura completa del cliente HTTP base, el patrón de jobs y el endpoint genérico de webhooks: `docs/integrations-architecture.md`.

## Usuario admin inicial

Configura estas variables en `.env`:

```env
DEFAULT_ADMIN_EMAIL=admin@example.com
DEFAULT_ADMIN_PASSWORD=change-me-admin-password
SECRET_KEY=change-me-before-production
```

`python -m app.db.init_db` crea el usuario admin si no existe. Cambia la contraseña antes de usar datos reales.

## Flujos de contraseña

- Usuario autenticado: `POST /api/auth/change-password`.
- Recuperación stub: `POST /api/auth/password-reset/request` devuelve un token de prueba si el usuario existe y está activo.
- Confirmación: `POST /api/auth/password-reset/confirm` consume el token y cambia la contraseña.
- Admin: `PATCH /api/users/{user_id}/password` permite establecer una contraseña nueva.

## Roles

- `viewer`: solo lectura.
- `user`: puede crear notas y tareas.
- `manager`: puede crear/editar/desactivar contactos y empresas.
- `admin`: puede gestionar usuarios y consultar auditoría.

## Frontend

```bash
cd frontend
npm install
npm run build
npm run lint
npm run dev
```

La URL de API se configura con `NEXT_PUBLIC_API_BASE_URL`. Si el backend no responde o falta sesión, la UI muestra un estado de error y enlace a login.

## Docker Compose

```bash
docker compose up --build
docker compose down
docker compose down -v
```

El servicio `api` ejecuta `alembic upgrade head` y `python -m app.db.init_db` al arrancar. MySQL persiste datos en el volumen `mysql_data`.

## Exportación de auditoría

`GET /api/audit-logs/export?format=csv` devuelve `text/csv`. `format=json` devuelve una lista JSON descargable. Ambos requieren rol `admin`.

## Integraciones externas

Los paquetes bajo `backend/app/integrations/` son placeholders. No deben contener llamadas reales ni credenciales hasta que exista una tarea específica para cada conector.

## Comprobación local

```bash
./scripts/check.sh
```

Si hay restricciones de proxy/registro, no se debe simular éxito. Documenta el comando exacto y el error recibido.

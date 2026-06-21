# Instalación offline de BoHub CRM

Paquete autocontenido para desplegar el CRM en un VPS sin (o con poco)
acceso a internet, con todas las dependencias Python + frontend ya
descargadas. No incluye datos de contactos: la BD arranca vacía y se
populará vía login + uso normal.

## Contenido del paquete

```
bootstrap-server-offline.sh        # script de instalación
INSTALL-OFFLINE.md                 # este archivo
crmbomedia-source.tar.gz           # ~10 MB  — código fuente del repo
wheels/                            # ~40 MB  — wheels Python offline
frontend-node_modules.tar.gz       # ~185 MB — node_modules pre-instalado
docker-compose.prod.yml            # orquestación
docker-compose.plesk.yml           # override Plesk (opcional)
.env.production.example            # template de secretos
deploy/nginx/                      # configs nginx
```

**Tamaño total**: ~245 MB.

## Niveles de "offline"

Dependiendo de cuánto acceso a internet tenga el VPS:

### Nivel 1 — VPS con internet outbound (uso normal)

El bundle se aprovecha para evitar las descargas más pesadas (`npm install`
del frontend, wheels Python). Las **imágenes base Docker** (mysql:8,
redis:7-alpine, nginx:1.27-alpine, python:3.12-slim, node:20-alpine, ~500 MB
en total) se descargan de Docker Hub durante el build.

```bash
# En el VPS, tras descomprimir el zip:
sudo bash bootstrap-server-offline.sh --domain crm.tudominio.com
```

### Nivel 2 — VPS air-gapped (sin acceso a internet)

Necesitas pre-construir el archivo `docker-base-images.tar` en una máquina
con acceso a Docker Hub, copiarlo al VPS junto con el bundle, y lanzar el
bootstrap con `--air-gapped`.

**Paso A — en una máquina con Docker Hub abierto** (puede ser tu portátil):

```bash
# Descarga las 5 imágenes base requeridas.
docker pull mysql:8
docker pull redis:7-alpine
docker pull nginx:1.27-alpine
docker pull python:3.12-slim
docker pull node:20-alpine

# Guarda todas como un único tar.
docker save \
  mysql:8 \
  redis:7-alpine \
  nginx:1.27-alpine \
  python:3.12-slim \
  node:20-alpine \
  -o docker-base-images.tar
# Pesa ~1 GB.
```

**Paso B — sube `docker-base-images.tar` al VPS al lado del resto del bundle**:

```bash
scp docker-base-images.tar root@vps:/tmp/bohub-offline/
```

**Paso C — en el VPS, lanza el bootstrap air-gapped**:

```bash
sudo bash /tmp/bohub-offline/bootstrap-server-offline.sh \
  --domain crm.tudominio.com \
  --air-gapped
```

El script detecta `docker-base-images.tar` en el bundle dir, hace
`docker load`, y construye sin tocar la red.

> Nota: en air-gapped Docker + Docker Compose deben estar ya instalados
> en el VPS. El script no los descarga.

## Requisitos del VPS

- Ubuntu 22.04+ / Debian 12.
- 4 GB RAM (8 GB recomendado).
- 40 GB disco.
- root.
- DNS apuntando al servidor (para el cert TLS).

Si vas a hacer instalación air-gapped, instala Docker antes con tu fuente
favorita (paquete .deb local, mirror interno…).

## Cómo usarlo paso a paso

### 1. Descomprimir en el VPS

```bash
mkdir -p /tmp/bohub-offline
cd /tmp/bohub-offline
unzip /ruta/a/bohub-offline-install.zip
chmod +x bootstrap-server-offline.sh
```

### 2. Lanzar el bootstrap

Con internet outbound normal:

```bash
sudo bash bootstrap-server-offline.sh --domain crm.tudominio.com
```

Air-gapped (tras haber copiado `docker-base-images.tar` al bundle):

```bash
sudo bash bootstrap-server-offline.sh --domain crm.tudominio.com --air-gapped
```

### 3. Te pedirá interactivamente

- Dominio público del CRM.
- Email + password del admin inicial.
- Password MySQL del user `crm` y del root.
- SMTP host/user/password (para reset de contraseñas).
- Anthropic API key (opcional para la feature de IA).

Autogenera con `python3 -c`:
- `SECRET_KEY` JWT (48 chars urlsafe).
- `INTEGRATION_SECRETS_KEY` Fernet (44 chars base64).

### 4. El script hace

1. Extrae el repo en `/opt/crmbo/`.
2. Copia los wheels en `/opt/crmbo/offline-wheels/` (para
   reconstrucciones futuras sin internet).
3. Extrae `node_modules` en `/opt/crmbo/frontend/node_modules/`.
4. Genera `.env.production` con permisos 600.
5. Build de las imágenes Docker. Si `--air-gapped`, sin `--pull`.
6. Up del stack, espera a MySQL healthy.
7. `alembic upgrade head` + crea admin inicial.
8. Si NO air-gapped: pide cert Let's Encrypt con `certbot --standalone`.

### 5. Login

```
https://crm.tudominio.com
admin@tudominio.com / la-pass-que-pusiste
```

## Flags del bootstrap

| Flag | Descripción |
|---|---|
| `--domain crm.tudominio.com` | Dominio público (obligatorio para TLS) |
| `--bundle-dir /tmp/bohub-offline` | Dónde está el bundle (default: cwd del script) |
| `--install-dir /opt/crmbo` | Dónde instalar (default: `/opt/crmbo`) |
| `--air-gapped` | Modo sin internet. Requiere `docker-base-images.tar`. Skip TLS. |
| `--with-plesk` | Override Plesk (nginx en `127.0.0.1:8080`). Skip TLS. |
| `--no-tls` | No pide cert (manual luego) |

## Estructura post-instalación

```
/opt/crmbo/                       # repo extraído
├── .env.production               # secretos (600)
├── offline-wheels/               # wheels Python (~40 MB) preservados
├── docker-compose.prod.yml
├── backend/
├── frontend/                     # con node_modules dentro
└── ...

/var/backups/crmbo/               # backups cifrados (750)
```

## Reconstruir imágenes sin internet tras la instalación

Tras la primera instalación, las imágenes Docker quedan en el caché local.
Para rebuilds offline (ej. tras un git pull manual sin acceso a registries):

```bash
cd /opt/crmbo

# Frontend: si modificaste código, el build USA el node_modules existente
# (Dockerfile capa `deps` se invalida solo si cambia package*.json).
docker compose --env-file .env.production -f docker-compose.prod.yml \
  build frontend

# Backend: el pip install dentro del Dockerfile sigue requiriendo PyPI
# por defecto. Para offline, edita backend/Dockerfile añadiendo el
# --find-links sobre el bind-mount de offline-wheels/:
#
#   RUN pip install --no-index --find-links /wheels --no-cache-dir \
#     -r requirements.txt
#
# Y monta /opt/crmbo/offline-wheels en /wheels via volume.
```

## Operaciones diarias (igual que la instalación con internet)

```bash
cd /opt/crmbo

# Estado
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# Logs
docker compose --env-file .env.production -f docker-compose.prod.yml \
  logs -f api worker-sync worker-workflows

# Restart tras pull del repo
git pull
docker compose --env-file .env.production -f docker-compose.prod.yml \
  up -d --build --force-recreate api worker-sync worker-workflows frontend
docker compose --env-file .env.production -f docker-compose.prod.yml \
  exec api alembic upgrade head
```

## Troubleshooting

- **`docker compose build` falla por timeout en pip install** — estás en
  un VPS sin acceso a PyPI. Necesitas editar `backend/Dockerfile` para
  usar `--find-links /wheels` sobre el bind-mount de `offline-wheels/`.

- **MySQL no arranca tras restart** — borra el volumen huérfano:
  `docker volume rm crmbo_mysql_data` y vuelve a levantar.

- **node_modules dentro del container está vacío** — el Dockerfile del
  frontend `COPY . .` puede excluir node_modules vía `.dockerignore`.
  Verifica que `frontend/.dockerignore` NO lista `node_modules` (o ajusta).

- **Certbot falla con DNS pendiente** — DNS aún no propagado o firewall
  bloqueando :80. Espera o lanza `certbot certonly --webroot` luego.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Sprint Backup-Hardening. `default-mysql-client` aporta `mysqldump`
# (lo invoca `scripts/backup-crmbo.sh` desde el worker) y `gnupg`
# aporta `gpg` para el cifrado simétrico AES-256. Sin ambos paquetes
# en la imagen, los backups manuales / cron desde el worker fallaban
# silenciosamente — el hack post-deploy había sido `apt-get install`
# a mano dentro del container, perdido al primer `--force-recreate`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        default-mysql-client \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# One-off operator scripts (backfills, cleanups). Without this COPY,
# `docker compose exec api python scripts/...` fails with
# "No such file or directory" — the runbooks in docs/ depend on it.
COPY scripts ./scripts
COPY entrypoint.sh ./entrypoint.sh

EXPOSE 8000
CMD ["./entrypoint.sh"]

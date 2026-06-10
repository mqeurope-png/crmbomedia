FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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

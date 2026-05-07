#!/usr/bin/env sh
set -eu

alembic upgrade head
python -m app.db.init_db
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

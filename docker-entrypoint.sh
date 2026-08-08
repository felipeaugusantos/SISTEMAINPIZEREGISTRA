#!/bin/sh
set -e

# Aplica as migrações pendentes antes de subir a API.
DATABASE_URL="${MIGRATION_DATABASE_URL:-$DATABASE_URL}" /app/.venv/bin/alembic upgrade head
/app/.venv/bin/python -m app.bootstrap_admin

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

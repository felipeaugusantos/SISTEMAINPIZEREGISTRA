#!/bin/sh
set -e

# Aplica as migrações pendentes antes de iniciar os serviços.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    DATABASE_URL="${MIGRATION_DATABASE_URL:-$DATABASE_URL}" /app/.venv/bin/alembic upgrade head
fi

if [ "${BOOTSTRAP_ADMIN:-true}" = "true" ]; then
    /app/.venv/bin/python -m app.bootstrap_admin
fi

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="${UVICORN_FORWARDED_ALLOW_IPS:-127.0.0.1}"

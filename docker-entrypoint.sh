#!/bin/sh
set -e

# Aplica as migrações pendentes antes de subir a API.
/app/.venv/bin/alembic upgrade head

exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$APP_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE inpi_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inpi_app')\gexec
GRANT CONNECT ON DATABASE inpi TO inpi_app;
GRANT USAGE ON SCHEMA public TO inpi_app;
SQL

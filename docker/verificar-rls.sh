#!/bin/sh
set -eu

# Verifica no banco de producao (somente leitura) se TODA tabela com coluna
# organizacao_id tem RLS ativo e forcado, e se a role da aplicacao nao
# ignora RLS (auditoria de seguranca de 13/10/2026, item 8).
# Saida 0 = tudo certo; 1 = ha tabelas sem RLS ou role insegura.
#
# Uso (na VPS):  ./docker/verificar-rls.sh

cd "$(dirname "$0")/.."

SQL_TABELAS="
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'organizacao_id' AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
ORDER BY 1;"
SQL_ROLE="SELECT rolname FROM pg_roles WHERE rolname = 'inpi_app' AND (rolsuper OR rolbypassrls);"

SEM_RLS="$(docker compose exec -T db psql -U inpi -d inpi -At -c "$SQL_TABELAS")"
ROLE_RUIM="$(docker compose exec -T db psql -U inpi -d inpi -At -c "$SQL_ROLE")"
STATUS=0
if [ -n "$SEM_RLS" ]; then
    echo "TABELAS COM organizacao_id SEM RLS ativo+forcado:" >&2
    echo "$SEM_RLS" >&2
    STATUS=1
fi
if [ -n "$ROLE_RUIM" ]; then
    echo "A role inpi_app e superuser ou tem BYPASSRLS -- RLS inutil para a aplicacao." >&2
    STATUS=1
fi
[ "$STATUS" -eq 0 ] && echo "OK: todas as tabelas com organizacao_id tem RLS ativo e forcado; inpi_app nao ignora RLS."
exit "$STATUS"

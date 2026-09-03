#!/bin/sh
set -eu

# Backup do banco de producao (pg_dump formato custom), com validacao do
# arquivo gerado e retencao configuravel. Espelha scripts/backup-banco.ps1
# (uso local/Windows), mas roda direto na VPS via docker compose.
#
# Uso:
#   ./docker/backup-banco.sh              # retencao padrao (14 dias)
#   RETENCAO_DIAS=30 ./docker/backup-banco.sh

RETENCAO_DIAS="${RETENCAO_DIAS:-14}"

cd "$(dirname "$0")/.."
mkdir -p backups

DATA="$(date +%Y%m%d-%H%M%S)"
NOME="inpi-${DATA}.dump"
TEMP="/tmp/${NOME}"

CONTAINER="$(docker compose ps -q db)"
if [ -z "$CONTAINER" ]; then
    echo "Container do PostgreSQL nao esta em execucao." >&2
    exit 1
fi

docker compose exec -T db pg_dump -U inpi -d inpi -Fc -f "$TEMP"
# Falha cedo se o dump estiver corrompido -- pg_restore -l so le o cabecalho,
# nao aplica nada.
docker compose exec -T db pg_restore -l "$TEMP" >/dev/null
docker cp "${CONTAINER}:${TEMP}" "backups/${NOME}"
docker compose exec -T db rm -f "$TEMP"

TAMANHO="$(stat -c%s "backups/${NOME}" 2>/dev/null || stat -f%z "backups/${NOME}")"
if [ "$TAMANHO" -le 0 ]; then
    echo "O arquivo de backup foi criado vazio." >&2
    exit 1
fi

find backups -name 'inpi-*.dump' -type f -mtime "+${RETENCAO_DIAS}" -delete

echo "backups/${NOME} (${TAMANHO} bytes)"

#!/bin/sh
set -eu

# Backup do banco de producao (pg_dump formato custom), com validacao do
# arquivo gerado e retencao configuravel. Espelha scripts/backup-banco.ps1
# (uso local/Windows), mas roda direto na VPS via docker compose.
#
# Uso:
#   ./docker/backup-banco.sh              # retencao padrao (7 dias), no maximo 1x/dia
#   RETENCAO_DIAS=30 ./docker/backup-banco.sh
#   FORCAR=1 ./docker/backup-banco.sh     # ignora o limite de 1x/dia

# Achado 04/09/2026: com o banco em ~30GB (apos a migration wp43q1s5d064
# adicionar indices faltantes), cada dump comprimido passou de ~1.6GB para
# ~5.5GB. Com 14 dias de retencao isso sozinho consumiria ~77GB no disco da
# VPS (193GB total) -- reduzido para 7 dias para caber com folga.
RETENCAO_DIAS="${RETENCAO_DIAS:-7}"

cd "$(dirname "$0")/.."
mkdir -p backups

# Achado 05/09/2026: mesmo so rodando quando ha migration pendente
# (docker/deploy.sh), varios deploys com migration no mesmo dia ainda
# geravam varios dumps de ~5.5GB. O usuario pediu explicitamente no
# maximo 1 backup por dia -- se ja existe um dump de hoje, pula.
if [ "${FORCAR:-}" != "1" ] && ls "backups/inpi-$(date +%Y%m%d)-"*.dump >/dev/null 2>&1; then
    echo "Ja existe um backup de hoje em backups/ -- pulando (use FORCAR=1 para forcar)."
    exit 0
fi

DATA="$(date +%Y%m%d-%H%M%S)"
NOME="inpi-${DATA}.dump"
TEMP="/tmp/${NOME}"

CONTAINER="$(docker compose ps -q db)"
if [ -z "$CONTAINER" ]; then
    echo "Container do PostgreSQL nao esta em execucao." >&2
    exit 1
fi

# Achado 10 da auditoria (07/10/2026): o docker cp escrevia direto no nome
# definitivo (backups/inpi-DATA.dump). Um cp interrompido deixava um arquivo
# PARCIAL com o nome final, que (a) nao era validado e (b) fazia a verificacao
# de "ja existe backup de hoje" pular o backup. Agora grava num .part, confere
# o tamanho contra o original e so entao renomeia (atomico) para o nome final.
rm -f backups/*.part 2>/dev/null || true
PARCIAL="backups/${NOME}.part"

docker compose exec -T db pg_dump -U inpi -d inpi -Fc -f "$TEMP"
# Falha cedo se o dump estiver corrompido -- pg_restore -l so le o cabecalho,
# nao aplica nada.
docker compose exec -T db pg_restore -l "$TEMP" >/dev/null
TAMANHO_ORIG="$(docker compose exec -T db stat -c%s "$TEMP" | tr -d '\r')"
docker cp "${CONTAINER}:${TEMP}" "$PARCIAL"
docker compose exec -T db rm -f "$TEMP"

TAMANHO="$(stat -c%s "$PARCIAL" 2>/dev/null || stat -f%z "$PARCIAL")"
if [ -z "$TAMANHO" ] || [ "$TAMANHO" -le 0 ] || [ "$TAMANHO" != "$TAMANHO_ORIG" ]; then
    echo "Copia do backup incompleta ($TAMANHO de $TAMANHO_ORIG bytes) -- descartando." >&2
    rm -f "$PARCIAL"
    exit 1
fi

# Renomeia atomicamente: o nome definitivo so aparece apos a copia integra.
mv "$PARCIAL" "backups/${NOME}"

find backups -name 'inpi-*.dump' -type f -mtime "+${RETENCAO_DIAS}" -delete

echo "backups/${NOME} (${TAMANHO} bytes)"

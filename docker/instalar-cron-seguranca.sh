#!/bin/sh
set -eu

# Instala no crontab do usuario atual da VPS (idempotente) a rotina diaria
# encadeada das 02:00 (hora local da VPS = Brasilia): backup, envio externo
# cifrado e, aos domingos, simulado de restauracao (docker/rotina-diaria-backup.sh).
# Remove as linhas antigas de backup-banco.sh e simulado-restauracao.sh e as
# linhas com a marca "# zeregistra-seguranca" para nao duplicar execucoes.

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MARCA="# zeregistra-seguranca"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

crontab -l 2>/dev/null | grep -vE "backup-banco\.sh|simulado-restauracao\.sh|rotina-diaria-backup\.sh|$MARCA" > "$TMP" || true
echo "0 2 * * * cd $DIR && sh ./docker/rotina-diaria-backup.sh >> /var/log/zeregistra-backup.log 2>&1 $MARCA" >> "$TMP"
crontab "$TMP"
echo "Crontab atualizado:"
crontab -l | grep -E "backup|simulado|seguranca|shutdown"

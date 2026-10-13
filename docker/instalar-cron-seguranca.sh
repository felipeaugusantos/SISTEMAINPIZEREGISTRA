#!/bin/sh
set -eu

# Instala no crontab do usuario atual da VPS (idempotente):
#   - rotina diaria encadeada das 02:00 (hora local da VPS = Brasilia): backup,
#     envio externo cifrado e, aos domingos, simulado de restauracao
#     (docker/rotina-diaria-backup.sh);
#   - a cada 5 minutos, docker/manutencao-limpeza.sh: atende pedidos de limpeza de
#     disco feitos no painel do superadmin e publica o relatorio de espaco.
# Remove as linhas antigas de backup-banco.sh e simulado-restauracao.sh e as
# linhas com as marcas "# zeregistra-seguranca" e "# zeregistra-manutencao"
# para nao duplicar execucoes.

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MARCA="# zeregistra-seguranca"
MARCA_MANUTENCAO="# zeregistra-manutencao"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

crontab -l 2>/dev/null | grep -vE "backup-banco\.sh|simulado-restauracao\.sh|rotina-diaria-backup\.sh|manutencao-limpeza\.sh|$MARCA|$MARCA_MANUTENCAO" > "$TMP" || true
echo "0 2 * * * cd $DIR && sh ./docker/rotina-diaria-backup.sh >> /var/log/zeregistra-backup.log 2>&1 $MARCA" >> "$TMP"
echo "*/5 * * * * cd $DIR && sh ./docker/manutencao-limpeza.sh >> /var/log/zeregistra-manutencao.log 2>&1 $MARCA_MANUTENCAO" >> "$TMP"
crontab "$TMP"
echo "Crontab atualizado:"
crontab -l | grep -E "backup|simulado|seguranca|manutencao|shutdown"

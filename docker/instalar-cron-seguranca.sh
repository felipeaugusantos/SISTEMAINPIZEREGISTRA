#!/bin/sh
set -eu

# Instala (idempotente) no crontab do usuario atual da VPS:
#   - backup diario do banco                       (05:00 UTC = 02:00 Brasilia)
#   - envio externo cifrado, logo depois            (06:30 UTC)
#   - simulado de restauracao SEMANAL (domingo)     (08:00 UTC)
# Auditoria de seguranca de 13/10/2026, item 19. Linhas marcadas com
# "# zeregistra-seguranca" sao substituidas a cada execucao.
#
# O envio externo so entra se /opt/zeregistra/.backup-offsite.env existir
# (define BACKUP_OFFSITE_REMOTE e BACKUP_PASSPHRASE_FILE; chmod 600).

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MARCA="# zeregistra-seguranca"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

crontab -l 2>/dev/null | grep -v "$MARCA" > "$TMP" || true
{
    echo "0 5 * * * cd $DIR && ./docker/backup-banco.sh >> /var/log/zeregistra-backup.log 2>&1 $MARCA"
    if [ -f "$DIR/.backup-offsite.env" ]; then
        echo "30 6 * * * cd $DIR && set -a && . ./.backup-offsite.env && set +a && ./docker/backup-offsite.sh >> /var/log/zeregistra-backup.log 2>&1 $MARCA"
    else
        echo "Aviso: $DIR/.backup-offsite.env ausente -- envio externo NAO agendado." >&2
    fi
    echo "0 8 * * 0 cd $DIR && ./docker/simulado-restauracao.sh >> /var/log/zeregistra-simulado.log 2>&1 $MARCA"
} >> "$TMP"
crontab "$TMP"
echo "Crontab atualizado:"
crontab -l | grep "$MARCA"

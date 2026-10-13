#!/bin/sh
set -eu

# Copia externa e CIFRADA do backup mais recente do banco (auditoria de
# seguranca de 13/10/2026, item 19/17): os dumps de backups/ ficavam em texto
# claro so na VPS (e num PC pessoal). Aqui o dump mais recente e cifrado com
# AES-256 (openssl, PBKDF2) e enviado por rclone para um destino FORA da VPS
# (S3, Backblaze B2, Google Drive etc. -- qualquer remote do rclone).
#
# Configuracao (no .env do VPS ou exportada pelo cron):
#   BACKUP_OFFSITE_REMOTE   remote:caminho do rclone, ex.: b2:zeregistra-backups/db
#   BACKUP_PASSPHRASE_FILE  arquivo (chmod 600) com a senha de cifragem.
#                           GUARDE UMA COPIA DESSA SENHA FORA DA VPS: sem ela
#                           o backup cifrado nao pode ser restaurado.
#   RETENCAO_OFFSITE_DIAS   padrao 30
#
# Restauracao: ver docs/seguranca-semana1-runbook.md.

cd "$(dirname "$0")/.."

: "${BACKUP_OFFSITE_REMOTE:?defina BACKUP_OFFSITE_REMOTE (remote:caminho do rclone)}"
: "${BACKUP_PASSPHRASE_FILE:?defina BACKUP_PASSPHRASE_FILE (arquivo com a senha de cifragem)}"
RETENCAO_OFFSITE_DIAS="${RETENCAO_OFFSITE_DIAS:-30}"

command -v rclone >/dev/null || { echo "rclone nao instalado (apt install rclone)." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl nao instalado." >&2; exit 1; }
[ -s "$BACKUP_PASSPHRASE_FILE" ] || { echo "Arquivo de senha ausente/vazio: $BACKUP_PASSPHRASE_FILE" >&2; exit 1; }

DUMP="$(ls -t backups/inpi-*.dump 2>/dev/null | head -n1 || true)"
[ -n "$DUMP" ] || { echo "Nenhum dump em backups/." >&2; exit 1; }

# Nao reenvia o mesmo dump duas vezes no mesmo dia.
MARCA="backups/.offsite-$(basename "$DUMP")"
if [ -f "$MARCA" ] && [ "${FORCAR:-}" != "1" ]; then
    echo "$(basename "$DUMP") ja foi enviado -- pulando."
    exit 0
fi

CIFRADO="backups/$(basename "$DUMP").enc.part"
trap 'rm -f "$CIFRADO"' EXIT
openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -in "$DUMP" -out "$CIFRADO" -pass "file:$BACKUP_PASSPHRASE_FILE"

# Confere que a cifra decifra de volta (le so o inicio) antes de enviar.
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -in "$CIFRADO" -pass "file:$BACKUP_PASSPHRASE_FILE" | head -c 5 | grep -q "PGDMP" \
    || { echo "Falha na verificacao da cifra -- nao enviado." >&2; exit 1; }

DESTINO="${BACKUP_OFFSITE_REMOTE%/}/$(basename "$DUMP").enc"
rclone copyto "$CIFRADO" "$DESTINO" --checksum

touch "$MARCA"
rclone delete "${BACKUP_OFFSITE_REMOTE%/}/" --min-age "${RETENCAO_OFFSITE_DIAS}d" --include "inpi-*.dump.enc"
find backups -name '.offsite-inpi-*' -type f -mtime "+${RETENCAO_OFFSITE_DIAS}" -delete
echo "Enviado: $DESTINO"

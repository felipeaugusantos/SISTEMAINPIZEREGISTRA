#!/bin/sh
set -u

# Rotina diaria encadeada, chamada pelo cron da VPS (fuso local = Brasilia):
#   1. backup do banco (backup-banco.sh)
#   2. envio externo cifrado, se /opt/zeregistra/.backup-offsite.env existir
#   3. aos domingos, simulado de restauracao
# Encadear garante a ordem: a copia externa e o simulado usam o dump que acabou
# de ser gerado. Rotinas que baixam e APAGAM o dump da VPS (ex.: tarefa no PC)
# devem rodar DEPOIS do marcador backups/.offsite-<dump> existir (ver
# docs/seguranca-semana1-runbook.md).

cd "$(dirname "$0")/.."
STATUS=0

sh ./docker/backup-banco.sh || { echo "ERRO: backup-banco.sh falhou." >&2; exit 1; }

if [ -f .backup-offsite.env ]; then
    (set -a && . ./.backup-offsite.env && set +a && sh ./docker/backup-offsite.sh) \
        || { echo "ERRO: envio externo falhou." >&2; STATUS=1; }
else
    echo "Aviso: .backup-offsite.env ausente -- copia externa nao enviada." >&2
fi

if [ "$(date +%u)" = "7" ]; then
    mkdir -p logs
    sh ./docker/simulado-restauracao.sh >> logs/simulado-restauracao.log 2>&1 \
        || { echo "ERRO: simulado de restauracao falhou." >&2; STATUS=1; }
fi
exit "$STATUS"

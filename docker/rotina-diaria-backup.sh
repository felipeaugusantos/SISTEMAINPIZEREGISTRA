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
#
# Cada etapa grava uma linha JSON em backups/historico-rotina.jsonl, que o
# painel de saude dos backups (superadmin) le via worker -- ver
# app/saude_backups.py.

cd "$(dirname "$0")/.."
STATUS=0
HIST="backups/historico-rotina.jsonl"

registrar() {
    # $1 etapa  $2 ok(true|false)  $3 arquivo  $4 tamanho_bytes  $5 duracao_s
    mkdir -p backups
    printf '{"etapa":"%s","ok":%s,"em":"%s","arquivo":"%s","tamanho_bytes":%s,"duracao_s":%s}\n' \
        "$1" "$2" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$3" "$4" "$5" >> "$HIST"
    # Mantem so as ultimas 300 linhas (troca atomica).
    tail -n 300 "$HIST" > "$HIST.tmp" && mv "$HIST.tmp" "$HIST"
}

dump_mais_recente() {
    ls -t backups/inpi-*.dump 2>/dev/null | head -n1 || true
}

tamanho_de() {
    if [ -n "$1" ] && [ -f "$1" ]; then stat -c%s "$1"; else echo 0; fi
}

INICIO=$(date +%s)
if sh ./docker/backup-banco.sh; then
    DUMP="$(dump_mais_recente)"
    registrar dump true "$(basename "${DUMP:-}")" "$(tamanho_de "${DUMP:-}")" "$(( $(date +%s) - INICIO ))"
else
    registrar dump false "" 0 "$(( $(date +%s) - INICIO ))"
    echo "ERRO: backup-banco.sh falhou." >&2
    exit 1
fi

if [ -f .backup-offsite.env ]; then
    INICIO=$(date +%s)
    if (set -a && . ./.backup-offsite.env && set +a && sh ./docker/backup-offsite.sh); then
        registrar offsite true "$(basename "${DUMP:-}")" "$(tamanho_de "${DUMP:-}")" "$(( $(date +%s) - INICIO ))"
    else
        registrar offsite false "$(basename "${DUMP:-}")" 0 "$(( $(date +%s) - INICIO ))"
        echo "ERRO: envio externo falhou." >&2
        STATUS=1
    fi
else
    echo "Aviso: .backup-offsite.env ausente -- copia externa nao enviada." >&2
fi

if [ "$(date +%u)" = "7" ]; then
    mkdir -p logs
    INICIO=$(date +%s)
    if sh ./docker/simulado-restauracao.sh >> logs/simulado-restauracao.log 2>&1; then
        registrar simulado true "$(basename "${DUMP:-}")" 0 "$(( $(date +%s) - INICIO ))"
    else
        registrar simulado false "$(basename "${DUMP:-}")" 0 "$(( $(date +%s) - INICIO ))"
        echo "ERRO: simulado de restauracao falhou." >&2
        STATUS=1
    fi
fi
find backups -name ".pc-inpi-*" -type f -mtime +30 -delete 2>/dev/null || true
exit "$STATUS"

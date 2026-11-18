#!/bin/sh
set -u

# Sonda de disponibilidade (painel de TV, item 3 - 17/11/2026): a cada minuto,
# verifica se os dois sistemas respondem e grava uma linha por alvo em
# backups/disponibilidade.jsonl. Uma vez por dia (cron 00:03, fuso local =
# Brasilia) fecha o dia anterior em backups/disponibilidade-diario.jsonl e
# poda o raw para os ultimos 14 dias. O worker le esses arquivos (backups/ e
# montado somente leitura) e o painel mostra a disponibilidade do mes e os
# incidentes -- ver app/disponibilidade.py.
#
# Cron do host (fuso local = Brasilia):
#   * * * * *  /opt/zeregistra/docker/sonda-disponibilidade.sh
#   3 0 * * *  /opt/zeregistra/docker/sonda-disponibilidade.sh --fechar-dia
#
# No ar = HTTP 200 no /health do Zé Registra (testa app+banco+fila) e qualquer
# resposta HTTP abaixo de 500 no endereco publico do EZRA ERP. Timeout de 5 s,
# sem retry: cada minuto e um dado, e minuto sem linha conta como fora.

cd "$(dirname "$0")/.."
RAW="backups/disponibilidade.jsonl"
DIARIO="backups/disponibilidade-diario.jsonl"
DIAS_RAW=14
URL_ZR="${SONDA_ZR_URL:-https://app.zeregistra.com.br/health}"
URL_ERP="${SONDA_ERP_URL:-https://erpzetech.lovable.app/}"

sonda() {
    # $1 alvo  $2 url  $3 modo (200 = so 200 vale; abaixo500 = qualquer 1xx-4xx vale)
    inicio=$(date +%s%N)
    codigo=$(curl -sS -o /dev/null -m 5 -w '%{http_code}' "$2" 2>/dev/null) || codigo=0
    fim=$(date +%s%N)
    ms=$(( (fim - inicio) / 1000000 ))
    [ "$ms" -ge 0 ] 2>/dev/null || ms=0
    ok=false
    if [ "$3" = "200" ]; then
        [ "$codigo" = "200" ] && ok=true
    else
        case "$codigo" in
            1??|2??|3??|4??) ok=true ;;
        esac
    fi
    mkdir -p backups
    printf '{"alvo":"%s","ok":%s,"em":"%s","ms":%s,"http":%s}\n' \
        "$1" "$ok" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ms" "$codigo" >> "$RAW"
}

fechar_dia() {
    # Fecha o dia de ontem (fuso de Brasilia) por alvo e poda o raw.
    dia=$(TZ=America/Sao_Paulo date -d 'yesterday' +%F)
    ini=$(date -u -d "$dia 03:00" +'%Y-%m-%dT%H:%M')
    fim=$(date -u -d "$dia 03:00 + 1 day" +'%Y-%m-%dT%H:%M')
    [ -f "$RAW" ] || return 0
    for alvo in zeregistra erp; do
        up=$(awk -v alvo="\"$alvo\"" -v ini="$ini" -v fim="$fim" '
            index($0, "\"alvo\":" alvo) && index($0, "\"ok\":true") {
                em = substr($0, index($0, "\"em\":\"") + 6, 16)
                if (em >= ini && em < fim && !visto[em]++) up++
            }
            END { print up + 0 }
        ' "$RAW")
        # Idempotente: refaz a linha do mesmo dia se rodar de novo.
        grep -v "\"alvo\":\"$alvo\",\"dia\":\"$dia\"" "$DIARIO" 2>/dev/null > "$DIARIO.tmp" || true
        printf '{"alvo":"%s","dia":"%s","minutos_up":%s}\n' "$alvo" "$dia" "$up" >> "$DIARIO.tmp"
        mv "$DIARIO.tmp" "$DIARIO"
    done
    # Poda atômica: só os últimos DIAS_RAW dias ficam no raw.
    corte=$(date -u -d "$DIAS_RAW days ago" +'%Y-%m-%dT%H:%M')
    awk -v corte="$corte" '
        {
            em = substr($0, index($0, "\"em\":\"") + 6, 16)
            if (em >= corte) print
        }
    ' "$RAW" > "$RAW.tmp" && mv "$RAW.tmp" "$RAW"
}

if [ "${1:-}" = "--fechar-dia" ]; then
    fechar_dia
else
    sonda zeregistra "$URL_ZR" 200
    sonda erp "$URL_ERP" abaixo500
fi

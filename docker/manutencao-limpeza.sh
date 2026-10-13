#!/bin/sh
set -u

# Limpeza de disco da VPS pedida pelo painel do superadmin (Observabilidade).
#
# Roda no CRON DO HOST a cada 5 minutos (ver docker/instalar-cron-seguranca.sh) e
# faz duas coisas:
#   1. se existir um pedido na fila Redis (manutencao:limpeza:fila), executa a
#      lista FIXA de limpezas abaixo e publica o resultado;
#   2. publica (no maximo de 2 em 2 horas) o relatorio de quanto cada categoria
#      ocupa, que o painel mostra antes de o superadmin pedir a limpeza.
# O pedido NAO carrega comando: o conteudo e ignorado, so a existencia importa.
# A API nunca recebe acesso ao Docker (socket do Docker = root na VPS).
#
# LISTA FIXA -- o que este script faz:
#   - remove imagens Docker orfas (sem tag, "dangling");
#   - remove cache de build com mais de 7 dias;
#   - remove containers PARADOS que nao sao do projeto compose;
#   - remove residuos inpi-*.dump com mais de 1 dia do /tmp do container do banco;
#   - remove arquivos temporarios antigos do /tmp da VPS e backups/*.part;
#   - corta logs /var/log/zeregistra-*.log acima de 50 MB (mantem as ultimas linhas).
# O que NUNCA faz (nao adicionar): apagar volumes (postgres/redis/clamav/ollama),
# backups/ (alem de .part), data/ e uploads, `prune -a`, `volume prune`,
# `system prune`, `down`. (Incidente de 16/09/2026: um `down -v` apagou a producao.)
#
# Uso:  sh docker/manutencao-limpeza.sh              (modo cron)
#       sh docker/manutencao-limpeza.sh --relatorio  (so publica o relatorio agora)

cd "$(dirname "$0")/.."

PROJETO="${PROJETO_COMPOSE:-$(basename "$PWD")}"
FILA="manutencao:limpeza:fila"
RELATORIO="manutencao:limpeza:relatorio"
RESULTADO="manutencao:limpeza:resultado"
ANDAMENTO="manutencao:limpeza:andamento"
LOCK="/tmp/zeregistra-manutencao.lock"

# Trava contra duas execucoes ao mesmo tempo; trava velha (>2h) e descartada.
if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null || true
fi
mkdir "$LOCK" 2>/dev/null || exit 0
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

redis() { docker compose exec -T redis redis-cli "$@" 2>/dev/null | tr -d '\r'; }
agora() { date -u +%Y-%m-%dT%H:%M:%SZ; }
limpa() { tr -d '"\\' ; }   # defesa: nenhum texto vindo de comando quebra o JSON
usado_bytes() { df -B1 --output=used / | tail -n1 | tr -d ' '; }
backup_em_andamento() { pgrep -f "backup-banco|rotina-diaria-backup|pg_dump" >/dev/null 2>&1; }
container_db() { docker compose ps -q db 2>/dev/null | head -n1; }

soma_bytes() { awk '{s+=$1} END{print s+0}'; }

# --- medicao (cada funcao imprime: bytes quantidade) -------------------------
medir_imagens_orfas() {
    ids="$(docker images -f dangling=true -q 2>/dev/null | sort -u)"
    [ -z "$ids" ] && { echo "0 0"; return; }
    n="$(echo "$ids" | wc -l | tr -d ' ')"
    b="$(echo "$ids" | xargs docker image inspect -f '{{.Size}}' 2>/dev/null | soma_bytes)"
    echo "$b $n"
}
texto_cache_build() {
    docker system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null | grep '^Build Cache' | cut -d'|' -f2 | limpa
}
ids_containers_alheios() {
    docker ps -a -f status=exited --format '{{.ID}} {{.Label "com.docker.compose.project"}}' 2>/dev/null \
        | awk -v p="$PROJETO" '$2 != p {print $1}'
}
medir_containers_alheios() {
    ids="$(ids_containers_alheios)"
    [ -z "$ids" ] && { echo "0 0"; return; }
    echo "null $(echo "$ids" | wc -l | tr -d ' ')"
}
medir_residuo_dump_db() {
    c="$(container_db)"
    [ -z "$c" ] && { echo "0 0"; return; }
    docker exec "$c" find /tmp -maxdepth 1 -type f -name 'inpi-*.dump' -mmin +1440 -printf '%s\n' 2>/dev/null \
        | awk '{s+=$1; n++} END{print s+0, n+0}'
}
medir_tmp_vps() {
    { find /tmp -maxdepth 1 -type f \( -name '*.dump' -o -name '*.dump.enc' -o -name 'zeregistra-*' -o -name 'simulado-*' \) -mtime +7 -printf '%s\n' 2>/dev/null
      find backups -maxdepth 1 -type f -name '*.part' -mmin +1440 -printf '%s\n' 2>/dev/null; } \
        | awk '{s+=$1; n++} END{print s+0, n+0}'
}
medir_logs() {
    find /var/log -maxdepth 1 -type f -name 'zeregistra-*.log' -size +50M -printf '%s\n' 2>/dev/null \
        | awk '{s+=$1; n++} END{print s+0, n+0}'
}

item_relatorio() {
    # $1 chave  $2 nome  $3 "bytes quantidade"  $4 detalhe
    set -- "$1" "$2" "$3" "$4"
    bytes="$(echo "$3" | cut -d' ' -f1)"
    qtd="$(echo "$3" | cut -d' ' -f2)"
    printf '{"chave":"%s","nome":"%s","bytes":%s,"quantidade":%s,"detalhe":"%s"}' "$1" "$2" "$bytes" "$qtd" "$4"
}

publicar_relatorio() {
    cache="$(texto_cache_build)"
    read -r tam usado livre <<EOF
$(df -B1 --output=size,used,avail / | tail -n1)
EOF
    json="{\"gerado_em\":\"$(agora)\",\"disco\":{\"total_bytes\":${tam:-0},\"usado_bytes\":${usado:-0},\"livre_bytes\":${livre:-0}},\"itens\":["
    json="${json}$(item_relatorio imagens_orfas "Imagens Docker órfãs" "$(medir_imagens_orfas)" "Sem tag; nenhum container as usa."),"
    json="${json}$(item_relatorio cache_build "Cache de build (mais de 7 dias)" "null 0" "Recuperável segundo o Docker: ${cache:-n/d}."),"
    json="${json}$(item_relatorio containers_alheios "Containers parados fora do projeto" "$(medir_containers_alheios)" "Containers do projeto nunca são removidos."),"
    json="${json}$(item_relatorio residuo_dump_db "Resíduos de dump no container do banco" "$(medir_residuo_dump_db)" "Arquivos inpi-*.dump com mais de 1 dia em /tmp."),"
    json="${json}$(item_relatorio tmp_vps "Temporários da VPS" "$(medir_tmp_vps)" "Dumps/arquivos de teste em /tmp (mais de 7 dias) e backups/*.part."),"
    json="${json}$(item_relatorio logs "Logs grandes (acima de 50 MB)" "$(medir_logs)" "Mantém as últimas 20 mil linhas.")"
    json="${json}]}"
    redis SET "$RELATORIO" "$json" EX 7200 >/dev/null
}

# --- execucao (lista fixa) ---------------------------------------------------
resultados=""
registrar() {
    # $1 chave  $2 ok(true|false)  $3 detalhe
    item="{\"chave\":\"$1\",\"ok\":$2,\"detalhe\":\"$(echo "$3" | limpa)\"}"
    if [ -z "$resultados" ]; then resultados="$item"; else resultados="$resultados,$item"; fi
}

executar_limpeza() {
    pedido_id="$1"
    inicio="$(agora)"
    antes="$(usado_bytes)"
    redis SET "$ANDAMENTO" 1 EX 3600 >/dev/null

    n="$(echo "$(medir_imagens_orfas)" | cut -d' ' -f2)"
    if docker image prune -f >/dev/null 2>&1; then registrar imagens_orfas true "${n} imagem(ns) órfã(s) analisada(s)"; else registrar imagens_orfas false "docker image prune falhou"; fi

    if docker builder prune -f --filter until=168h >/dev/null 2>&1; then registrar cache_build true "cache com mais de 7 dias removido"; else registrar cache_build false "docker builder prune falhou"; fi

    ids="$(ids_containers_alheios)"
    if [ -z "$ids" ]; then
        registrar containers_alheios true "nenhum container parado fora do projeto"
    elif echo "$ids" | xargs docker rm >/dev/null 2>&1; then
        registrar containers_alheios true "$(echo "$ids" | wc -l | tr -d ' ') container(s) removido(s)"
    else
        registrar containers_alheios false "falha ao remover containers parados"
    fi

    if backup_em_andamento; then
        registrar residuo_dump_db true "pulado: há backup em andamento"
        registrar tmp_vps true "pulado: há backup em andamento"
    else
        c="$(container_db)"
        if [ -n "$c" ] && docker exec "$c" find /tmp -maxdepth 1 -type f -name 'inpi-*.dump' -mmin +1440 -delete >/dev/null 2>&1; then
            registrar residuo_dump_db true "resíduos antigos removidos do /tmp do banco"
        else
            registrar residuo_dump_db false "falha ao limpar o /tmp do container do banco"
        fi
        find /tmp -maxdepth 1 -type f \( -name '*.dump' -o -name '*.dump.enc' -o -name 'zeregistra-*' -o -name 'simulado-*' \) -mtime +7 -delete 2>/dev/null
        find backups -maxdepth 1 -type f -name '*.part' -mmin +1440 -delete 2>/dev/null
        registrar tmp_vps true "temporários antigos removidos"
    fi

    for f in $(find /var/log -maxdepth 1 -type f -name 'zeregistra-*.log' -size +50M 2>/dev/null); do
        tail -n 20000 "$f" > "$f.tmp" && cat "$f.tmp" > "$f"
        rm -f "$f.tmp"
    done
    registrar logs true "logs grandes cortados nas últimas 20 mil linhas"

    depois="$(usado_bytes)"
    liberado=$(( antes - depois ))
    [ "$liberado" -lt 0 ] && liberado=0
    redis SET "$RESULTADO" "{\"id\":\"${pedido_id}\",\"iniciado_em\":\"${inicio}\",\"concluido_em\":\"$(agora)\",\"liberado_bytes\":${liberado},\"itens\":[${resultados}]}" EX 604800 >/dev/null
    redis DEL "$ANDAMENTO" >/dev/null
}

if [ "${1:-}" = "--relatorio" ]; then
    publicar_relatorio
    exit 0
fi

pedido="$(redis LPOP "$FILA")"
if [ -n "$pedido" ] && [ "$pedido" != "(nil)" ]; then
    # Descarta pedidos duplicados: uma limpeza atende a todos.
    while [ -n "$(redis LPOP "$FILA")" ]; do :; done
    id="$(echo "$pedido" | sed -n 's/.*"id": *"\([0-9a-f]*\)".*/\1/p')"
    executar_limpeza "${id:-desconhecido}"
    publicar_relatorio
elif [ "$(redis EXISTS "$RELATORIO")" != "1" ]; then
    publicar_relatorio
fi

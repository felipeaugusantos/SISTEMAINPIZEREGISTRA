#!/bin/sh
set -eu

# Restaura um dump (gerado por backup-banco.sh) num banco de destino. Por
# padrao restaura no db-test (efemero, perfil "test" do compose.yaml) -- para
# validar que o backup funciona sem qualquer risco ao banco de producao.
#
# Uso:
#   ./docker/restaurar-banco.sh backups/inpi-20260903-010000.dump
#   ./docker/restaurar-banco.sh backups/inpi-20260903-010000.dump --producao
#
# --producao restaura no banco real (db) -- exige essa flag explicita para
# nao ser possivel disparar por engano. NUNCA use isso sem ter certeza:
# sobrescreve os dados atuais.

DUMP="${1:-}"
ALVO_SERVICO="db-test"
PERFIL="test"

if [ -z "$DUMP" ]; then
    echo "Uso: $0 <arquivo.dump> [--producao]"
    exit 1
fi
if [ ! -f "$DUMP" ]; then
    echo "Arquivo nao encontrado: $DUMP" >&2
    exit 1
fi
if [ "${2:-}" = "--producao" ]; then
    ALVO_SERVICO="db"
    PERFIL=""
    echo "!! Restaurando no banco de PRODUCAO (db). Isso sobrescreve os dados atuais."
    printf "Digite 'sim' para confirmar: "
    read -r confirmacao
    [ "$confirmacao" = "sim" ] || { echo "Cancelado."; exit 1; }
fi

cd "$(dirname "$0")/.."

if [ -n "$PERFIL" ]; then
    docker compose --profile "$PERFIL" up -d "$ALVO_SERVICO"
    echo "==> aguardando $ALVO_SERVICO ficar saudavel"
    for _ in $(seq 1 30); do
        status="$(docker compose --profile "$PERFIL" ps "$ALVO_SERVICO" --format '{{.Health}}' 2>/dev/null || true)"
        [ "$status" = "healthy" ] && break
        sleep 1
    done
fi

NOME_ARQUIVO="$(basename "$DUMP")"
TEMP="/tmp/${NOME_ARQUIVO}"
CONTAINER="$(docker compose --profile "$PERFIL" ps -q "$ALVO_SERVICO" 2>/dev/null || docker compose ps -q "$ALVO_SERVICO")"

docker cp "$DUMP" "${CONTAINER}:${TEMP}"
if [ "$ALVO_SERVICO" = "db-test" ]; then
    # db-test comeca vazio (sem o schema da aplicacao) -- pg_restore recria tudo.
    docker exec -i "$CONTAINER" pg_restore -U inpi -d inpi --clean --if-exists "$TEMP"
else
    docker compose exec -T "$ALVO_SERVICO" pg_restore -U inpi -d inpi --clean --if-exists "$TEMP"
fi
docker exec "$CONTAINER" rm -f "$TEMP"

echo "==> restauracao concluida em '${ALVO_SERVICO}'. Conferindo algumas tabelas:"
docker exec "$CONTAINER" psql -U inpi -d inpi -c "SELECT count(*) AS leads FROM leads;"
docker exec "$CONTAINER" psql -U inpi -d inpi -c "SELECT count(*) AS organizacoes FROM organizacoes;"

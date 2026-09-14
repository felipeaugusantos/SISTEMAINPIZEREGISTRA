#!/bin/sh
set -eu

# Simulado periodico de restauracao: pega o backup mais recente, restaura no
# banco efemero de testes (db-test, perfil "test" do compose.yaml -- nunca
# toca em "db"/producao) e confere que as tabelas centrais vieram com dados
# compativeis com o banco de producao. Existe para detectar cedo os dois
# problemas do incidente de 05/09/2026: um backup corrompido/vazio, ou uma
# restauracao que "parece" ter dado certo mas perdeu tabelas no meio do
# caminho (o pg_restore --clean pode ignorar erros e seguir em frente).
#
# Uso:
#   ./docker/simulado-restauracao.sh                      # usa o backup mais recente em backups/
#   ./docker/simulado-restauracao.sh backups/inpi-20260905-*.dump
#
# Saida: 0 se todas as tabelas centrais bateram (dentro da tolerancia), 1 se
# alguma tabela ficou vazia/muito menor que a producao ou a restauracao falhou.
# Pensado para rodar via cron (ver scripts/instalar-simulado-restauracao.ps1
# ou a entrada equivalente direto no crontab da VPS) -- toda a saida vai para
# stdout/stderr, redirecionar para um arquivo de log na chamada do cron.

cd "$(dirname "$0")/.."

DUMP="${1:-}"
if [ -z "$DUMP" ]; then
    DUMP="$(ls -t backups/inpi-*.dump 2>/dev/null | head -n1 || true)"
fi
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "Nenhum backup encontrado em backups/." >&2
    exit 1
fi

echo "==> $(date -Iseconds) simulado de restauracao usando: $DUMP"

echo "==> subindo db-test"
docker compose --profile test up -d db-test
for _ in $(seq 1 30); do
    status="$(docker compose --profile test ps db-test --format '{{.Health}}' 2>/dev/null || true)"
    [ "$status" = "healthy" ] && break
    sleep 1
done
if [ "$status" != "healthy" ]; then
    echo "ERRO: db-test nao ficou saudavel a tempo." >&2
    exit 1
fi

CONTAINER_TESTE="$(docker compose --profile test ps -q db-test)"
CONTAINER_PROD="$(docker compose ps -q db)"
NOME_ARQUIVO="$(basename "$DUMP")"
TEMP="/tmp/${NOME_ARQUIVO}"

docker cp "$DUMP" "${CONTAINER_TESTE}:${TEMP}"
FALHOU_RESTORE=0
SAIDA_RESTORE="/tmp/simulado-restauracao-saida-$$.log"
docker exec -i "$CONTAINER_TESTE" pg_restore -U inpi -d inpi --clean --if-exists "$TEMP" >"$SAIDA_RESTORE" 2>&1 || FALHOU_RESTORE=1
cat "$SAIDA_RESTORE"
docker exec "$CONTAINER_TESTE" rm -f "$TEMP"

# Tabelas centrais e o tamanho minimo aceitavel: se a producao tem N linhas,
# a restauracao so falha o simulado se vier com menos de 90% disso -- deixa
# folga para o backup ter sido tirado um pouco antes da comparacao.
TABELAS="organizacoes usuarios_operacoes leads processos_monitorados sessoes_operacoes"
FALHOU=0

if [ "$FALHOU_RESTORE" -eq 1 ]; then
    # Achado (14/09/2026): um "could not create unique index" por dado
    # duplicado (violacao de integridade real em producao) ficava com o
    # mesmo tratamento de ruido benigno tipo "does not exist" de objeto
    # novo -- o simulado de 13/09 pegou exatamente esse caso (duplicidade
    # de idempotency_key em lembretes_crm) e so registrou como aviso,
    # sem falhar; o problema so virou visivel um dia depois, como bug
    # relatado pelo usuario (500 ao editar o lembrete). Erros de
    # constraint/indice unico agora derrubam o simulado de verdade.
    if grep -qiE "could not create unique index|duplicate key value|violates[a-z ]* constraint" "$SAIDA_RESTORE"; then
        echo "FALHA CRITICA: pg_restore encontrou violacao de integridade (indice/constraint unico nao pode ser criado) -- normalmente indica dado duplicado real em producao, nao so ruido de restauracao. Ver saida do pg_restore acima."
        FALHOU=1
    else
        echo "AVISO: pg_restore reportou erros (podem ser so 'does not exist' de objetos novos). Conferindo tabelas mesmo assim."
    fi
fi
rm -f "$SAIDA_RESTORE"
for tabela in $TABELAS; do
    n_teste="$(docker exec "$CONTAINER_TESTE" psql -U inpi -d inpi -tAc "SELECT count(*) FROM ${tabela};" 2>/dev/null || echo "-1")"
    n_prod="$(docker exec "$CONTAINER_PROD" psql -U inpi -d inpi -tAc "SELECT count(*) FROM ${tabela};" 2>/dev/null || echo "-1")"
    minimo=$(( n_prod * 90 / 100 ))
    if [ "$n_teste" -lt "$minimo" ]; then
        echo "FALHA: ${tabela} restaurou com ${n_teste} linha(s), producao tem ${n_prod} (minimo aceitavel: ${minimo})."
        FALHOU=1
    else
        echo "OK: ${tabela} restaurou com ${n_teste} linha(s) (producao: ${n_prod})."
    fi
done

echo "==> derrubando db-test (container efemero; --clean na proxima execucao substitui o conteudo)"
docker compose --profile test rm -sf db-test >/dev/null 2>&1 || true

if [ "$FALHOU" -eq 1 ]; then
    echo "==> $(date -Iseconds) SIMULADO FALHOU -- ver detalhes acima."
    exit 1
fi
echo "==> $(date -Iseconds) simulado de restauracao OK."

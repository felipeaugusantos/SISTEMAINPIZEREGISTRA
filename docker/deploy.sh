#!/bin/sh
set -eu

# Builda, tagueia com um numero de versao (1.0.0, 1.0.1, ...) + a data do dia
# e reinicia os servicos passados como argumento (default: api worker
# migrate). Cada build fica guardado sob a tag `<servico>:<versao>-<data>`
# alem de `<servico>:latest`, para permitir voltar para uma versao anterior
# sem rebuild (ver rollback.sh).
#
# A versao comeca em 1.0.0 e o numero de patch (o ultimo) e incrementado a
# cada deploy automaticamente -- fica guardado em .deploy-version (nao
# versionado no git, so nesta copia do repositorio). Para pular pra uma nova
# minor/major, edite .deploy-version manualmente (ex.: "1.1.0") antes de
# rodar o deploy.
#
# Uso:
#   ./docker/deploy.sh                  # api worker migrate rpi-sync (default)
#   ./docker/deploy.sh api               # so o api
#   ./docker/deploy.sh api worker
#
# api, worker e rpi-sync compartilham o mesmo Dockerfile/codigo (app/models.py
# etc.) -- todos entram no default para nao ficar nenhum rodando uma versao
# desatualizada sem perceber.
#
# Rode a partir da raiz do repositorio (/opt/zeregistra).
#
# Fase 0 (auditoria de 03/09/2026): este script SEMPRE mescla
# compose.production.yaml -- sem isso, `docker compose` sozinho so le
# compose.yaml e o ambiente sobe com APP_ENV=development, ADMIN_FORCE_HTTPS
# desligado e INTEGRATION_AUTH_ENABLED desligado em produção, sem que
# ninguem percebesse (achado F0-1/F0-2 da auditoria). O script agora falha
# cedo se o ambiente efetivo resolvido nao for "production", e falha no
# final se algum dos tres servicos que compartilham codigo (api/worker/
# rpi-sync) acabar rodando um commit diferente dos outros (achado F0-3).

MANTER_VERSOES="${MANTER_VERSOES:-5}"
SERVICOS="${*:-api worker migrate rpi-sync}"
ARQUIVO_VERSAO=".deploy-version"
COMPOSE="docker compose -f compose.yaml -f compose.production.yaml"

cd "$(dirname "$0")/.."

echo "==> git pull"
git pull origin main

if [ -f "$ARQUIVO_VERSAO" ]; then
    VERSAO_ANTERIOR="$(cat "$ARQUIVO_VERSAO")"
    MAJOR="$(echo "$VERSAO_ANTERIOR" | cut -d. -f1)"
    MINOR="$(echo "$VERSAO_ANTERIOR" | cut -d. -f2)"
    PATCH="$(echo "$VERSAO_ANTERIOR" | cut -d. -f3)"
    VERSAO="${MAJOR}.${MINOR}.$((PATCH + 1))"
else
    VERSAO="1.0.0"
fi
echo "$VERSAO" > "$ARQUIVO_VERSAO"
DATA="$(date +%Y-%m-%d)"
TAG_VERSAO="${VERSAO}-${DATA}"
export APP_VERSION="$TAG_VERSAO"
echo "==> versao: $TAG_VERSAO (commit $(git rev-parse --short HEAD))"

echo "==> verificando ambiente efetivo (compose.yaml + compose.production.yaml)"
AMBIENTE_EFETIVO="$($COMPOSE config 2>/dev/null | grep -m1 '^\s*APP_ENV:' | awk '{print $2}' | tr -d '"')"
if [ "$AMBIENTE_EFETIVO" != "production" ]; then
    echo "ERRO: APP_ENV efetivo resolvido e '${AMBIENTE_EFETIVO:-<vazio>}', esperado 'production'." >&2
    echo "compose.production.yaml nao esta sendo aplicado corretamente -- abortando antes de buildar." >&2
    exit 1
fi

echo "==> build: $SERVICOS"
GIT_SHA="$(git rev-parse HEAD)"
for servico in $SERVICOS; do
    $COMPOSE build --build-arg "GIT_SHA=${GIT_SHA}" "$servico"
done

for servico in $SERVICOS; do
    imagem="zeregistra-${servico}"
    docker tag "${imagem}:latest" "${imagem}:${TAG_VERSAO}"
    echo "==> ${imagem}:${TAG_VERSAO} marcada (commit ${GIT_SHA})"
done

# migrate roda as migrations pendentes e sai sozinho -- espera terminar antes
# de seguir para os servicos de longa duracao. Backup automatico antes de
# qualquer migration (Fase 0, item 8): se a migration der problema, da pra
# restaurar com docker/restaurar-banco.sh. `docker compose wait` propaga o
# codigo de saida do container de migration -- com `set -eu` isso ja faz o
# script inteiro falhar se a migration falhar (Fase 0, item 10).
#
# Achado 05/09/2026: o backup rodava incondicionalmente aqui mesmo quando
# nao havia NENHUMA migration pendente (ex.: deploy so de frontend) --
# com o banco em ~30GB isso gerava um dump de ~5.5GB por deploy, varias
# vezes ao dia. Agora so faz backup quando a revisao atual do banco
# diverge do topo das migrations da imagem que acabou de ser buildada.
case " $SERVICOS " in
    *" migrate "*)
        ATUAL="$($COMPOSE run --rm --entrypoint /app/.venv/bin/alembic migrate current 2>/dev/null | awk 'NF{print $1; exit}')"
        TOPO="$($COMPOSE run --rm --entrypoint /app/.venv/bin/alembic migrate heads 2>/dev/null | awk 'NF{print $1; exit}')"
        if [ -n "$ATUAL" ] && [ "$ATUAL" = "$TOPO" ]; then
            echo "==> nenhuma migration pendente (banco ja em $ATUAL) -- pulando backup"
        else
            echo "==> backup antes da migration"
            ./docker/backup-banco.sh
            # Fase 9 (ordem recomendada de deploy), item 1: "backup validado"
            # -- ate aqui so validavamos o ARQUIVO do dump (backup-banco.sh),
            # nunca se ele restaura de verdade com os dados certos, antes de
            # aplicar a migration em cima dele. Opcional (nao muda o
            # comportamento padrao) porque roda um pg_restore completo no
            # db-test -- pode levar minutos com o banco em dezenas de GB.
            # Recomendado (VALIDAR_BACKUP=1) para deploys com migration em
            # producao; sempre ligado seria lento demais pra deploys triviais.
            if [ "${VALIDAR_BACKUP:-}" = "1" ]; then
                echo "==> validando backup (simulado de restauracao)"
                ./docker/simulado-restauracao.sh
            fi
            echo "==> aplicando migrations"
            $COMPOSE up -d migrate
            $COMPOSE wait migrate
        fi
        ;;
esac

for servico in $SERVICOS; do
    if [ "$servico" != "migrate" ]; then
        echo "==> subindo $servico"
        $COMPOSE up -d "$servico"
    fi
done

# Retencao: mantem as ultimas $MANTER_VERSOES tags por servico (alem de
# `latest`), remove o resto para nao acumular imagens indefinidamente.
for servico in $SERVICOS; do
    imagem="zeregistra-${servico}"
    tags_antigas=$(
        docker images "$imagem" --format '{{.Tag}} {{.CreatedAt}}' \
            | grep -v '^latest ' \
            | sort -k2 -r \
            | awk 'NR>'"$MANTER_VERSOES"' {print $1}'
    )
    for tag in $tags_antigas; do
        echo "==> removendo versao antiga ${imagem}:${tag}"
        docker rmi "${imagem}:${tag}" 2>/dev/null || true
    done
done

# Fase 0, item 9: api, worker e rpi-sync compartilham o mesmo codigo e devem
# rodar exatamente o mesmo commit. Compara os tres containers que estiverem
# de pe agora (nao so os que este deploy tocou) -- pega tambem o caso de
# "deploy.sh api" deixar worker/rpi-sync desatualizados sem que ninguem note.
echo "==> verificando consistencia de commit entre api/worker/rpi-sync"
SHAS_DIVERGENTES=0
SHA_REFERENCIA=""
for servico in api worker rpi-sync; do
    container="zeregistra-${servico}-1"
    if ! docker inspect "$container" >/dev/null 2>&1; then
        continue
    fi
    sha_servico="$(docker inspect "$container" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
    echo "    ${servico}: ${sha_servico}"
    if [ -z "$SHA_REFERENCIA" ]; then
        SHA_REFERENCIA="$sha_servico"
    elif [ "$sha_servico" != "$SHA_REFERENCIA" ]; then
        SHAS_DIVERGENTES=1
    fi
done
if [ "$SHAS_DIVERGENTES" -eq 1 ]; then
    echo "ERRO: api/worker/rpi-sync nao estao no mesmo commit. Rode 'docker/deploy.sh api worker migrate rpi-sync' para alinhar." >&2
    exit 1
fi

echo "==> pronto (versao ${TAG_VERSAO}). verificando saude da api"
# Com ADMIN_FORCE_HTTPS=true (agora realmente aplicado -- achado F0-1/F0-2),
# a api redireciona (307) requisicoes que nao parecem vir de HTTPS. O
# healthcheck do proprio container (compose.production.yaml) ja manda esses
# cabecalhos; aqui precisa do mesmo, senao o curl direto sempre bate no
# redirect e nunca ve o 200 de verdade.
SAUDAVEL=0
for tentativa in 1 2 3 4 5; do
    sleep 3
    CODIGO_HTTP="$(
        curl -s -o /dev/null -w '%{http_code}' \
            -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-For: 127.0.0.1' \
            http://localhost:8000/health || echo "000"
    )"
    if [ "$CODIGO_HTTP" = "200" ]; then
        SAUDAVEL=1
        break
    fi
    echo "    tentativa ${tentativa}: health respondeu ${CODIGO_HTTP}, tentando de novo..."
done
if [ "$SAUDAVEL" -ne 1 ]; then
    echo "ERRO: api nao ficou saudavel apos o deploy (ultimo codigo HTTP: ${CODIGO_HTTP})." >&2
    exit 1
fi
echo "==> api saudavel (200)"

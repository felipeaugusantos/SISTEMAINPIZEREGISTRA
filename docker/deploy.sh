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

MANTER_VERSOES="${MANTER_VERSOES:-10}"
SERVICOS="${*:-api worker migrate rpi-sync}"
ARQUIVO_VERSAO=".deploy-version"

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
echo "==> versao: $TAG_VERSAO (commit $(git rev-parse --short HEAD))"

echo "==> build: $SERVICOS"
docker compose build $SERVICOS

for servico in $SERVICOS; do
    imagem="zeregistra-${servico}"
    docker tag "${imagem}:latest" "${imagem}:${TAG_VERSAO}"
    echo "==> ${imagem}:${TAG_VERSAO} marcada"
done

# migrate roda as migrations pendentes e sai sozinho -- espera terminar antes
# de seguir para os servicos de longa duracao.
case " $SERVICOS " in
    *" migrate "*)
        echo "==> aplicando migrations"
        docker compose up -d migrate
        docker compose wait migrate
        ;;
esac

for servico in $SERVICOS; do
    if [ "$servico" != "migrate" ]; then
        echo "==> subindo $servico"
        docker compose up -d "$servico"
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

echo "==> pronto (versao ${TAG_VERSAO}). api saudavel?"
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health || true

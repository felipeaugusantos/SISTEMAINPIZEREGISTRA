#!/bin/sh
set -eu

# Builda, tagueia com o commit atual e reinicia os servicos passados como
# argumento (default: api worker migrate). Cada build fica guardado sob a tag
# `<servico>:<sha-curto>` alem de `<servico>:latest`, para permitir voltar
# para uma versao anterior sem rebuild (ver rollback.sh).
#
# Uso:
#   ./docker/deploy.sh                  # api worker migrate (default)
#   ./docker/deploy.sh api               # so o api
#   ./docker/deploy.sh api worker rpi-sync
#
# Rode a partir da raiz do repositorio (/opt/zeregistra).

MANTER_VERSOES="${MANTER_VERSOES:-10}"
SERVICOS="${*:-api worker migrate}"

cd "$(dirname "$0")/.."

echo "==> git pull"
git pull origin main

SHA="$(git rev-parse --short HEAD)"
echo "==> commit atual: $SHA"

echo "==> build: $SERVICOS"
docker compose build $SERVICOS

for servico in $SERVICOS; do
    imagem="zeregistra-${servico}"
    docker tag "${imagem}:latest" "${imagem}:${SHA}"
    echo "==> ${imagem}:${SHA} marcada"
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

echo "==> pronto. api saudavel?"
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health || true

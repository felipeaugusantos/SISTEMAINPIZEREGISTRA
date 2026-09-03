#!/bin/sh
set -eu

# Volta um servico para uma versao anterior ja tagueada por deploy.sh, sem
# rebuild -- so retagueia `latest` e reinicia o container.
#
# Uso:
#   ./docker/rollback.sh api                 # lista as versoes disponiveis
#   ./docker/rollback.sh api ae63dc7          # volta o api para essa versao
#
# IMPORTANTE: isso so volta o codigo. Se a versao problematica tiver rodado
# uma migration nova, o banco continua no esquema novo -- reverter a imagem
# nao desfaz isso. Rode `alembic downgrade <revisao-anterior>` primeiro se
# for o caso (cada migration deste repo tem downgrade() pronto).

SERVICO="${1:-}"
SHA="${2:-}"

if [ -z "$SERVICO" ]; then
    echo "Uso: $0 <servico> [sha]"
    exit 1
fi

IMAGEM="zeregistra-${SERVICO}"

if [ -z "$SHA" ]; then
    echo "Versoes disponiveis de ${IMAGEM}:"
    docker images "$IMAGEM" --format '{{.Tag}}  {{.CreatedAt}}' | grep -v '^latest ' | sort -k2 -r
    echo
    echo "Uso: $0 $SERVICO <sha-da-lista-acima>"
    exit 0
fi

if ! docker image inspect "${IMAGEM}:${SHA}" >/dev/null 2>&1; then
    echo "Nao existe ${IMAGEM}:${SHA} localmente. Rode '$0 $SERVICO' sem o sha para ver as versoes disponiveis."
    exit 1
fi

echo "==> revertendo ${IMAGEM} para ${SHA}"
docker tag "${IMAGEM}:${SHA}" "${IMAGEM}:latest"
docker compose up -d "$SERVICO"

echo "==> pronto. Lembrete: isso NAO desfaz migrations aplicadas depois dessa versao."
if [ "$SERVICO" = "api" ]; then
    sleep 3
    curl -s -o /dev/null -w 'health: %{http_code}\n' http://localhost:8000/health || true
fi

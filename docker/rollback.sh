#!/bin/sh
set -eu

# Volta um servico para uma versao anterior ja tagueada por deploy.sh, sem
# rebuild -- so retagueia `latest` e reinicia o container.
#
# Uso:
#   ./docker/rollback.sh api                 # lista as versoes disponiveis
#   ./docker/rollback.sh api 1.0.2-2026-09-03 # volta o api para essa versao
#
# IMPORTANTE: isso so volta o codigo. Se a versao problematica tiver rodado
# uma migration nova, o banco continua no esquema novo -- reverter a imagem
# nao desfaz isso. Rode `alembic downgrade <revisao-anterior>` primeiro se
# for o caso (cada migration deste repo tem downgrade() pronto).

SERVICO="${1:-}"
VERSAO="${2:-}"

if [ -z "$SERVICO" ]; then
    echo "Uso: $0 <servico> [versao]"
    exit 1
fi

IMAGEM="zeregistra-${SERVICO}"

if [ -z "$VERSAO" ]; then
    echo "Versoes disponiveis de ${IMAGEM}:"
    docker images "$IMAGEM" --format '{{.Tag}}  {{.CreatedAt}}' | grep -v '^latest ' | sort -k2 -r
    echo
    echo "Uso: $0 $SERVICO <versao-da-lista-acima>"
    exit 0
fi

if ! docker image inspect "${IMAGEM}:${VERSAO}" >/dev/null 2>&1; then
    echo "Nao existe ${IMAGEM}:${VERSAO} localmente. Rode '$0 $SERVICO' sem a versao para ver as disponiveis."
    exit 1
fi

echo "==> revertendo ${IMAGEM} para ${VERSAO}"
docker tag "${IMAGEM}:${VERSAO}" "${IMAGEM}:latest"
docker compose up -d "$SERVICO"

echo "==> pronto. Lembrete: isso NAO desfaz migrations aplicadas depois dessa versao."
if [ "$SERVICO" = "api" ]; then
    sleep 3
    curl -s -o /dev/null -w 'health: %{http_code}\n' http://localhost:8000/health || true
fi

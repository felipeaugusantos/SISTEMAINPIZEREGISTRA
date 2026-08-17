# Fase 11 — Observabilidade e segurança

## Endpoints operacionais

- `/health`: visão geral da API, banco e Redis;
- `/health/db`: conectividade e latência do banco;
- `/health/rpi`: idade, integridade, erros e última RPI importada;
- `/health/queue`: pendências, processamento, retries e falhas da fila;
- `/metrics`: métricas em formato Prometheus, sem dados pessoais.

## Segurança e rastreabilidade

O middleware gera/propaga `X-Request-ID`, grava logs estruturados e registra eventos operacionais com tenant, ator, status e duração. Sessões possuem revogação e expiração por inatividade; MFA/TOTP usa segredo protegido e versionado. As rotas administrativas usam permissões por módulo e as consultas aplicam organização/tenant, retornando 403/404 para IDs fora do escopo.

## Continuidade

Backups são gerados pelos scripts `scripts/backup-banco.ps1` e `scripts/restaurar-backup.ps1`. A restauração deve ocorrer primeiro em banco descartável, com validação de integridade, execução das migrations e smoke tests antes do cutover. O rollback de aplicação usa a versão anterior e o downgrade Alembic somente em janela controlada.

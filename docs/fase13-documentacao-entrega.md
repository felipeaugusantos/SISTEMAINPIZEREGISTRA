# Fase 13 — Documentação e entrega

## Instalação

1. Instale Python 3.11+, PostgreSQL 15+ e Redis 7+.
2. Crie o ambiente: `python -m venv .venv` e instale `pip install -e .`.
3. Copie `.env.example` para `.env` e configure banco, Redis, domínio e segredos.
4. Execute `alembic upgrade head`.
5. Inicie a API com `uvicorn app.main:app --host 0.0.0.0 --port 8000` e o worker com `python -m app.worker`.

## Variáveis e migrations

As variáveis estão documentadas no `.env.example` e em `app/settings.py`. Em produção, defina senha forte, `SECURITY_MASTER_KEY`, versão atual/anterior de chave, `AUDIT_IP_SALT`, `GATEWAY_WEBHOOK_SECRET`, `DATABASE_URL`, `REDIS_URL` e `ADMIN_FORCE_HTTPS=true`. Nunca commite `.env` ou segredos.

Confira o estado com `alembic current`, liste pendências com `alembic history` e aplique somente em homologação primeiro com `alembic upgrade head`.

## Deploy e rollback

O deploy recomendado é: backup validado, migração em homologação, smoke tests (`/health`, `/health/db`, `/health/rpi`, `/health/queue`), subida do worker, migração controlada e monitoramento de `/metrics`. Para rollback, interrompa o tráfego, preserve logs/request IDs, restaure a imagem anterior e reverta a migration somente se ela for reversível e ainda não houver dados dependentes. Restaure backup em banco descartável antes de qualquer restauração produtiva.

Scripts: `scripts/backup-banco.ps1`, `scripts/restaurar-backup.ps1` e `scripts/instalar-backup-diario.ps1`.

## Operação e suporte

Monitore fila, RPI, latência, taxa de erro e falhas de worker. Toda ocorrência deve incluir horário, tenant, endpoint, request ID, usuário, migration/versão e evidência. Escalone incidentes de segurança imediatamente; não envie dados pessoais em tickets.

## Matriz de permissões

`finance.*` controla exclusivamente financeiro; `juridico.*`, prazos e protocolos; `leads.*` controla CRM/propostas; `production.*` controla auditoria/observabilidade; `admin.*` é reservado a administradores. Acesso a um módulo não concede acesso a outro. Toda rota valida organização e objeto antes de retornar dados.

## Indicadores

- RPI: última RPI, idade dos dados, integridade, anomalias e erros.
- API: requisições, erros, taxa de erro e p50/p95/p99 de latência.
- CRM: conversão, oportunidades sem responsável/próxima ação e tarefas atrasadas.
- Protocolo: aceite, pagamento, documentos pendentes, início e cumprimento do SLA de 24 horas.
- Financeiro: aberto, pago, vencido, inadimplência e conciliação idempotente.
- Busca: Recall/Precision@5/10/20, MRR, latência e falsos negativos críticos.

## Critérios finais de aceite

Os 15 critérios da Fase 13 estão formalizados no checklist `docs/release-candidate.md` e devem ser comprovados por teste automatizado, evidência de auditoria ou validação operacional antes da promoção.

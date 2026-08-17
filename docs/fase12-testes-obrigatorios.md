# Fase 12 — Testes obrigatórios

A suíte cobre autenticação, permissões, tenants/RLS e IDs cruzados; importação RPI repetida, corrompida, parcial, vazia, queda abrupta e edição pulada; idempotência de filas e operações financeiras; propostas, aceite, SLA, documentos, assinatura e portal; filtros, paginação e contratos visuais; busca/ranking, recall, falsos negativos críticos e auditoria.

Os cenários de PostgreSQL real permanecem marcados para execução quando `TEST_DATABASE_URL` estiver configurado. Os demais gates rodam em todas as execuções locais e no CI. A validação de infraestrutura da fase inclui `/health/db`, `/health/queue` e `/metrics` em `tests/test_phase12_mandatory.py`.

# Changelog

## Busca avancada de anterioridade — 2026-08-18

- Consulta por estrategias exata, radical, prefixo, sufixo, fonetica e similaridade textual.
- Filtros por Nice, titular, situacao, periodo, apresentacao e Classificacao de Viena.
- Projetos de busca salvos, isolados por tenant, com reprocessamento e evidencias por resultado.
- Pesos configuraveis no score combinado, mantendo revisao humana obrigatoria.
- Dataset e gate de benchmark preservam Recall/Precision@5/@10/@20, MRR, p50/p95/p99 e falsos negativos criticos.

## RC2 — Fase 13 — 2026-08-17

- Documentação consolidada de instalação, ambiente, migrations, deploy, rollback, operação, suporte, permissões e indicadores.
- Checklist final de aceite e evidências do Release Candidate.
- README atualizado com comandos de instalação, atualização e execução.
- Critérios de segurança, RPI, CRM, propostas, documentos, financeiro, busca, interface, observabilidade e testes formalizados.

## RC1 — 2026-08-17

- Confiabilidade da RPI: validação de arquivos corrompidos/parciais, checksum e idempotência.
- CRM: cadências idempotentes, timeline e propostas vinculadas a leads.
- Propostas: aceite, SLA de 24 horas, pagamento, protocolo e auditoria.
- Portal do cliente: login isolado, documentos, mensagens, arquivos, preferências e revogação.
- Documentos: assinatura, hash, versão, validade e trilha de auditoria.
- Busca visual: score explicável, OCR opcional, Viena e gates de benchmark.
- Vigilância RPI: preferências, colidências, revisão humana e notificações no portal.
- Financeiro: catálogo de serviços, contratações vinculadas, parcelas e idempotência.

Este RC não deve ser promovido enquanto houver gates pendentes no checklist.

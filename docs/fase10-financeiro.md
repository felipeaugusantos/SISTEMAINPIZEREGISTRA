# Fase 10 — Financeiro

O módulo financeiro agora cobre o ciclo operacional de serviços, contratação e recebimento sem misturar permissões jurídicas.

## Entregas

- catálogo multi-tenant de serviços, inclusive recorrentes;
- contratação com parcelas e chave de idempotência;
- vínculo obrigatório a proposta, oportunidade (lead) ou processo;
- GRUs vinculadas a proposta/processo;
- consulta de inadimplência;
- conciliação de eventos assinados do gateway (preparação para integração futura);
- emissão idempotente de recibo por parcela paga;
- agenda de renovação e manutenção por processo;
- histórico e auditoria financeira.

## Proteções

O índice único de `idempotency_key`, as chaves únicas de contratação/recibo e o bloqueio da parcela durante a conciliação impedem duplicidade em requisições concorrentes. O webhook valida assinatura, tenant, parcela e valor antes de baixar.

As rotas usam exclusivamente `finance.view`, `finance.manage`, `finance.approve` e `finance.export`; permissões jurídicas não concedem acesso financeiro automaticamente.

Rotas adicionais:

- `GET /v1/admin/financeiro/inadimplencia`
- `POST /v1/admin/financeiro/guias`
- `POST /v1/admin/financeiro/renovacoes`
- `POST /v1/admin/financeiro/parcelas/{id}/recibo`

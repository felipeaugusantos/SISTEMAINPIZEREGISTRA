# Fase 4 - CRM e automacao comercial

## Entregas validadas

- empresa, contato e oportunidade mantidos como entidades separadas;
- responsavel e proxima acao controlados pela politica CRM;
- timeline unica para interacoes, tarefas, propostas, documentos e eventos;
- cadencias automaticas com tarefas idempotentes por lead, cadencia e passo;
- alertas de atraso, ausencia de responsavel e ausencia de proxima acao;
- dashboard de conversao por etapa e responsavel;
- propostas vinculadas ao lead e a pesquisa de marca;
- acao de gerar proposta disponivel em Leads -> Por pesquisa.

## Evidencias do gate

Validacao executada em 2026-08-17:

- `tests/test_crm.py`;
- `tests/test_crm_history.py`;
- `tests/test_leads.py`;
- `tests/test_phase4_proposals.py`;
- total: 33 testes aprovados.

O gate foi aprovado: oportunidades abertas sem responsavel ou proxima acao sao
identificadas pelo dashboard e bloqueadas conforme a politica CRM configurada.
As reexecucoes de cadencia preservam a chave de idempotencia e nao criam tarefas
duplicadas.

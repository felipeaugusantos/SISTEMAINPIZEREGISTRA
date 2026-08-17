# Fase 10 - Financeiro e portal de servicos

## Entregas validadas

- catalogo de servicos por organizacao;
- contratacao vinculada a lead ou processo;
- parcelas, vencimentos, baixas, estornos e recibos/historico;
- GRUs e retribuicoes do INPI relacionadas ao atendimento;
- conciliacao por evento de gateway;
- identificacao de parcelas vencidas e inadimplencia;
- servicos recorrentes, adicionais, renovacao e manutencao por catalogo;
- permissoes `finance.view`, `finance.manage`, `finance.approve` e `finance.export`;
- webhook HMAC com referencia idempotente e lock transacional da parcela.

## Gate e evidencias

Validacao executada em 2026-08-17:

- todo lancamento de contratacao exige lead ou processo;
- chave `idempotency_key` e indice unico impedem contratacoes duplicadas;
- baixa concorrente de parcela usa `FOR UPDATE`;
- eventos repetidos do gateway retornam resposta idempotente;
- a suite completa passou: 354 testes aprovados.

O gate foi aprovado. O webhook exige `GATEWAY_WEBHOOK_SECRET`, valida HMAC,
concilia apenas a parcela da mesma organizacao e nao interfere em decisoes juridicas.

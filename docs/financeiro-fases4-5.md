# Financeiro — implementação das fases 4 e 5

Data: 26/09/2026  
Escopo: indicadores de competência versus caixa e proteção de erros de NFS-e.

## Fase 4 — competência, caixa realizado e vencimentos

- Mantido o endpoint `/v1/admin/financeiro/dre` como demonstrativo gerencial por competência; os valores continuam sendo os valores integrais dos lançamentos não cancelados.
- Criado `GET /v1/admin/financeiro/dre/caixa?data_de=AAAA-MM-DD&data_ate=AAAA-MM-DD`.
- O realizado soma `ParcelaFinanceira.valor_pago` quando `pago_em` pertence ao intervalo.
- O saldo em aberto soma `max(valor - valor_pago, 0)` para parcelas não canceladas com vencimento dentro do intervalo. Parcelas vencidas antes do intervalo não aparecem nesse indicador de período.
- Os dois cálculos filtram lançamento e parcela pela organização autenticada; lançamentos cancelados não entram.
- A tela Plano de contas & DRE apresenta separadamente competência, caixa realizado e vencimentos, com aviso de que vencimento não representa recebimento garantido e de que o DRE é gerencial, não fiscal/societário.
- A lucratividade por cliente mantém a margem por competência e passa a exibir recebido/pago por data de baixa e a receber/a pagar por vencimento. Clientes com movimento de caixa no período aparecem mesmo sem lançamento cuja competência caia no intervalo.
- Não foi alterada regra de baixa, pagamento, aprovação ou configuração de integração.

Limite do modelo atual: `ParcelaFinanceira` guarda um único `pago_em` e um acumulado `valor_pago`, não um razão de recebimentos parciais. O indicador usa essa representação existente; conciliação detalhada de múltiplas baixas exigiria entidade de movimentação própria e fica fora desta fase.

## Fase 5 — sanitização dos erros de NFS-e

- Exceções arbitrárias de adaptadores não são devolvidas na resposta HTTP nem salvas em `erro_detalhe`.
- A resposta externa é uma mensagem genérica com HTTP 502; a tentativa continua sendo registrada com status `erro` e detalhe operacional constante, sem texto do provedor.
- Mensagem de adaptador indisponível também é controlada, sem repassar texto livre da exceção.
- Não foram incluídos tokens, documentos, payloads nem exceções brutas em logs novos.

## Verificação

- `tests/test_financeiro.py` e `tests/test_api_nfse.py`: 61 testes passaram.
- Cobertura acrescentada para saldos de caixa separados no DRE e por cliente, intervalo invertido, cliente com movimento apenas no caixa e mensagem/detalhe de erro sem conteúdo potencialmente sensível.
- Deploy não realizado nesta alteração.

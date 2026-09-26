# Auditoria financeira — linha de base e plano das Fases 0–3

Data: 2026-09-26
Commit auditado: `7f945f4`
Escopo: código e documentação do checkout local; sem acesso ou confirmação do estado do VPS.

## Fase 0 — linha de base (somente leitura)

### Fluxo confirmado

- Lançamentos de contas a pagar/receber criam parcelas; a baixa manual exige o valor integral da parcela.
- A baixa manual pode ser feita com `finance.manage`; estorno e cancelamento exigem `finance.approve`.
- Contratações derivadas de serviço/proposta criam contas a receber; eventos webhook também podem baixar ou estornar parcelas.
- Importação OFX grava transações bancárias e tenta conciliar automaticamente apenas créditos sem ambiguidade, pelo valor e janela de três dias.
- A conciliação manual atualmente exige apenas igualdade entre valor pago da parcela e valor da transação.
- Os adaptadores disponíveis para pagamentos e NFS-e são exclusivamente sandbox; não há cobrança nem emissão fiscal real integrada neste checkout.
- O DRE usa competência e valor total dos lançamentos não cancelados; o endpoint de lucratividade por cliente também agrega valores totais, sem separar realizado e em aberto.
- A listagem principal de lançamentos limita a consulta a 200 itens e informa como total a quantidade devolvida, sem paginação nessa rota.

### Achados prioritários

| ID | Achado comprovado | Impacto | Prioridade | Proposta |
|---|---|---|---|---|
| F0-01 | Conciliação manual não verifica crédito/débito, parcela paga, nem vínculo anterior da parcela; o modelo não tem unicidade para `TransacaoBancaria.parcela_id`. | Associação incorreta ou duplicada de movimentações. | Alta | Validar natureza e estado, bloquear vínculos duplicados sob transação e adicionar proteção no banco após verificar dados existentes. |
| F0-02 | OFX é lido integralmente antes de verificar o limite de 5 MB. | O limite não impede alocação de memória proporcional a um upload muito maior. | Média | Ler em blocos limitados e abortar ao exceder o limite. |
| F0-03 | Webhook é deduplicado por organização + referência da cobrança, não por ID único do evento. Payload bruto inteiro é armazenado. | Um evento posterior da mesma cobrança pode ser descartado; retenção excessiva de dados no payload. | Alta | Persistir `event_id` do provedor (com fallback documentado), validar transições e armazenar payload minimizado/mascarado. Depende do contrato do PSP. |
| F0-04 | Baixa de conta a pagar exige `finance.manage`; não há aprovação prévia de despesa. | O fluxo é rápido, mas não separa quem registra de quem paga. | Média | Decisão do usuário: usuários com permissão financeira registram contas a pagar diretamente, sem etapa de aprovação. Preservar essa regra e revisar se a permissão está restrita aos papéis pretendidos. |
| F0-05 | Integrações financeiras e fiscais são somente sandbox. | O QR/boleto/NFS-e demonstrativo não é pagável nem documento fiscal real. | Alta | Selecionar PSP e provedor/município antes de desenhar produção. |
| F0-06 | Listagem principal limita em até 200 e reporta apenas itens retornados. | Histórico extenso pode parecer completo quando está truncado. | Média | Paginação explícita, total real e interface de navegação. |
| F0-07 | DRE é por competência, mas a tela precisa distinguir esse critério de caixa realizado/previsto. | Leitura gerencial equivocada se valores em aberto forem entendidos como dinheiro recebido/pago. | Média | Rotular as bases e separar relatórios de competência, caixa e previsão. |
| F0-08 | Erro de emissão NFS-e é devolvido e armazenado como texto bruto da exceção. | Possível divulgação de detalhes do provedor ao operador e retenção excessiva em logs/tabela. | Média | Mensagem pública controlada e detalhe técnico sanitizado. |
| F0-09 | O grafo Alembic tinha dois heads independentes (`i1d2e3f4g5h6` e `zz32u7y3s519`). | `upgrade head` era ambíguo até convergir as linhas. | Alta | Merge revision sem DDL antes da migration financeira. |

### Referências no checkout

- Baixa, estorno e cancelamento: `app/api/financeiro.py` (funções `baixar`, `estornar`, `cancelar`).
- Listagem e DRE: `app/api/financeiro.py` (funções `listar`, `obter_dre`, `obter_lucratividade_clientes`).
- OFX e conciliação: `app/api/conciliacao.py`.
- Modelo de transação, parcela e webhook: `app/models/financeiro.py`.
- Webhook de pagamento: `app/api/pagamentos.py`.
- Adaptadores disponíveis: `app/pagamentos.py` e `app/nfse.py`.
- Erro de emissão: `app/api/nfse.py`.

## Fases 1–3 — gates de implementação

### Fase 1 — controles de acesso e integridade

- Decisão do usuário: contas a pagar entram automaticamente no fluxo para usuários com permissão financeira; não criar fila de aprovação.
- Implementação local não adiciona etapa de aprovação. Registro e baixa existentes continuam protegidos por `finance.manage`; estorno e cancelamento por `finance.approve`.
- Não foi necessário alterar código nesta fase para aplicar a decisão; o comportamento continua sem bloqueio adicional.

### Fase 2 — ciclo de eventos de pagamento (implementada localmente)

- Não conectar PSP real sem escolha do provedor e contrato de webhook.
- Separar chave idempotente do evento e referência da cobrança. Usar `event_id` se fornecido; no sandbox, usar hash do payload canônico.
- Persistir apenas parcela, valor e ID do evento normalizados, não o payload arbitrário do PSP.
- Limitar cobrança/webhook a contas a receber e recalcular corretamente o estado do lançamento após estorno.
- A migration conserva os eventos existentes como `legacy:<id>`; seu downgrade recusa se a nova estrutura já tiver múltiplos eventos para a mesma cobrança.

### Fase 3 — conciliação bancária (implementada localmente)

- Validação manual agora exige crédito, parcela paga, igualdade de valor, mesma organização e ausência de vínculo conciliado prévio.
- Conciliar trava a transação e a parcela; a reconciliação automática trava candidatas e consulta vínculos depois do lock para reduzir corrida entre importações.
- O upload lê no máximo 5 MB + 1 byte antes de rejeitar.
- Listagem de OFX tem paginação explícita, total real e controles anterior/próxima.
- A unicidade global no banco por parcela não foi adicionada, pois não há acesso à base de produção para verificar vínculos duplicados históricos. Os fluxos da aplicação usam lock/validação; a constraint pode ser fase seguinte após auditoria dos dados.

## Limites desta auditoria

- Não comprova versão em produção, configuração do VPS, credenciais ou configuração externa de banco/provedor.
- Não afirma conformidade fiscal ou contábil; isso exige validação com contador, município e PSP escolhidos.
- Testes focados executados: 78 aprovados (`test_conciliacao.py`, `test_api_pagamentos.py`, `test_financeiro.py`, `test_financeiro_proposta_sync.py`).
- `alembic heads` retorna um único head após adicionar merge revision.
- A geração SQL offline da migration nova, a partir do merge, passou. A geração offline da cadeia inteira ainda falha numa migration antiga (`zn00i5m1g397_retribuicoes_valores_2025.py`), que chama `.first()` num resultado inexistente no modo offline; isso não foi modificado nesta tarefa.
- Nenhuma migration foi aplicada a banco; nenhum deploy foi feito.

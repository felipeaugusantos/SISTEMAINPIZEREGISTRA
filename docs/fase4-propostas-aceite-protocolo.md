# Fase 4 — Propostas, aceite e protocolo em 24 horas

## Entregas aplicadas

- Propostas continuam versionadas e com PDF padronizado.
- Link público possui token aleatório com hash persistido e expiração.
- Aceite público é gravado com data/hora e muda a proposta para `aceita`.
- Pagamento possui estado (`pendente`, `parcial`, `confirmado`, `cancelado`) e data de confirmação.
- O SLA começa quando o aceite e o pagamento confirmado liberam a operação; o prazo operacional é de 24 horas corridas.
- O SLA expõe os estados `aguardando_aceite`, `aguardando_pagamento`, `aguardando_documentos`, `em_prazo`, `vencido` e `protocolado`.
- O responsável, número do protocolo, comprovante e motivo de atraso ficam registrados na proposta.
- Rotas administrativas de pagamento, protocolo e consulta do SLA são protegidas por organização e permissão.
- Eventos de auditoria registram alterações de pagamento e protocolo.

## Operação

1. Envie a proposta pelo link público.
2. O cliente aceita; o sistema registra a versão e o horário.
3. Confirme o pagamento em `PATCH /v1/admin/propostas/{id}/pagamento`.
4. Complete os documentos pendentes e acompanhe `GET /v1/admin/propostas/{id}/sla`.
5. Registre o protocolo ou o motivo do atraso em `PATCH /v1/admin/propostas/{id}/protocolo`.

O protocolo só é considerado concluído quando há número informado. Sem número, o motivo de atraso é obrigatório e permanece auditável.

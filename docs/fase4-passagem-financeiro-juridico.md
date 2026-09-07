# Fase 4 — passagem para financeiro e jurídico

## Fluxo implantado

1. O aceite da proposta, por operador, link público, portal ou Clicksign,
   cria uma contratação e um lançamento a receber usando o valor assinado.
2. A oportunidade avança por `proposta_aceita` até
   `aguardando_pagamento`, sem depender de atualização manual do CRM.
3. A baixa integral da parcela mantém o financeiro como fonte de verdade,
   confirma o pagamento e avança a oportunidade para
   `pagamento_confirmado`.
4. A proposta passa a aparecer em **Operação jurídica → Novos serviços para
   iniciar**, com cliente, marcas, classes, valor e pendências documentais.
5. Um usuário com `legal.manage` assume o atendimento. O sistema registra
   responsável, data, autor, auditoria e avança a oportunidade para `ganho`.
6. O protocolo avança a oportunidade para `protocolo_inpi`; fluxos antigos
   que protocolarem diretamente também formalizam o recebimento jurídico.

## Controles

- A fila jurídica só recebe proposta aceita com pagamento confirmado.
- O recebimento é idempotente para o mesmo responsável e não permite que
  outro usuário sobrescreva silenciosamente quem já assumiu.
- Se um pagamento for estornado depois do recebimento, o serviço não some da
  fila: passa a exibir bloqueio financeiro.
- A procuração continua sendo o documento mínimo obrigatório, e documentos
  marcados como obrigatórios ou vencidos aparecem como pendência.
- Todas as consultas e alterações são isoladas por organização.
- Eventos de CRM, financeiro e jurídico aparecem na linha do tempo da
  oportunidade.

## Migração

`ha53v9c5o175` adiciona à proposta o instante e o usuário que receberam o
serviço no jurídico. O downgrade remove apenas esses metadados; não altera
lançamentos, propostas, documentos ou processos.

# Fase 3 — CRM e automação comercial

## Entregas consolidadas

- empresa, contato, lead/oportunidade e pesquisa permanecem vinculados por tenant;
- validação de responsável e próxima ação conforme a política CRM;
- timeline operacional com pesquisas, contatos, documentos, GRUs, propostas e eventos de domínio;
- cadências geram tarefas com chave idempotente por lead, cadência e passo;
- reexecução da mesma cadência informa tarefas criadas e tarefas ignoradas por idempotência;
- automações de status/fase registram eventos de domínio e não duplicam lembretes;
- propostas permanecem vinculadas ao lead e aparecem na timeline;
- ação “Gerar proposta” está disponível em Leads → Por pesquisa;
- dashboard existente expõe oportunidades sem responsável, sem próxima ação e atrasadas.

## Atualização da Fase 3

- Novas propostas recebem o `pesquisa_id` da pesquisa de origem e validam o vínculo por organização e oportunidade.
- Interações registradas também geram eventos tipados `crm.interacao_registrada` na trilha operacional.
- O endpoint de lembretes expõe `alertas_atraso` para ações pendentes vencidas.
- A chave de idempotência das tarefas automáticas passou a ser parte explícita do contrato do modelo e da migration.

## Critério de aceite

Os testes de leads, CRM, histórico e automações devem permanecer aprovados. O fluxo operacional deve ser conferido com uma oportunidade real de homologação: criar pesquisa, atribuir responsável, definir próxima ação, gerar proposta, aplicar cadência duas vezes e conferir uma única tarefa por passo na timeline.

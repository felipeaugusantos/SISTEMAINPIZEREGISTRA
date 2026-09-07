# Fase 6 — automações do fluxo

## Escopo implantado

O worker reconcilia a cada hora as propostas aceitas e mantém tarefas internas
para três passagens da contratação:

- proposta aceita sem pagamento confirmado: cobrar pagamento;
- pagamento confirmado sem recebimento jurídico: receber o novo serviço;
- documentação liberada e serviço recebido sem protocolo: protocolar dentro
  do SLA.

As tarefas são idempotentes, vinculadas ao lead e à proposta e respeitam os
responsáveis comercial e jurídico já definidos. Quando a etapa é concluída, a
tarefa anterior ainda pendente é encerrada automaticamente para não poluir a
agenda. Propostas e leads arquivados não geram novas tarefas.

## Configuração e segurança

As regras aparecem em **Configuração → Regras automáticas** e podem ser
ativadas, desativadas ou ter o prazo ajustado por organização. A execução usa
chaves únicas no banco, registra eventos na trilha operacional e pode ser
repetida após falhas sem duplicar tarefas.

Esta fase usa somente tarefas e notificações internas. Não depende de API do
WhatsApp e não envia mensagens externas automaticamente.

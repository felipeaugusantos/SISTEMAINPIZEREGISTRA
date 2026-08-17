# Fase 6 - Portal do cliente

## Entregas validadas

- login separado do ambiente operacional;
- processos, propostas, documentos, pagamentos e GRUs no resumo do cliente;
- pendencias documentais e assinatura pelo portal;
- mensagens, envio e listagem de arquivos;
- notificacoes e historico de eventos auditados;
- criacao de acesso pelo responsavel do atendimento;
- bloqueio e revogacao imediata das sessoes ativas;
- filtros simultaneos por cliente, lead e organizacao em todas as consultas.

## Evidencias do gate

Validacao executada em 2026-08-17:

- controles de RLS e isolamento verificados no PostgreSQL real;
- MFA, auditoria e revogacao validados nos testes de seguranca;
- rotas de portal compiladas e integradas ao app;
- suite completa: 354 testes aprovados.

O gate foi aprovado: o cliente fica restrito ao proprio lead e organizacao, e o
operador consegue desativar o acesso e revogar as sessoes existentes.

# Fase 5 - Propostas, aceite e protocolo

## Entregas validadas

- propostas versionadas e PDF padronizado;
- link publico com token aleatorio armazenado somente como hash e expiracao;
- aceite eletronico com data/hora e versao da proposta;
- confirmacao de pagamento com status e data;
- checklist de documentos por etapa;
- contador de SLA de 24 horas corridas apos aceite, pagamento e documentos;
- responsavel, numero e comprovante do protocolo;
- motivo obrigatorio quando o protocolo nao e registrado;
- auditoria de pagamento, aceite e protocolo.

## Evidencias do gate

Validacao executada em 2026-08-17:

- `tests/test_phase4_proposals.py`;
- `tests/test_leads.py`;
- `tests/test_crm_history.py`;
- `tests/test_auditing.py`;
- total: 32 testes aprovados.

O gate foi aprovado: a proposta expoe aceite, pendencias, inicio e prazo do SLA,
responsavel, protocolo ou motivo de atraso. O estado `protocolado` somente e
atribuido quando existe numero de protocolo.

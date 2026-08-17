# Fase 7 - Assinatura digital e documentos

## Entregas validadas

- assinatura de procuracao, proposta e contrato por documento tipado;
- hash SHA-256 do conteudo canonico;
- registro de IP hash, data/hora, cliente, versao e provedor;
- versionamento e invalidacao da assinatura anterior quando metadados mudam;
- validade e bloqueio de documentos expirados;
- checklist por etapa e bloqueio de avancos com pendencias;
- trilha de auditoria de criacao, alteracao, assinatura e invalidacao;
- campo de provedor preparado para Clicksign, DocuSign ou outro integrador.

## Evidencias do gate

Validacao executada em 2026-08-17:

- `tests/test_phase6_documents.py`;
- `tests/test_leads.py`;
- `tests/test_phase4_proposals.py`;
- `tests/test_juridico.py`;
- `tests/test_auditing.py`;
- total: 49 testes aprovados.

O gate foi aprovado: alteracoes no documento geram nova versao e removem a
assinatura anterior; documentos pendentes ou expirados impedem a etapa que os exige.

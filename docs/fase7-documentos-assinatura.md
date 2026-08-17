# Fase 7 — Documentos e assinatura

- Propostas aceitas pelo link público ou pelo portal geram uma evidência própria
  em `assinaturas_propostas_comerciais`, com versão, hash, IP hash, data/hora e
  provedor.
- Procurações, contratos e demais documentos do lead são assinados no portal.
  O hash canônico, a versão, o cliente, o IP hash e a data ficam em
  `assinaturas_documentos_lead`.
- Toda alteração de número, data, status, observação, validade ou obrigatoriedade
  de um documento gera uma entrada em `versoes_documentos_lead`, incrementa a
  versão e invalida a assinatura anterior.
- Arquivos enviados ao portal recebem SHA-256 em `arquivos_clientes_portal`.
- Documentos obrigatórios vencidos, pendentes ou ausentes bloqueiam as etapas de
  protocolo/processo e o início do SLA quando aplicável.
- Auditoria registra assinatura, alteração, consulta de versões e bloqueio de
  etapa.

Aplicar com `alembic upgrade head`. As revisões desta fase são:
`f63a8b9c0d12` (assinaturas de propostas) e `g74b9c0d1e23` (hash de arquivos).

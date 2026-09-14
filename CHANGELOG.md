# Changelog

> **Este arquivo está desatualizado desde 26/08/2026.** As atualizações do
> sistema passaram a ser registradas na **Central de Atualizações**
> (`/admin/atualizacoes`), com evidências de teste, impacto e plano de
> rollback por versão. Este histórico abaixo é mantido só como referência
> do período anterior a essa migração.

## P2 concluído — 2026-08-26

- Uploads do portal e documentos de ativos usam a camada de storage configurável, com suporte a S3/MinIO e fallback local.
- OpenTelemetry opcional integrado à API via `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Snapshots visuais versionados e E2E do portal adicionados ao Playwright.
- Outbox transacional de eventos criado com migration `ac74e9f0b125`.
- Dependência `httpx` fixada em versão compatível; aviso residual pertence ao TestClient do Starlette e está documentado para migração futura.

## Escala e qualidade — 2026-08-26

- Ampliados smoke tests E2E para o portal do cliente.
- Adicionada captura visual automatizada das telas principais.
- Criada camada de armazenamento local/S3 compatível em `app/storage.py`.
- Documentadas regras formais de versionamento da API em `docs/api-versioning.md`.
- Criado script operacional `scripts/aplicar-p2.ps1` para migrations, lint, testes e E2E.

## Qualidade de código e lint — 2026-08-26

- Reformatados módulos Python e testes com Ruff.
- Corrigidos imports e exceções sem encadeamento explícito (`B904`).
- Padronizado limite de linha do projeto para 120 caracteres.
- Ruff e formatação passam sem erros.

## Hardening e isolamento de produção — 2026-08-26

- Tenant desconhecido não cai mais na organização padrão em produção.
- Health checks detalhados e métricas protegidos por `X-Health-Key`.
- Webhook Clicksign otimizado por envelope e idempotente por evento.
- CORS atualizado para clientes que utilizam proteção CSRF e monitoramento autenticado.
- Teste da versão do asset de análise corrigido.

## Isolamento por empresa e módulos operacionais — 2026-08-18

- CRM passou a ter permissões próprias, separadas de Leads.
- Planos podem habilitar individualmente Leads, CRM, Processos Monitorados e Operação Jurídica.
- Processos e operação jurídica deixaram de herdar o módulo Leads; aliases antigos são normalizados.
- Migration idempotente atualiza planos existentes sem duplicar entradas.

## Recursos para escritórios jurídicos — 2026-08-18

- Departamentos, centros de custo, fornecedores e contratos vinculados ao tenant.
- Custos para custas INPI, honorários e despesas, com múltiplos responsáveis e idempotência.
- Relatório executivo, exportação segura, webhook HMAC, logs de tentativas e reprocessamento.
- Testes de permissão, tenant, duplicidade, assinatura de webhook e exposição mínima na exportação.

## Jornada comercial — 2026-08-18

- Landing page de busca gratuita em `/buscar-gratuita` com captura automática de leads e origem `landing`.
- Dashboard comercial ampliado com origens, tempos médios, taxas de pagamento/protocolo e atrasos.
- Fluxo existente de Kanban, qualificação, responsável, próxima ação, proposta versionada, aceite,
  pagamento, documentos, SLA e protocolo mantido integrado.

## Portfólio completo de propriedade intelectual — 2026-08-18

- Ativos para marcas, patentes, modelos de utilidade, desenhos industriais, contratos,
  cessões, licenças e franquias.
- Vínculos de titulares, inventores, procuradores, processos e clientes do portal.
- Documentos versionados com hash SHA-256 e auditoria de cada versão.
- Isolamento por organização e endpoint do portal limitado ao cliente autenticado.

## Agenda juridica centralizada — 2026-08-18

- Lista, kanban e calendario de prazos de propriedade intelectual.
- Eventos para RPI, oposicao, exigencia, manifestacao, recurso, pagamento, deferimento,
  concessao, renovacao, decenio e vencimentos internos.
- Filtros por responsavel, cliente/processo, prioridade, evento e periodo.
- Alertas de atraso e proximidade, com escalonamento mantido pelo motor juridico.
- Nenhuma exclusao fisica de prazo: alteracoes e encerramentos permanecem auditados na timeline.

## Busca avancada de anterioridade — 2026-08-18

- Consulta por estrategias exata, radical, prefixo, sufixo, fonetica e similaridade textual.
- Filtros por Nice, titular, situacao, periodo, apresentacao e Classificacao de Viena.
- Projetos de busca salvos, isolados por tenant, com reprocessamento e evidencias por resultado.
- Pesos configuraveis no score combinado, mantendo revisao humana obrigatoria.
- Dataset e gate de benchmark preservam Recall/Precision@5/@10/@20, MRR, p50/p95/p99 e falsos negativos criticos.

## RC2 — Fase 13 — 2026-08-17

- Documentação consolidada de instalação, ambiente, migrations, deploy, rollback, operação, suporte, permissões e indicadores.
- Checklist final de aceite e evidências do Release Candidate.
- README atualizado com comandos de instalação, atualização e execução.
- Critérios de segurança, RPI, CRM, propostas, documentos, financeiro, busca, interface, observabilidade e testes formalizados.

## RC1 — 2026-08-17

- Confiabilidade da RPI: validação de arquivos corrompidos/parciais, checksum e idempotência.
- CRM: cadências idempotentes, timeline e propostas vinculadas a leads.
- Propostas: aceite, SLA de 24 horas, pagamento, protocolo e auditoria.
- Portal do cliente: login isolado, documentos, mensagens, arquivos, preferências e revogação.
- Documentos: assinatura, hash, versão, validade e trilha de auditoria.
- Busca visual: score explicável, OCR opcional, Viena e gates de benchmark.
- Vigilância RPI: preferências, colidências, revisão humana e notificações no portal.
- Financeiro: catálogo de serviços, contratações vinculadas, parcelas e idempotência.

Este RC não deve ser promovido enquanto houver gates pendentes no checklist.

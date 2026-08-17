# Fase 0 — Diagnóstico e preparação

## Objetivo

Estabelecer uma linha de base técnica e operacional antes de novas mudanças de produto. Este documento é a referência da homologação e deve ser atualizado a cada release.

## Inventário dos módulos

| Módulo | Rotas/tela principal | Estado atual |
|---|---|---|
| Autenticação e SaaS | `/login`, `/admin/saas` | Implementado; sessões, perfis, organizações e permissões |
| Visão geral | `/admin` | Implementado |
| Leads e CRM | `/admin/leads`, `/admin/crm` | Implementado; funil, contatos, cadências, timeline e propostas |
| Central de análise | `/admin/analises/{id}` | Implementado; validação, risco e registrabilidade |
| Busca de marcas | `/admin/consulta` | Implementado; score nominativo explicável |
| Busca figurativa | `/admin/figurativa` | Viena implementado; imagem/OCR em evolução |
| Processos monitorados | `/admin/processos-monitorados` | Implementado; carteira e situações do INPI |
| Consulta RPI | `/admin/configuracao/consulta-rpi` | Implementado; lista paginada, filtro e índice dedicado |
| Produção/auditoria | `/admin/producao` | Implementado; eventos, métricas e histórico |
| Financeiro | `/admin/financeiro/*` | Implementado como módulo operacional |
| Jurídico | `/admin/juridico` | Implementado |
| Confiabilidade | `/admin/confiabilidade` | Implementado; saúde, alertas e identidade |

## Mapa de acesso e tenancy

- Usuários pertencem a uma `organizacao_id`; consultas administrativas devem filtrar pelo tenant autenticado.
- `superadmin` possui operações globais controladas; o perfil `tech` acessa observabilidade.
- Permissões principais: `dashboard.view`, `leads.view/manage/delete/export`, `validation.view/review`, `risk.view/review`, `learning.view/manage`, `portfolio.view/manage`, `finance.view/manage/approve/export`, `production.view`, `rpi.view` e `users.view`.
- A revisão da Fase 1 deve testar IDs cruzados entre dois tenants para todas as tabelas expostas pela API.

## Fluxos críticos a reproduzir em homologação

1. Login, troca de senha, logout e expiração de sessão.
2. Criar lead → pesquisa → análise → proposta → aceite → checklist de protocolo.
3. Importar uma RPI, reprocessá-la e consultar seus registros sem duplicidade.
4. Monitorar processo, alterar situação e registrar próxima ação.
5. Criar contato, registrar histórico e executar cadência.
6. Criar lançamento financeiro, parcela, pagamento e evento de auditoria.
7. Acessar a mesma URL com usuário de outro tenant e receber `404/403` conforme a política.

## Indicadores de sucesso da linha de base

- API `/health` e `/health/rpi`: `200` em ambiente saudável.
- Testes automatizados críticos: 100% aprovados.
- Nenhum acesso por ID cruzado entre tenants.
- Consulta RPI paginada sem `COUNT` global para a primeira página.
- Importação RPI idempotente e com checksum/anomalia registrados.
- Proposta aceita com auditoria e prazo operacional de 24 horas úteis rastreável.
- p95 de endpoints administrativos definido antes do benchmark de carga.

## Homologação, backup e rollback

- Homologação local: `docker compose up -d --build`, com PostgreSQL, Redis, Mailpit, API, worker e sincronizador RPI.
- Validação de saúde: `docker compose ps` e `Invoke-WebRequest http://localhost:8000/health`.
- Migrações: executadas pelo serviço `migrate`; não aplicar mudanças diretamente no banco.
- Backup: usar o procedimento em `docs/operacao-recuperacao-desastre.md`, validar restauração em banco separado e registrar checksum do arquivo.
- Rollback: manter a imagem anterior, reverter a versão da aplicação e executar somente downgrades de migração previamente testados; nunca apagar dados de produção como estratégia de rollback.

## Dataset inicial para benchmark

O arquivo [`docs/benchmark/dataset-v0.json`](benchmark/dataset-v0.json) é um contrato inicial de casos. Ele ainda está marcado como `pendente_revisao_especialista`; nenhum score deve ser usado como decisão jurídica até a revisão dos casos, relevantes e falsos negativos críticos.

## Critério de conclusão da Fase 0

A Fase 0 está concluída quando este inventário estiver revisado, o ambiente de homologação reproduzir os sete fluxos críticos, o backup for restaurado com sucesso e o dataset receber aprovação de um especialista.

## Evidência de execução local — 2026-08-17

- Homologação Docker: API, PostgreSQL, Redis, Mailpit, worker e sincronizador RPI ativos; API `/health` respondeu `200`.
- Suíte automatizada: `354 passed`.
- O dataset permanece `pendente_revisao_especialista`; não pode ser usado para decisão jurídica.
- A restauração de backup e o rollback em banco descartável ainda exigem evidência operacional anexada antes de marcar o gate como aprovado.

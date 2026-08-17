# Diagnóstico de evolução do Zé Registra

Data: 17/08/2026  
Escopo: diagnóstico inicial antes da implementação do prompt de evolução operacional.

## Resultado executivo

O sistema já possui uma base ampla de plataforma SaaS, com FastAPI, SQLAlchemy/Alembic, PostgreSQL/Redis preparados, autenticação, isolamento por organização, CRM, RPI, processos monitorados, propostas, portal do cliente, documentos, financeiro, busca e observabilidade.

O repositório está na branch `agent/rc1-consolidacao`, sem alterações locais pendentes, e a suíte atual foi executada com **354 testes aprovados** e um aviso de depreciação do `TestClient`/`httpx`.

O principal trabalho restante não é criar módulos isolados, mas consolidar os fluxos ponta a ponta, fechar lacunas operacionais e validar as integrações que dependem do INPI, provedores externos e operação humana.

## Inventário técnico

### Aplicação e rotas

- Aplicação principal em `app/main.py` com routers para processos, leads, CRM, propostas, RPI, consulta, carteira, jurídico, financeiro, portal do cliente, SaaS, autenticação, observabilidade, busca figurativa e vigilância.
- Interfaces administrativas em `app/web/`, incluindo CRM, consulta RPI, confiabilidade, financeiro, figurativa, análise e aprendizado.
- Worker e fila presentes em `app/worker.py` e `app/queueing.py`.
- Observabilidade e contexto de requisição presentes em `app/observability.py` e `app/request_context.py`.

### Domínio e persistência

Os modelos já cobrem organizações, usuários operacionais, sessões, processos, titulares, movimentações, importações RPI, processos monitorados, empresas, contatos, leads, cadências, financeiro, propostas, portal do cliente, documentos, assinaturas, vigilância, colidências, prazos jurídicos, eventos e auditoria.

Há migrations incrementais para SaaS/RLS, RPI, CRM, propostas/SLA, portal, documentos, busca, registrabilidade, colidências e financeiro.

### Testes

Existem testes unitários e de integração para autenticação, permissões, tenancy/RLS, RPI, processos, CRM, propostas, portal, documentos, financeiro, busca, aprendizado, auditoria, health checks e fluxos administrativos.

Baseline executado em 17/08/2026:

```text
354 passed, 1 warning in 34.76s
```

## Mapa de lacunas prioritárias

### Alta prioridade

1. Protocolo de 24 horas ainda depende de operação e integração específica; o fluxo precisa comprovar aceite, pagamento, documentos, início do SLA, protocolo oficial e atraso.
2. Portal específico do cliente precisa ser validado ponta a ponta em produção, incluindo geração, revogação e recuperação de acesso.
3. Integração financeira com gateway, webhooks idempotentes e conciliação ainda é uma dependência externa.
4. Alertas transacionais de processos/RPI por e-mail e WhatsApp ainda não constituem um fluxo completo.
5. Isolamento entre tenants precisa permanecer coberto por testes recorrentes em PostgreSQL real, não apenas por testes locais.

### Média prioridade

1. Consolidar componentes visuais e corrigir regressões de encoding em todas as telas administrativas.
2. Fechar a reconciliação entre dados importados da RPI, processos oficiais e eventos jurídicos.
3. Finalizar assinatura digital verificável e versionamento de documentos em todos os fluxos obrigatórios.
4. Transformar benchmark de busca em gate obrigatório de publicação, com dataset revisado e limiar de regressão.
5. Ampliar métricas operacionais: SLA, conversão, pendências, atrasos, inadimplência, cobertura RPI e disponibilidade.

### Fora do escopo atual

- Garantia de concessão de marca.
- Decisão jurídica automática.
- Monitoramento de marketplaces e domínios.
- Gestão completa de patentes e contratos.

Esses itens não devem ser anunciados como funcionalidades disponíveis sem novo modelo de dados, fontes, regras e testes específicos.

## Riscos de implementação

- Alterações de autenticação/RLS podem bloquear login ou expor dados se forem feitas sem testes de acesso cruzado.
- Alterações de ranking podem reduzir recall jurídico; toda mudança deve passar pelo benchmark antes da publicação.
- A RPI pode ser importada parcialmente ou sofrer mudança de formato; checksum, validação e reprocessamento devem ocorrer antes da gravação definitiva.
- O SLA comercial não deve iniciar antes de pagamento e documentos obrigatórios, conforme configuração da proposta.
- Integrações externas devem possuir timeout, retry com backoff, idempotência, logs e fallback manual.

## Ordem recomendada após este diagnóstico

1. Padronização visual e correção de encoding.
2. Fechamento do fluxo de proposta, aceite, pagamento, documentos e SLA.
3. Reconciliação RPI/processos e alertas operacionais.
4. Validação do portal e isolamento entre tenants.
5. Gateway financeiro e webhooks idempotentes.
6. Assinatura digital verificável.
7. Benchmark e busca visual com gate de recall.
8. Hardening, testes end-to-end, documentação e novo Release Candidate.

## Critério para iniciar a implementação

Cada etapa deverá ser executada em commit separado, com migration reversível quando necessário, testes específicos, atualização da documentação e relatório de riscos. Nenhuma alteração estrutural deve ser aplicada sem preservar os dados atuais e sem plano de rollback.

# Monitoramento de erro e uso | 16/11/2026

## Pedido e escopo

Pedido do dono (WhatsApp, 16/11/2026): um dashboard de acompanhamento de
erro e dificuldade de uso para o sistema central, no formato do
monitoramento que ele já usa no outro produto dele. Entrega em
`feat/monitoramento-erros-uso`, sem merge, deploy ou migração.

Nenhuma tabela ou migração nova: todos os números saem de consultas sobre
`eventos_operacionais` e `eventos_auditoria`, que o middleware
(`app/observability.py`) e as auditorias de negócio já gravam por
requisição. `alembic check` não deve acusar deriva.

## O que foi criado

- `app/api/monitoramento.py` — router `/v1/admin/monitoramento` com cinco
  endpoints (`resumo`, `serie`, `uso-organizacoes`, `top-erros`,
  `dificuldade`), todos com parâmetro `dias` (1-90, padrão 30). Mesmo
  recorte de acesso da Observabilidade: `production.view` + perfil
  administrador/tech (ou superadmin), porque os números cruzam
  organizações. Datas de negócio em America/Sao_Paulo (buckets "por dia" e
  a janela do período).
- `app/web/admin-monitoramento.html` + `app/web/static/admin-monitoramento.js`
  — página `/admin/monitoramento`: cartões de resumo, gráfico de ações e
  erros por dia (SVG puro de `observabilidade-graficos.js`, sem dependência
  nova), uso por organização, organizações ativas sem uso, erros mais
  frequentes e os quatro sinais de dificuldade de uso. Seletor de período
  (7/30/90 dias) e atualização automática a cada 60 s.
- Item "Monitoramento de uso" no menu admin (Configuração), mesma
  permissão e perfis da Observabilidade.
- `tests/test_monitoramento.py` — agregações, preenchimento de dias
  zerados, divisão por zero, controle de acesso (403 para operador) e a
  página servida.

## O que cada número significa

- **Ações / usuários / organizações ativas**: contagens e distintos de
  `eventos_auditoria` no período (ações no painel administrativo).
- **Organizações sem uso**: organizações com `status = 'ativa'` sem
  nenhuma ação no período — sinal de churn silencioso.
- **Erros**: `eventos_operacionais` com `sucesso = false` (5xx e 4xx que
  o middleware marca como falha), com taxa sobre as requisições.
- **Top erros**: agrupado por `codigo_erro` + operação + status.

## "Dificuldade de uso": premissa assumida

O produto não captura eventos de abandono de tela, funil ou cliques —
não havia base para medir dificuldade diretamente. Implementado como
proxies calculados sobre o que já é registrado, descritos na própria
tela:

1. **Tentativas frustradas (4xx)** por operação — dado recusado, rota
   errada ou permissão negada;
2. **Usuários com mais falhas** acumuladas no período;
3. **Recursos travados** — o mesmo usuário falhou 3+ vezes no mesmo
   recurso (sinal mais forte de travamento);
4. **Operações mais lentas** (p95, mínimo de 5 requisições) — lentidão
   também faz desistir.

Se o dono quiser medir abandono de telas de verdade (ex.: pesquisa
iniciada e não concluída), é preciso uma captura nova no frontend +
tabela de eventos — fora deste escopo.

## Verificação

- `ruff check app tests migrations --ignore E501` limpo.
- `pytest tests/test_monitoramento.py tests/test_web.py`: 80 aprovados.
- SQL das consultas compilado no dialeto PostgreSQL e conferido; sem
  Postgres local para execução (CI cobre com pg16).
- Página renderizada em Chromium com APIs simuladas: layout, gráficos,
  tabelas e item de menu conferidos em captura de tela (dados fictícios).
- Sintaxe JS validada com `node --check`.

## Limitações

- `eventos_operacionais` não tem `organizacao_id`, então erros não são
  atribuídos por organização (uso e falhas sim, via auditoria).
- Produção, volume real e desempenho das consultas não foram homologados;
  as tabelas têm índice em `criado_em`, e as agregações usam os índices
  existentes.

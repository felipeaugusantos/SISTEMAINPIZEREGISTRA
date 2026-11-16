# Centro de Operações: correções e limites de validação

Base: `d474bbed1cd993a9dc0ed8e8a09364fe5d4c84ee`. Escopo funcional: Leads, Consulta/Pesquisa e Central de Análise. Dashboard geral e site institucional não foram modificados. Componentes compartilhados foram apenas consultados.

## Correções

- Consulta: `popstate` recarrega o deep link autenticado ao navegar com Voltar/Avançar, evitando URL de uma pesquisa com conteúdo de outra. O recarregamento usa as rotas existentes e pode perder preenchimento ainda não enviado.
- Consulta: sequência de requisições impede relatório ou erro antigo de substituir a resposta mais recente. Explicações de IA de outra pesquisa são ignoradas.
- Consulta: revisão da explicação usa o tratamento HTTP da API. Recusas mantêm a explicação, mostram o erro e liberam nova tentativa.
- Consulta: submissão fica bloqueada até concluir, impedindo duplo envio enquanto a requisição está em andamento. Não substitui idempotência no servidor.
- Leads: arquivamento/restauração bloqueiam cliques repetidos, tratam rejeições de rede, exibem erros, liberam nova tentativa e atualizam lista e resumo após sucesso.
- Análise: checklist abre o `details` de destino antes de seguir a âncora.
- Templates: incrementadas versões dos três scripts para invalidar cache.

A restauração da listagem pelo breadcrumb da Análise já existia. Foi inspecionada, sem modificação nem declaração de nova validação no navegador.

## Verificação local

425 testes de backend aprovados, em duas execuções:

```sh
uv run pytest tests/test_leads.py tests/test_admin_consulta.py tests/test_research_deletion.py tests/test_registrability.py tests/test_registrability_agent.py tests/test_web.py -q
uv run pytest tests/test_admin_analysis.py tests/test_analysis_presentation.py tests/test_consolidated_analysis.py tests/test_trademark_risk.py tests/test_prospeccao.py tests/test_prospeccao_triagem.py tests/test_prospeccao_reconciliacao.py tests/test_prospeccao_score.py -q
node --test tests/frontend/operations-navigation.test.cjs
uv run ruff check app tests migrations --ignore E501
node --check app/web/static/admin-consulta.js
node --check app/web/static/admin-leads.js
node --check app/web/static/admin-analise.js
git diff --check
```

10 testes JavaScript novos aprovados. Executam funções e eventos do código de produção em Node/VM com DOM e APIs simulados; não comprovam layout, integração completa ou navegação real do navegador. Backend usa fixtures do projeto. Aviso de depreciação Starlette/httpx, sem falha.

As suítes Playwright `operations-lists.test.cjs` e `lead-dialog.test.cjs` foram tentadas: 15 testes bloqueados no lançamento do Chromium (`socket() failed: Operation not permitted`). O download alternativo também falhou com arquivo inválido. Não há capturas reais desta rodada. Não declarar todas as ações ou responsividade como aprovadas.

## Homologação pendente

Usar organização e dados descartáveis. Validar 1440 e 390 px, teclado e perfis de leitura/gestão. Conferir listagem com filtros e retorno da análise por breadcrumb/navegador, novas consultas com múltiplas marcas/classes, Voltar/Avançar, recarga e URLs diretas. Simular erro HTTP e rede em revisão, arquivamento/restauração. Exercitar salvar, editar, registrar contato, excluir/solicitar/aprovar, mover, exportar e cancelar nos diálogos reais. Testar parecer, workflow e download de relatório com conflitos de versão.

Integrações externas, processamento real das filas, dados e desempenho de produção não foram testados. Não houve migração, merge ou deploy.

O PDF de diagnóstico reúne 12 propostas com situação atual, problema, solução, benefício, prioridade, complexidade e dependências. As propostas não foram implementadas.

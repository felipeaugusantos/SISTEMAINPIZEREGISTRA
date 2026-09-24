# Achado 17.2 — diálogo compartilhado entre CRM e Leads

## Linha de base

Base local: `e5541e9` (inclui a correção de permissões do PR #141).
Esta alteração não comprova nem modifica o commit em execução no VPS.

Antes, `admin-crm.html` carregava `admin-leads.js` completo e mantinha uma
cópia oculta dos filtros, lista, métricas e paginação de Leads para satisfazer
os bindings globais. O fechamento do diálogo precisava de tratamento separado
no CRM. O diálogo compartilhava permissões e estado com a listagem.

## Responsabilidades após a extração

- `static/lead-dialog.js`: fábrica `window.createLeadDialog(root, options)`.
  Carregar o arquivo não consulta nem vincula elementos do DOM. Cada raiz tem
  uma única instância (WeakMap), evitando duplicação de listeners. As consultas
  de elementos ficam restritas à raiz. Mantém atendimento, pesquisas, propostas,
  empresa, documentos, portal, funil, guias e horas no mesmo componente, sem
  reescrever suas regras nesta etapa.
- `static/admin-leads.js`: inicialização explícita da página, filtros,
  paginação, listagem, distribuição, importação, arquivamento e resumo comercial.
- `static/admin-crm.js`: Kanban, histórico, lembretes e integração com o diálogo
  por callbacks. Não carrega a listagem de Leads.
- Ambas as páginas declaram `#lead-workspace` com os diálogos necessários.
  O CRM não contém mais filtros/lista ocultos nem o diálogo de arquivamento.

## Contrato de integração

- `openLead(id, pesquisaId?)`: abre o atendimento completo.
- `abrirRegistroAtendimento(id, pesquisaId)`: registro contextual de contato.
- `onChange()`: recarrega lista em Leads; Kanban e histórico no CRM.
- `onSummary()`: recarrega resumo em Leads; indicadores de atendimento no CRM.
- `onClose()`: atualiza Kanban e histórico no CRM.
- `setContext()`: sincroniza apenas responsáveis e capacidades de apresentação
  da listagem. Ao abrir o atendimento, permissões são novamente obtidas de
  `/v1/auth/me`, sem depender de uma consulta à listagem de Leads.

Os auxiliares de apresentação reutilizados pela tabela são exportados na
instância, não como funções globais. A autorização continua no backend;
nenhuma permissão ou contrato de API foi ampliado.

## Correção complementar

O salvamento agora só limpa notas/tags quando os campos existem. Sem
`leads.pii.view`, esses campos permanecem ausentes da tela e do PATCH.

## Verificação

Comandos reproduzíveis, após instalar as dependências do projeto:

```sh
uv run --frozen pytest tests/test_web.py tests/test_permissions.py -q
npm ci
npx playwright install chromium
npm run test:frontend
node --check app/web/static/lead-dialog.js
node --check app/web/static/admin-leads.js
node --check app/web/static/admin-crm.js
git diff --check
```

Os testes de navegador usam HTML/JS reais, APIs simuladas e dados fictícios,
sem conexão com produção. Cobrem abertura pelas duas páginas, salvamento,
reabertura, ausência de handlers duplicados, callbacks, filtro de Leads,
consulta sem gestão, presença/ausência de PII e carregamento sem DOM.
São executados na CI após a instalação do Chromium.

Limite: não substituem homologação integrada dos subsistemas (Clicksign,
uploads, e-mail e banco). Não foi realizado deploy, migration ou exclusão real.
Para rollback técnico, reverter conjuntamente os scripts e as duas páginas;
não há mudança de schema.

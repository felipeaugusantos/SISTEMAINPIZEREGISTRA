const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../app/web/static/admin-consulta.js'), 'utf8');
function element() {
  return { hidden: false, disabled: false, value: '', textContent: '', innerHTML: '', handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
    querySelector() { return this.child ||= element(); }, querySelectorAll() { return []; },
    replaceChildren() {}, append() {}, scrollIntoView() {}, reset() {}, focus() {},
    classList: { add() {}, remove() {} } };
}
async function fixture(fetcher = async () => ({ ok: true, json: async () => ({}) })) {
  const elements = new Map(), events = {}, requests = [];
  const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const context = vm.createContext({
    document: { querySelector: get, querySelectorAll: () => [], createElement: element },
    window: { addEventListener: (name, fn) => { events[name] = fn; } },
    location: { pathname: '/admin/consulta', reload() { requests.push('reload'); } },
    history: { pushState() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url === '/v1/admin/consulta/classes-nice') return { ok: true, json: async () => [] };
      return fetcher(url, options);
    },
    FormData: class { get(name) { return ({ nome: 'Teste', email: 'teste@example.com', telefone: '16999999999' })[name]; } },
    URLSearchParams, encodeURIComponent, decodeURIComponent, console,
  });
  vm.runInContext(source, context);
  await new Promise(setImmediate);
  return { context, get, events, requests, run: code => vm.runInContext(code, context) };
}
test('Pesquisa: Voltar/Avançar recarrega o deep link autenticado da URL atual', async () => {
  const f = await fixture();
  f.events.popstate();
  assert.equal(f.requests.at(-1), 'reload');
});
test('Pesquisa: resposta antiga não substitui a marca mais recente', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const f = await fixture(async url => {
    if (url.includes('/antiga/relatorio')) await pending;
    return { ok: true, json: async () => ({ marca: url.includes('/antiga/') ? 'Antiga' : 'Nova' }) };
  });
  f.run('pesquisaAtual = "nova"');
  const old = f.run('carregarRelatorio("antiga")');
  await f.run('carregarRelatorio("nova")');
  release(); await old;
  assert.equal(f.get('#res-marca').textContent, 'Nova');
  assert.equal(f.get('#consulta-status').textContent, 'Consulta concluída.');
});
test('Pesquisa: falha antiga não apaga sucesso da consulta mais recente', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const f = await fixture(async url => {
    if (url.includes('/antiga/relatorio')) { await pending; throw Error('Falha antiga'); }
    return { ok: true, json: async () => ({ marca: 'Nova' }) };
  });
  const old = f.run('carregarRelatorio("antiga")');
  await f.run('carregarRelatorio("nova")'); release(); await old;
  assert.equal(f.get('#consulta-status').textContent, 'Consulta concluída.');
});
test('Pesquisa: revisão recusada mantém explicação e libera nova tentativa', async () => {
  const f = await fixture(async url => ({ ok: !url.endsWith('/revisar'), json: async () =>
    url.endsWith('/revisar') ? { detail: 'Sem permissão' } : { id: 7, status: 'pendente', explicacao: 'Teste' } }));
  const button = element(); button.dataset = { revisarExplicacao: 'aprovada' };
  f.get('#res-explicacao-ia').querySelectorAll = () => [button];
  f.run('pesquisaAtual = "r1"');
  await f.run('renderExplicacaoIA("r1")');
  await button.handlers.click();
  assert.equal(f.get('#res-explicacao-ia').hidden, false);
  assert.equal(button.disabled, false);
  assert.equal(f.get('#consulta-status').textContent, 'Sem permissão');
});
test('Pesquisa: revisão confirmada pelo servidor oculta a explicação', async () => {
  const f = await fixture(async () => ({ ok: true, json: async () => ({ id: 7, status: 'pendente', explicacao: 'Teste' }) }));
  const button = element(); button.dataset = { revisarExplicacao: 'aprovada' };
  f.get('#res-explicacao-ia').querySelectorAll = () => [button];
  f.run('pesquisaAtual = "r1"'); await f.run('renderExplicacaoIA("r1")'); await button.handlers.click();
  assert.equal(f.get('#res-explicacao-ia').hidden, true);
});
test('Pesquisa: duplo envio produz um POST e falha libera o formulário', async () => {
  let release, posts = 0;
  const pending = new Promise(resolve => { release = resolve; });
  const f = await fixture(async (url, options) => {
    if (options?.method === 'POST') { posts++; await pending; return { ok: false, json: async () => ({ detail: 'Indisponível' }) }; }
    return { ok: true, json: async () => ({}) };
  });
  const submit = f.get('#consulta-form').handlers.submit;
  const first = submit({ preventDefault() {} }); await submit({ preventDefault() {} });
  assert.equal(posts, 1); assert.equal(f.get('#consulta-form').child.disabled, true);
  release(); await first;
  assert.equal(f.get('#consulta-form').child.disabled, false);
  assert.equal(f.get('#consulta-status').textContent, 'Indisponível');
});
test('Análise: checklist abre a etapa fechada antes de navegar até ela', () => {
  const analysis = fs.readFileSync(path.join(__dirname, '../../app/web/static/admin-analise.js'), 'utf8');
  const elements = new Map(); const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const target = { open: false };
  vm.runInNewContext(analysis, {
    location: { pathname: '/admin/analises/r1', search: '' }, URLSearchParams,
    document: { querySelector: get, createElement: element, getElementById: id => id === 'step-report' ? target : null },
    fetch: async () => ({ ok: false, json: async () => ({ detail: 'Indisponível' }) }),
  });
  get('#analysis-progress').handlers.click({ target: { closest: () => ({ hash: '#step-report' }) } });
  assert.equal(target.open, true);
});

for (const archive of [true, false]) test(`Leads: ${archive ? 'arquivar' : 'restaurar'} trata falha de rede e impede clique duplicado`, async () => {
  const leads = fs.readFileSync(path.join(__dirname, '../../app/web/static/admin-leads.js'), 'utf8');
  // Execute the production handler, isolated from the list's initial loading.
  const handler = leads.slice(leads.indexOf('async function changeArchive('), leads.indexOf('\ndocument.querySelector("#confirm-archive")'));
  let release, calls = 0, closed = false; const messages = [];
  const pending = new Promise(resolve => { release = resolve; });
  const context = vm.createContext({
    fetch: async () => { calls++; await pending; throw Error('Sem conexão'); },
    responsePayload: async response => response,
    archiveDialog: { close() { closed = true; } },
    showMessage: (...args) => messages.push(args), loadLeads: async () => {}, loadCrmSummary: async () => {},
  });
  vm.runInContext(handler, context);
  const button = { disabled: false };
  const first = context.changeArchive(button, 1, archive);
  await context.changeArchive(button, 1, archive);
  assert.equal(calls, 1); assert.equal(button.disabled, true);
  release(); await first;
  assert.equal(button.disabled, false); assert.equal(closed, false);
  assert.deepEqual(messages, [['Sem conexão', 'error']]);
});

test('Leads: arquivamento confirmado fecha diálogo e atualiza lista e resumo', async () => {
  const leads = fs.readFileSync(path.join(__dirname, '../../app/web/static/admin-leads.js'), 'utf8');
  const handler = leads.slice(leads.indexOf('async function changeArchive('), leads.indexOf('\ndocument.querySelector("#confirm-archive")'));
  const calls = []; const context = vm.createContext({
    fetch: async (url, options) => { calls.push([url, options.method]); return { ok: true }; },
    responsePayload: async r => r, archiveDialog: { close: () => calls.push('closed') },
    showMessage: () => {}, loadLeads: async () => calls.push('list'), loadCrmSummary: async () => calls.push('summary'),
  });
  vm.runInContext(handler, context); await context.changeArchive({ disabled: false }, 7, true);
  assert.deepEqual(calls, [['/v1/admin/leads/7', 'DELETE'], 'closed', 'list', 'summary']);
});

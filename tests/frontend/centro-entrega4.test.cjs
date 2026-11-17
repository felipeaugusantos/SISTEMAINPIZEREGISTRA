// Entrega 4 (plano de correções 17/11/2026): R10 proteção de preenchimento
// na consulta. R07 (tornar a leitura de mensagens explícita) NÃO integra esta
// entrega: muda a regra atual de leitura, que o usuário mandou preservar sem
// definição específica. Este teste comprova a preservação do comportamento
// atual: mensagem do cliente marcada como lida no carregamento da ficha.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');

async function browserTest(fn) { const browser = await chromium.launch(); try { await fn(browser); } finally { await browser.close(); } }

function rotas(page, handlers = []) {
  page.setDefaultTimeout(8000);
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  return page.route('**/*', async route => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    requests.push({ path: p, method: req.method(), body: req.postData() });
    for (const [match, handler] of handlers) {
      if ((typeof match === 'string' && p === match) || (match instanceof RegExp && match.test(p))) return handler(route, req, u);
    }
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) {
      const html = read('admin-leads.html').replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '');
      return route.fulfill({ contentType: 'text/html', body: html });
    }
    if (p === '/v1/auth/me') return route.fulfill({ json: { id: 7, nome: 'Teste', perfil: 'admin', permissoes: ['leads.view', 'leads.manage'], organizacao: { id: 1 }, mfa_ativo: false } });
    await route.fulfill({ json: { itens: [], contatos: [], total: 0 } });
  }).then(() => ({ errors, requests }));
}

const portalHandlers = () => [
  ['/v1/admin/leads/42', route => route.fulfill({ json: { id: 42, nome: 'Ana Souza', pesquisas: [], status: 'novo', tags: [], criado_em: '2026-01-01T12:00:00Z', responsavel_id: 7 } })],
  ['/v1/admin/leads/42/portal-acesso', route => route.fulfill({ json: { existe: true, portal: '/portal' } })],
  ['/v1/admin/leads/42/portal-mensagens', (route, req) => {
    if (req.method() === 'GET') return route.fulfill({ json: { mensagens: [
      { id: 1, autor_tipo: 'cliente', mensagem: 'Enviei os documentos, podem conferir?', criado_em: '2026-11-15T10:00:00Z', lida_em: null },
      { id: 2, autor_tipo: 'operador', mensagem: 'Olá! Como podemos ajudar?', criado_em: '2026-11-14T09:00:00Z', lida_em: null },
    ] } });
    return route.fulfill({ json: {} });
  }],
  [/\/v1\/admin\/leads$/, route => route.fulfill({ json: { itens: [{ id: 42, nome: 'Ana Souza', status: 'novo', tags: [], criado_em: '2026-01-01T12:00:00Z', responsavel_id: 7 }], total: 1, total_global: 1, pesquisas_total: 0, por_status: { novo: 1 }, acoes: { gerenciar: true } } })],
];

test('R07 preservado: abrir a ficha com mensagem não lida do cliente registra a leitura (regra atual mantida)', () => browserTest(async browser => {
  const page = await browser.newPage();
  const { errors, requests } = await rotas(page, portalHandlers());
  await page.goto('http://operations.test/admin/leads');
  await page.locator('.view-lead').first().click();
  await page.locator('.lead-portal-messages .lead-contact-entry').first().waitFor();
  await page.waitForFunction(() => true);
  assert.ok(requests.some(r => r.path.endsWith('/portal-mensagens/ler') && r.method === 'POST'),
    'regra atual: carregar a ficha com mensagem não lida marca a leitura');
  assert.deepEqual(errors, []);
}));

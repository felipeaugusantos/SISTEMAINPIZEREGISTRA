const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');
const research = id => ({ id: `r${id}`, marca: `Marca ${id}`, criado_em: '2026-11-15T12:00:00Z', risco_nivel: 'baixo' });
const lead = id => ({ id, nome: `Contato ${id}`, empresa: `Empresa ${id}`, status: 'novo', pesquisas: [research(id)], total_pesquisas: 1,
  criado_em: '2026-11-15T12:00:00Z', responsavel_nome: 'Operador Teste', proxima_acao_em: '2026-01-01T12:00:00Z', ultimo_contato_em: '2026-11-15T12:00:00Z', tags: [] });
async function fixture(page, { userId = 7, orgId = 1, denied = false } = {}) {
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    requests.push({ path: p, query: u.searchParams, method: req.method(), body: req.postData() });
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) {
      const file = p.startsWith('/admin/analises/') ? 'admin-analise.html' : p.startsWith('/admin/consulta') ? 'admin-consulta.html' : 'admin-leads.html';
      return route.fulfill({ contentType: 'text/html', body: read(file).replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '') });
    }
    let data = { itens: [], contatos: [], total: 0, deslocamento: 0, tem_mais: false };
    if (p === '/v1/auth/me') data = { id: userId, nome: 'Teste', perfil: 'operador', permissoes: ['leads.view', 'leads.manage'], organizacao: { id: orgId } };
    if (p === '/v1/admin/leads') {
      const size = Number(u.searchParams.get('limite')) || 50, offset = Number(u.searchParams.get('deslocamento')) || 0;
      data = { itens: Array.from({ length: Math.min(size, 80-offset) }, (_, i) => lead(i+offset+1)), total: 80, total_global: 80,
        pesquisas_total: 80, por_status: {}, acoes: { gerenciar: true, arquivar: true }, tem_mais: offset+size < 80 };
    }
    if (/\/v1\/admin\/leads\/\d+$/.test(p)) data = lead(Number(p.split('/').pop()));
    if (p.endsWith('/contatos')) data = { contatos: [{ resultado: 'Contato de teste concluído', canal: 'telefone', criado_em: '2026-11-15T12:00:00Z' }], total: 1 };
    if (p.includes('/score') || p.includes('/sugestao') || p.includes('/qualificacao')) return route.fulfill({ status: 503, json: { detail: 'Opcional indisponível' } });
    if (p.includes('/analises/')) data = { pesquisa: { id: 'r60', marca: 'Marca 60' }, permissoes: {}, relatorio_completo: {}, workflow: { state: 'PENDING_REVIEW', history: [] }, usuario: {} };
    if (p.endsWith('/mover-lead')) {
      if (denied) return route.fulfill({ status: 403, json: { detail: 'Operação não autorizada' } });
      data = { lead_destino_nome: 'Contato 2' };
    }
    if (p.endsWith('/classes-nice')) data = Array.from({ length: 45 }, (_, i) => ({ codigo: String(i+1), titulo: i === 29 ? 'Café e farinhas' : `Descrição classe ${i+1}` }));
    if (p === '/v1/admin/consulta') data = { itens: [{ id: 'r1', marca: 'Marca teste', relatorio_url: '/admin/consulta/r1' }], lead_id: 1 };
    if (p.endsWith('/relatorio')) data = { marca: p.includes('/r2/') ? 'Marca 2' : 'Marca teste', total: 1, classes_atividade: [] };
    if (p.endsWith('/contexto')) data = { lead_id: 1, pode_excluir: false };
    await route.fulfill({ json: data });
  });
  return { errors, requests };
}
async function browserTest(fn) { const browser = await chromium.launch(); try { await fn(browser); } finally { await browser.close(); } }
for (const width of [1440, 390]) test(`Leads ${width}: retorno mantém linha, filtros e rolagem sem destaque persistente`, { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width, height: 900 } }); const f = await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas'); await page.locator('#leads-list tr').first().waitFor();
  await page.locator('#lead-next').click(); await page.waitForFunction(() => document.querySelector('#lead-page-number').value === '2');
  const link = page.locator('tr[data-research-id="r60"] a[href*="analises"]'); await link.scrollIntoViewIfNeeded();
  const scroll = await page.evaluate(() => scrollY); await link.click(); await page.locator('.analysis-breadcrumb a').click();
  await page.locator('.lead-returned').waitFor(); await page.waitForFunction(y => Math.abs(scrollY-y) < 4, scroll);
  assert.equal(await page.locator('#lead-page-number').inputValue(), '2');
  await page.keyboard.press('Tab'); assert.equal(await page.locator('.lead-returned').count(), 0);
  assert.deepEqual(f.errors, []);
}));
test('M01: criar, aplicar, renomear, excluir e isolar visões sem persistir busca', { timeout: 30000 }, async () => browserTest(async browser => {
  const context = await browser.newContext(); const page = await context.newPage(); const f = await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas'); await page.locator('#leads-list tr').first().waitFor();
  await page.locator('#lead-search').fill('cliente@example.com'); await page.locator('#lead-status-filter').selectOption('novo');
  await page.locator('[data-view-name]').fill('Novos'); await page.locator('[data-view-action=save]').click();
  await page.waitForFunction(() => document.querySelector('[data-views-status]').textContent.includes('salva'));
  const stored = await page.evaluate(() => localStorage.getItem('zr_lead_views:v1:1:7'));
  assert.ok(stored.includes('novo')); assert.ok(!stored.includes('cliente@example.com'));
  await page.locator('#clear-lead-filters').click(); await page.locator('[data-view-action=apply]').click();
  assert.equal(await page.locator('#lead-status-filter').inputValue(), 'novo'); assert.equal(await page.locator('#lead-search').inputValue(), '');
  await page.locator('#lead-active-filters button').first().click(); assert.equal(await page.locator('#lead-status-filter').inputValue(), '');
  await page.locator('[data-view-name]').fill('Minha rotina'); await page.locator('[data-view-action=rename]').click();
  assert.equal(await page.locator('[data-views-list] option:checked').innerText(), 'Minha rotina');
  const other = await context.newPage(); await fixture(other, { userId: 8 }); await other.goto('http://operations.test/admin/pesquisas');
  await other.locator('#leads-list tr').first().waitFor(); assert.equal(await other.locator('[data-views-list] option').count(), 1);
  await page.locator('[data-view-action=delete]').click(); assert.equal(await page.locator('[data-views-list] option').count(), 1); assert.deepEqual(f.errors, []);
}));
for (const denied of [false, true]) test(`M02: busca, seleção explícita, confirmação e ${denied ? 'recusa' : 'movimentação'}`, { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage(); const f = await fixture(page, { denied }); page.on('dialog', d => d.accept());
  await page.goto('http://operations.test/admin/pesquisas'); await page.locator('.view-lead').first().click();
  await page.locator('#lead-dialog [data-tab=atendimento]').click(); await page.locator('.move-research').first().click();
  await page.locator('#research-move-form button[type=submit]').click();
  assert.equal(f.requests.filter(r => r.path.endsWith('/mover-lead')).length, 0);
  await page.locator('#research-move-search').fill('Contato'); await page.locator('#research-move-results button').nth(1).click();
  await page.locator('#research-move-form button[type=submit]').click();
  if (denied) { await page.waitForFunction(() => document.querySelector('#research-move-message').textContent.includes('não autorizada')); assert.equal(await page.locator('#research-move-dialog').evaluate(el => el.open), true); }
  else await page.waitForFunction(() => !document.querySelector('#research-move-dialog').open);
  const post = f.requests.find(r => r.path.endsWith('/mover-lead'));
  assert.equal(JSON.parse(post.body).lead_id_destino, 2); assert.deepEqual(f.errors, []);
}));
test('M03/M09: resumo real, abas por teclado, diálogo e foco de retorno', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage(); const f = await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas'); const opener = page.locator('.view-lead').first(); await opener.click();
  await page.locator('.lead-operational-summary').waitFor();
  assert.match(await page.locator('.lead-operational-summary').innerText(), /Operador Teste/);
  await page.waitForFunction(() => document.querySelector('[data-last-contact-summary]').textContent.includes('concluído'));
  assert.match(await page.locator('.lead-operational-summary').innerText(), /Retorno atrasado/);
  const first = page.locator('#lead-dialog .lead-tab').first(); await first.focus(); await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#lead-dialog .lead-tab[aria-selected=true]').getAttribute('data-tab'), 'atendimento');
  await page.keyboard.press('Escape'); assert.equal(await opener.evaluate(el => el === document.activeElement), true); assert.deepEqual(f.errors, []);
}));
for (const width of [1440, 390]) test(`M04 ${width}: 45 classes, busca, teclado e payload preservado`, { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width, height: 900 } }); const f = await fixture(page);
  await page.goto('http://operations.test/admin/consulta'); await page.locator('.nice-options input').first().waitFor();
  assert.equal(await page.locator('.nice-options input').count(), 45);
  await page.locator('.nice-selector input[type=search]').fill('cafe'); assert.equal(await page.locator('.nice-options input').count(), 1);
  await page.locator('.nice-options input').check(); await page.locator('.nice-selector input[type=search]').fill('45'); await page.locator('.nice-options input').check();
  assert.equal(await page.locator('[data-field=classes_nice]').evaluate(el => [...el.selectedOptions].map(o => o.value).join(',')), '30,45');
  await page.locator('[data-field=marca]').fill('Marca teste'); await page.locator('[name=nome]').fill('Contato Teste'); await page.locator('[name=email]').fill('teste@example.com');
  await page.locator('#consulta-form button[type=submit]').click(); await page.waitForFunction(() => location.pathname.endsWith('/r1'));
  const post = f.requests.find(r => r.path === '/v1/admin/consulta' && r.method === 'POST'); assert.deepEqual(JSON.parse(post.body).marcas[0].classes_nice, ['30','45']);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (process.env.OPERATIONS_SCREENSHOTS) { fs.mkdirSync(process.env.OPERATIONS_SCREENSHOTS, { recursive: true }); await page.screenshot({ path: path.join(process.env.OPERATIONS_SCREENSHOTS, `consulta-${width}.png`), fullPage: true }); }
  assert.deepEqual(f.errors, []);
}));
// No browser required for the privacy boundary and canonical class matching.
test('M01: whitelist remove texto livre e chaves de usuário/organização são distintas', () => {
  const context = { window: {} }; vm.runInNewContext(read('static/operations-views.js'), context);
  const api = context.window.ZeLeadViews, value = api.sanitize({ fields: { 'lead-search': 'Cliente privado', 'lead-status-filter': 'novo' }, secret: 'privado' });
  assert.ok(!JSON.stringify(value).includes('privado')); assert.ok(!JSON.stringify(value).includes('lead-search'));
  assert.notEqual(api.scope({ id: 1, organizacao: { id: 1 } }), api.scope({ id: 1, organizacao: { id: 2 } }));
  assert.equal(api.scope({ id: 1 }), null);
});

test('retorno da Análise reabre o mesmo diálogo e aba sem deslocar a lista', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage(); const f = await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas'); await page.locator('.view-lead').first().click();
  await page.locator('#lead-dialog [data-tab=atendimento]').click();
  const content = page.locator('#lead-dialog-content');
  const link = page.locator('#lead-dialog a[href*="/admin/analises/"]').first(); await link.scrollIntoViewIfNeeded();
  const before = await content.evaluate(el => el.scrollTop); await link.click();
  await page.locator('.analysis-breadcrumb a').click();
  await page.waitForFunction(() => document.querySelector('#lead-dialog').open && document.querySelector('.lead-tab.active')?.dataset.tab === 'atendimento');
  await page.waitForFunction(y => Math.abs(document.querySelector('#lead-dialog-content').scrollTop-y) < 4, before);
  assert.equal(await page.locator('.lead-returned').count(), 0); assert.deepEqual(f.errors, []);
}));
test('M01: filtros aplicados sobrevivem à recarga sem token de retorno antigo', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage(); await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas'); await page.locator('#leads-list tr').first().waitFor();
  await page.locator('#lead-status-filter').selectOption('qualificado');
  const filtered = page.waitForResponse(r => r.url().includes('status=qualificado'));
  await page.locator('#lead-filters button[type=submit]').click();
  await filtered;
  await page.reload(); await page.locator('#leads-list tr').first().waitFor();
  assert.equal(await page.locator('#lead-status-filter').inputValue(), 'qualificado');
}));
test('M04: teclado, remover, limpar e seleção vazia preservam contrato', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage(); await fixture(page); await page.goto('http://operations.test/admin/consulta');
  const first = page.locator('.nice-options input').first(); await first.waitFor(); await first.focus(); await page.keyboard.press('Space');
  assert.equal(await page.locator('[data-field=classes_nice]').evaluate(el => el.selectedOptions.length), 1);
  await page.locator('.nice-chosen button').click(); assert.equal(await page.locator('[data-field=classes_nice]').evaluate(el => el.selectedOptions.length), 0);
  await first.check(); await page.locator('.nice-selector').getByRole('button', { name: 'Limpar classes', exact: true }).click();
  assert.equal(await page.locator('[data-field=classes_nice]').evaluate(el => el.selectedOptions.length), 0);
  await page.locator('.nice-selector input[type=search]').fill('não existe termo'); assert.equal(await page.locator('.nice-options input').count(), 0);
}));
test('M09 Análise: checklist abre etapa e ajuda devolve foco ao fechar', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage(); const f = await fixture(page); await page.goto('http://operations.test/admin/analises/r60');
  await page.locator('#analysis-progress a[href="#step-report"]').click(); assert.equal(await page.locator('#step-report').evaluate(el => el.open), true);
  await page.locator('#analysis-help-open').click(); await page.keyboard.press('Escape');
  assert.equal(await page.locator('#analysis-help-open').evaluate(el => el === document.activeElement), true); assert.deepEqual(f.errors, []);
}));

// Entrega 1 (plano de correções 17/11/2026): R01 responsividade, R02
// destino dos atalhos de WhatsApp, R03 avisos compactos no Centro,
// R04 fila de exclusões recolhida, R05 clareza de filtros e contagens.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');

const TELEFONES = {
  1: '16982050534',          // nacional 11 dígitos -> prefixa 55
  2: '(16) 3205-0534',       // nacional 10 dígitos -> prefixa 55
  3: '+55 16 98205-0534',    // já internacional BR -> mantém
  4: '+1 415 555 0100',      // internacional -> mantém, sem 55
  5: '1234',                 // ambíguo -> bloqueia
  6: '',                     // vazio -> sem atalho
};
const research = id => ({ id: `r${id}`, marca: `Marca ${id}`, criado_em: '2026-11-15T12:00:00Z', atividade: 'café', risco_nivel: 'alto', risco_pontuacao: 87 });
const lead = id => ({ id, nome: `Contato ${id}`, empresa: `Empresa ${id}`, status: 'novo', pesquisas: [research(id)], total_pesquisas: 1,
  criado_em: '2026-11-15T12:00:00Z', responsavel_nome: 'Operador Teste', proxima_acao_em: '2026-01-01T12:00:00Z', ultimo_contato_em: null,
  telefone: TELEFONES[id] || '', email: `c${id}@example.com`, tags: [] });

async function fixture(page, { shell = false, exclusoes = 0 } = {}) {
  page.setDefaultTimeout(7000);
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    requests.push({ path: p, query: u.searchParams, method: req.method(), body: req.postData() });
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) {
      const file = p.startsWith('/admin/analises/') ? 'admin-analise.html' : p.startsWith('/admin/consulta') ? 'admin-consulta.html' : 'admin-leads.html';
      let html = read(file);
      if (!shell) html = html.replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '');
      return route.fulfill({ contentType: 'text/html', body: html });
    }
    let data = { itens: [], contatos: [], total: 0, deslocamento: 0, tem_mais: false };
    if (p === '/v1/auth/me') data = { id: 7, nome: 'Teste', perfil: 'operador', permissoes: ['leads.view', 'leads.manage'], organizacao: { id: 1 } };
    if (p === '/v1/admin/leads') {
      const size = Number(u.searchParams.get('limite')) || 50;
      data = { itens: Array.from({ length: Math.min(size, 6) }, (_, i) => lead(i + 1)), total: 6, total_global: 6,
        pesquisas_total: 6, deslocamento: 0, por_status: {}, acoes: { gerenciar: true, arquivar: true, excluir_pesquisa: exclusoes > 0 }, tem_mais: false };
    }
    if (/\/v1\/admin\/leads\/\d+$/.test(p)) data = lead(Number(p.split('/').pop()));
    if (p.endsWith('/contatos')) data = { contatos: [], total: 0 };
    if (p === '/v1/admin/exclusoes-pesquisas') data = { itens: Array.from({ length: exclusoes }, (_, i) => ({ id: `sol${i + 1}`, pesquisa_marca: `Marca ${i + 1}`, status_solicitacao: 'pendente', criado_em: '2026-11-15T12:00:00Z', solicitante_nome: 'Op', motivo: 'duplicada' })), total: exclusoes };
    if (p === '/v1/admin/atualizacoes') data = { novidades: [
      { id: 'n1', versao: 'v1.0.1', titulo: 'Aviso comum 1', impacto_usuario: 'Impacto 1', classificacao: 'comum', leitura_obrigatoria: false, pode_adiar: true, estado: {} },
      { id: 'n2', versao: 'v1.0.2', titulo: 'Aviso comum 2', impacto_usuario: 'Impacto 2', classificacao: 'comum', leitura_obrigatoria: false, pode_adiar: true, estado: {} },
      { id: 'n3', versao: 'v1.1.0', titulo: 'Aviso crítico', impacto_usuario: 'Impacto crítico', classificacao: 'critica', leitura_obrigatoria: true, pode_adiar: false, estado: {} },
    ], lidos: [], adiados: [] };
    if (p.includes('/analises/')) data = { pesquisa: { id: 'r1', marca: 'Marca 1' }, permissoes: {}, relatorio_completo: {}, workflow: { state: 'PENDING_REVIEW', history: [] }, usuario: {} };
    if (p.endsWith('/classes-nice')) data = [];
    await route.fulfill({ json: data });
  });
  return { errors, requests };
}
async function browserTest(fn) { const browser = await chromium.launch(); try { await fn(browser); } finally { await browser.close(); } }

test('R02: destinos de WhatsApp validados; país incerto exige confirmação explícita', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const f = await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas');
  await page.locator('#leads-list tr').first().waitFor();
  // Com DDI explícito (+55 ou internacional): link direto com destino visível.
  assert.equal(await page.locator('tr[data-lead-id="3"] a[data-wa-destino]').getAttribute('href'), 'https://wa.me/5516982050534');
  assert.equal(await page.locator('tr[data-lead-id="4"] a[data-wa-destino]').getAttribute('href'), 'https://wa.me/14155550100');
  assert.equal(await page.locator('tr[data-lead-id="3"] a[data-wa-destino]').getAttribute('aria-label'), 'Abrir WhatsApp para +55 (16) 98205-0534');
  // Nacional 10/11 dígitos sem DDI: país incerto, abre só após confirmação explícita.
  assert.equal(await page.locator('tr[data-lead-id="1"] a[data-wa-destino]').count(), 0);
  assert.equal(await page.locator('tr[data-lead-id="1"] a[data-wa-sugestao]').getAttribute('href'), 'https://wa.me/5516982050534');
  assert.equal(await page.locator('tr[data-lead-id="2"] a[data-wa-sugestao]').getAttribute('href'), 'https://wa.me/551632050534');
  // Formato ambíguo: sem link, botão pede revisão e orienta o operador.
  assert.equal(await page.locator('tr[data-lead-id="5"] a[data-wa-destino]').count(), 0);
  assert.equal(await page.locator('tr[data-lead-id="5"] a[data-wa-sugestao]').count(), 0);
  await page.locator('tr[data-lead-id="5"] [data-wa-invalido]').click();
  await page.waitForFunction(() => document.querySelector('#admin-message').textContent.includes('formato não reconhecido'));
  // Vazio: nenhum controle de WhatsApp.
  assert.equal(await page.locator('tr[data-lead-id="6"] a[data-wa-destino]').count(), 0);
  assert.equal(await page.locator('tr[data-lead-id="6"] [data-wa-invalido]').count(), 0);
  // O destino completo é confirmado antes de abrir; cancelar não navega.
  let aviso = '';
  page.on('dialog', async dialog => { aviso = dialog.message(); await dialog.dismiss(); });
  await page.locator('tr[data-lead-id="1"] a[data-wa-sugestao]').click();
  assert.match(aviso, /código do país/);
  assert.match(aviso, /\+55 \(16\) 98205-0534/);
  await page.locator('tr[data-lead-id="3"] a[data-wa-destino]').click();
  assert.match(aviso, /Abrir conversa no WhatsApp com \+55 \(16\) 98205-0534/);
  assert.deepEqual(f.errors, []);
}));

test('R01 390px: status legível, ações principais visíveis e secundárias no menu', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const f = await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas');
  await page.locator('#leads-list tr').first().waitFor();
  const row = page.locator('tr[data-research-id="r1"]');
  const statusWidth = await row.locator('.lead-status').evaluate(el => el.getBoundingClientRect().width);
  assert.ok(statusWidth >= 120, `seletor de status com ${statusWidth}px (mínimo 120)`);
  assert.ok(await row.locator('a[href*="/admin/analises/"]').isVisible());
  assert.ok(await row.locator('.view-lead').isVisible());
  assert.ok(!(await row.locator('.register-attendance').isVisible()));
  await row.locator('.lead-actions-toggle').click();
  assert.ok(await row.locator('.register-attendance').isVisible());
  assert.equal(await row.locator('.lead-actions-toggle').getAttribute('aria-expanded'), 'true');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(f.errors, []);
}));

test('R01 820px: funil e ações cabem na própria célula', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 820, height: 1000 } });
  const f = await fixture(page);
  await page.goto('http://operations.test/admin/pesquisas');
  await page.locator('#leads-list tr').first().waitFor();
  for (const sel of ['tr[data-research-id="r1"]', 'tr[data-research-id="r2"]']) {
    const row = page.locator(sel);
    const funil = await row.locator('.fase-mini').evaluate(el => { const r = el.getBoundingClientRect(); const td = el.closest('td').getBoundingClientRect(); return { dir: r.right, tdDir: td.right }; });
    assert.ok(funil.dir <= funil.tdDir + 1, `funil invade a célula vizinha (${JSON.stringify(funil)})`);
    const acoes = await row.locator('.lead-row-actions').evaluate(el => { const r = el.getBoundingClientRect(); const td = el.closest('td').getBoundingClientRect(); return { dir: r.right, tdDir: td.right }; });
    assert.ok(acoes.dir <= acoes.tdDir + 1, `ações invadem a célula vizinha (${JSON.stringify(acoes)})`);
  }
  assert.deepEqual(f.errors, []);
}));

test('R03: Centro mostra resumo compacto; crítico aberto; expandir não grava leitura', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const f = await fixture(page, { shell: true });
  await page.goto('http://operations.test/admin/pesquisas');
  await page.locator('#admin-atualizacoes-banner').waitFor();
  assert.match(await page.locator('.admin-avisos-resumo').innerText(), /3 avisos da plataforma \(1 crítico\)/);
  assert.ok(await page.locator('[data-atualizacao-id="n3"]').isVisible());
  assert.ok(!(await page.locator('[data-atualizacao-id="n1"]').isVisible()));
  await page.locator('[data-toggle-avisos]').click();
  assert.ok(await page.locator('[data-atualizacao-id="n1"]').isVisible());
  assert.equal(f.requests.filter(r => r.path.includes('confirmar-leitura')).length, 0);
  await page.locator('[data-atualizacao-id="n1"] [data-confirmar-atualizacao]').click();
  await page.waitForFunction(() => !document.querySelector('[data-atualizacao-id="n1"]'));
  assert.equal(f.requests.filter(r => r.path.includes('confirmar-leitura')).length, 1);
  assert.deepEqual(f.errors, []);
}));

test('R04/R05: fila de exclusões recolhida e contrato de filtros/contagem explícito', { timeout: 30000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const f = await fixture(page, { exclusoes: 2 });
  await page.goto('http://operations.test/admin/pesquisas');
  await page.locator('#leads-list tr').first().waitFor();
  // A fila de exclusões carrega em paralelo à lista de contatos; esperar a
  // contagem real evita ler o "0 pendentes" estático do HTML (flake do CI).
  await page.waitForFunction(() => document.querySelector('#deletion-request-count').textContent.includes('2 pendentes'));
  assert.ok(await page.locator('#deletion-requests').isVisible());
  assert.match(await page.locator('#deletion-request-count').innerText(), /2 pendentes/);
  assert.ok(!(await page.locator('#deletion-requests-body').isVisible()));
  await page.locator('#deletion-requests-toggle').click();
  assert.ok(await page.locator('#deletion-requests-body').isVisible());
  assert.match(await page.locator('label:has(#lead-risk-filter) span').innerText(), /Contatos com pesquisa de risco/);
  assert.match(await page.locator('label:has(#lead-date-start) span').innerText(), /Contato criado desde/);
  assert.match(await page.locator('#lead-sort option[value=recentes]').innerText(), /Criação do contato \(mais recentes\)/);
  assert.match(await page.locator('#lead-page-summary').innerText(), /1–6 de 6 contatos · 6 linhas de pesquisa exibidas/);
  assert.deepEqual(f.errors, []);
}));

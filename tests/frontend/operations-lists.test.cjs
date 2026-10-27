const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const lead = id => ({ id, nome: `Contato ${id}`, empresa: 'Empresa', email: 'teste@example.com',
  telefone: '', status: 'novo', total_pesquisas: 1, pesquisas: [{ id: `r${id}`, marca: `Marca ${id}`,
    criado_em: '2026-01-01T12:00:00Z', risco_nivel: 'baixo' }], criado_em: '2026-01-01T12:00:00Z' });

async function fixture(page, { manage = true, response, shell = false, permissions } = {}) {
  const requests = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    requests.push({ path: p, params: url.searchParams, method: request.method() });
    if (p.startsWith('/admin/')) {
      const file = p.startsWith('/admin/analises/') ? 'admin-analise.html'
        : p === '/admin/prospeccao' ? 'admin-prospeccao.html' : 'admin-leads.html';
      let html = fs.readFileSync(path.join(web, file), 'utf8');
      if (!shell) html = html.replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '');
      return route.fulfill({ contentType: 'text/html', body: html });
    }
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (response && await response(route, url)) return;
    let data = { itens: [], total: 0, deslocamento: 0, tem_mais: false };
    if (p === '/v1/auth/me') data = { nome: 'Equipe de teste', perfil: 'operador',
      permissoes: permissions || (manage ? ['prospeccao.manage', 'prospeccao.view', 'leads.view'] : []) };
    if (p === '/v1/admin/atualizacoes') data = { novidades: [] };
    if (p === '/v1/admin/leads') {
      const offset = Number(url.searchParams.get('deslocamento')) || 0;
      const size = Number(url.searchParams.get('limite')) || 50;
      data = { itens: Array.from({ length: Math.min(size, 123-offset) }, (_, i) => lead(offset+i+1)),
        total: 123, deslocamento: offset, tem_mais: offset+size < 123,
        total_global: 123, pesquisas_total: 123, por_status: {}, acoes: { gerenciar: true } };
    }
    if (p === '/v1/admin/leads-crm') data = {};
    if (p === '/v1/admin/prospeccao/dashboard') data = { funil: { novo: 1 } };
    if (p === '/v1/admin/prospects') data = { total: 21, itens: [{ id: 1, razao_social: 'Empresa Radar', status: 'novo', score: null }] };
    if (p.includes('/analises/')) data = {};
    await route.fulfill({ json: data });
  });
  return { requests, errors };
}

test('Leads: páginas diretas, limites e filtros enviados ao servidor', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { requests, errors } = await fixture(page);
    await page.goto('http://operations.test/admin/pesquisas');
    await page.waitForSelector('#leads-list tr');
    await page.locator('#lead-search').fill('Padaria');
    await page.locator('#lead-risk-filter').selectOption('alto');
    await page.locator('#lead-sort').selectOption('nome');
    const filtered = page.waitForResponse(r => r.url().includes('busca=Padaria'));
    await page.locator('#lead-filters button[type=submit]').click();
    await filtered;
    await page.locator('#lead-last').click();
    await page.waitForFunction(() => document.querySelector('#lead-page-number').value === '3');
    let query = requests.filter(r => r.path === '/v1/admin/leads').at(-1).params;
    assert.equal(query.get('deslocamento'), '100');
    assert.equal(query.get('busca'), 'Padaria');
    assert.equal(query.get('risco'), 'alto');
    assert.equal(query.get('ordenacao'), 'nome');
    await page.locator('#lead-page-number').fill('9999');
    await page.locator('#lead-page-number').press('Enter');
    await page.waitForFunction(() => document.querySelector('#lead-page-number').value === '3');
    await page.locator('#lead-first').click();
    await page.waitForFunction(() => document.querySelector('#lead-page-number').value === '1');
    await page.locator('#lead-page-size').selectOption('200');
    await page.waitForFunction(() => document.querySelectorAll('#leads-list tr').length === 123);
    assert.equal(await page.locator('#lead-next').isDisabled(), true);
    await page.locator('#clear-lead-filters').click();
    await page.waitForFunction(() => document.querySelector('#lead-search').value === '');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Leads: voltar pela análise ou navegador restaura cliente, filtros, página e rolagem', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await fixture(page);
    await page.goto('http://operations.test/admin/pesquisas');
    await page.waitForSelector('#leads-list tr');
    await page.locator('#lead-search').fill('Contato');
    await page.locator('#lead-filters button[type=submit]').click();
    await page.locator('#lead-next').click();
    await page.waitForFunction(() => document.querySelector('#lead-page-number').value === '2');
    const link = page.locator('tr[data-research-id="r60"] a[href*="analises"]');
    await link.scrollIntoViewIfNeeded();
    const scroll = await page.evaluate(() => scrollY);
    await link.click();
    await page.waitForURL('**/admin/analises/r60?retorno=*');
    await page.waitForFunction(() => document.querySelector('.analysis-breadcrumb a').href.includes('retomar='));
    await page.locator('.analysis-breadcrumb a').click();
    await page.waitForSelector('.lead-returned');
    assert.equal(await page.locator('#lead-search').inputValue(), 'Contato');
    assert.equal(await page.locator('#lead-page-number').inputValue(), '2');
    assert.equal(await page.locator('.lead-returned').getAttribute('data-lead-id'), '60');
    await page.waitForFunction(y => Math.abs(scrollY-y) < 3, scroll);
    await page.locator('tr[data-research-id="r60"] a[href*="analises"]').click();
    await page.waitForURL('**/admin/analises/r60?retorno=*');
    await page.goBack();
    await page.waitForSelector('.lead-returned');
    assert.equal(await page.locator('#lead-page-number').inputValue(), '2');
  } finally { await browser.close(); }
});

test('Leads: resposta lenta não sobrescreve a última busca', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await fixture(page, { response: async (route, url) => {
      if (url.pathname !== '/v1/admin/leads' || !url.searchParams.has('busca')) return false;
      const slow = url.searchParams.get('busca') === 'antiga';
      if (slow) await pending;
      await route.fulfill({ json: { itens: [lead(slow ? 1 : 2)], total: 1, deslocamento: 0,
        tem_mais: false, por_status: {}, acoes: {} } });
      return true;
    } });
    await page.goto('http://operations.test/admin/pesquisas');
    await page.waitForSelector('#leads-list tr');
    await page.locator('#lead-search').fill('antiga');
    const oldRequest = page.waitForRequest(r => r.url().includes('busca=antiga'));
    await page.locator('#lead-filters button[type=submit]').click();
    await oldRequest;
    await page.locator('#lead-search').fill('nova');
    await page.locator('#lead-filters button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#leads-list tr').dataset.leadId === '2');
    const oldResponse = page.waitForResponse(r => r.url().includes('busca=antiga'));
    release(); await oldResponse;
    assert.equal(await page.locator('#leads-list tr').getAttribute('data-lead-id'), '2');
  } finally { await browser.close(); }
});

test('Radar: visualizador só recebe ações de leitura e limpar remove filtros combinados', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { errors, requests } = await fixture(page, { manage: false });
    await page.goto('http://operations.test/admin/prospeccao');
    await page.waitForSelector('.prospeccao-item');
    assert.equal(await page.locator('[data-aprovar], [data-enriquecer], [data-triar]').count(), 0);
    assert.equal(await page.locator('#open-manual').isHidden(), true);
    assert.equal(await page.locator('[data-detalhe]').isVisible(), true);
    await page.locator('[name=uf]').first().selectOption(['SP', 'RJ']);
    await page.locator('#prospeccao-filter [name=cidade]').fill('São Paulo, Rio de Janeiro');
    const filtered = page.waitForResponse(r => r.url().includes('uf=SP'));
    await page.locator('#prospeccao-filter button[type=submit]').click();
    await filtered;
    let query = requests.filter(r => r.path === '/v1/admin/prospects').at(-1).params;
    assert.deepEqual(query.getAll('uf').sort(), ['RJ', 'SP']);
    assert.equal(query.getAll('cidade').length, 2);
    const cleared = page.waitForResponse(r => r.url().includes('/prospects?') && !r.url().includes('uf='));
    await page.locator('#prospeccao-clear').click();
    await cleared;
    query = requests.filter(r => r.path === '/v1/admin/prospects').at(-1).params;
    assert.equal(query.has('uf'), false);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Radar: bloqueia clique repetido e volta à primeira página quando o filtro esvazia', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let approved = false, calls = 0, release;
    const pending = new Promise(resolve => { release = resolve; });
    await fixture(page, { response: async (route, url) => {
      if (url.pathname === '/v1/admin/prospects/1/aprovar') {
        calls++; await pending; approved = true; await route.fulfill({ json: {} }); return true;
      }
      if (url.pathname === '/v1/admin/prospects' && approved) {
        await route.fulfill({ json: { itens: [], total: 0 } }); return true;
      }
      return false;
    } });
    await page.goto('http://operations.test/admin/prospeccao');
    await page.waitForSelector('[data-aprovar]');
    await page.locator('#prospeccao-next').click();
    await page.waitForFunction(() => document.querySelector('#prospeccao-page-summary').textContent.includes('Página 2'));
    await page.locator('[data-aprovar]').click();
    assert.equal(await page.locator('[data-aprovar]').isDisabled(), true);
    await page.locator('[data-aprovar]').dispatchEvent('click');
    release();
    await page.waitForFunction(() => document.querySelector('#prospeccao-page-summary').textContent.includes('Página 1 de 1'));
    assert.equal(calls, 1);
    assert.equal(await page.locator('#prospeccao-prev').isDisabled(), true);
  } finally { await browser.close(); }
});

test('Radar: conversão usa sua própria permissão, sem exigir gerenciamento', async () => {
  const browser = await chromium.launch();
  try {
    for (const [permission, expected] of [['prospeccao.convert', 1], ['prospeccao.manage', 0]]) {
      const page = await browser.newPage();
      await fixture(page, { permissions: [permission], response: async (route, url) => {
        if (url.pathname !== '/v1/admin/prospects') return false;
        await route.fulfill({ json: { total: 1, itens: [{ id: 1, razao_social: 'Aprovado', status: 'aprovado' }] } });
        return true;
      } });
      await page.goto('http://operations.test/admin/prospeccao');
      await page.waitForSelector('.prospeccao-item');
      assert.equal(await page.locator('[data-converter]').count(), expected);
      await page.close();
    }
  } finally { await browser.close(); }
});

test('Radar: a resposta da última consulta prevalece', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await fixture(page, { response: async (route, url) => {
      if (url.pathname !== '/v1/admin/prospects' || !url.searchParams.has('busca')) return false;
      const slow = url.searchParams.get('busca') === 'antiga';
      if (slow) await pending;
      await route.fulfill({ json: { total: 1, itens: [{ id: slow ? 1 : 2, razao_social: slow ? 'Antiga' : 'Nova', status: 'novo' }] } });
      return true;
    } });
    await page.goto('http://operations.test/admin/prospeccao');
    await page.waitForSelector('.prospeccao-item');
    await page.locator('#prospeccao-filter [name=busca]').fill('antiga');
    const slowRequest = page.waitForRequest(r => r.url().includes('busca=antiga'));
    await page.locator('#prospeccao-filter button[type=submit]').click();
    await slowRequest;
    await page.locator('#prospeccao-filter [name=busca]').fill('nova');
    await page.locator('#prospeccao-filter button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('.prospeccao-item').dataset.id === '2');
    const slowResponse = page.waitForResponse(r => r.url().includes('busca=antiga'));
    release(); await slowResponse;
    assert.equal(await page.locator('.prospeccao-item').getAttribute('data-id'), '2');
  } finally { await browser.close(); }
});

test('Telas completas: identidade oficial sem recorte e navegação em computador e celular', async () => {
  const browser = await chromium.launch();
  try {
    for (const screen of ['pesquisas', 'prospeccao']) {
      const page = await browser.newPage();
      const { errors } = await fixture(page, { shell: true });
      await page.goto(`http://operations.test/admin/${screen}`);
      await page.waitForSelector(screen === 'pesquisas' ? '#leads-list tr' : '.prospeccao-item');
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        await page.evaluate(() => document.fonts.ready);
        await page.waitForFunction(mobile => {
          const bounds = document.querySelector('.admin-sidebar').getBoundingClientRect();
          return mobile ? bounds.right <= 0 : bounds.left === 0;
        }, width === 390);
        const selector = width === 390 ? '.admin-mobile-header .brand-avatar' : '.admin-sidebar-brand .brand-avatar';
        const artwork = await page.locator(selector).evaluate(el => ({
          loaded: el.complete && el.naturalWidth > 0, fit: getComputedStyle(el).objectFit,
          radius: getComputedStyle(el).borderRadius,
        }));
        assert.deepEqual(artwork, { loaded: true, fit: 'contain', radius: '0px' });
        assert.equal(await page.locator('.assistente-widget-launcher img').evaluate(el => getComputedStyle(el).objectFit), 'contain');
        assert.ok(await page.locator('.assistente-widget-launcher img').evaluate(el => {
          const image = el.getBoundingClientRect(), button = el.parentElement.getBoundingClientRect();
          return image.top >= button.top && image.bottom <= button.bottom;
        }), 'mascot fits inside its launcher');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${screen} overflow at ${width} (root ${await page.evaluate(() => document.documentElement.scrollWidth)}): ${JSON.stringify(await page.evaluate(() => [...document.querySelectorAll("body *")].filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0, 25).map(el => ({ tag: el.tagName, id: el.id, cls: el.className, right: el.getBoundingClientRect().right }))))}`);
        if (process.env.OPERATIONS_SCREENSHOTS) {
          fs.mkdirSync(process.env.OPERATIONS_SCREENSHOTS, { recursive: true });
          await page.screenshot({ path: path.join(process.env.OPERATIONS_SCREENSHOTS, `${screen}-${width}.png`) });
        }
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
});

// Auditoria 2026-10-27: o primeiro clique em "Triar marca"/"Verificar site"
// precisa dar feedback imediato, não disparar requisições duplicadas em cliques
// repetidos e resolver o estado pendente quando o resultado chega na listagem.
test('Radar: triagem e verificação de site com feedback imediato e sem duplicar requisições', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let cliquesDisparados = 0;
    let triado = false;
    const { errors } = await fixture(page, {
      response: async (route, url) => {
        const p = url.pathname;
        if (p === '/v1/admin/prospects') {
          const item = { id: 1, razao_social: 'Empresa Radar', nome_fantasia: 'Empresa Radar', status: 'novo', score: null,
            presenca_digital: null, triagem_marca_status: triado ? 'nao_localizado' : null };
          return route.fulfill({ json: { total: 1, itens: [item] } }).then(() => true);
        }
        if (p.endsWith('/triar-marca')) {
          cliquesDisparados += 1;
          triado = false; // o resultado só aparece na listagem depois do "worker"
          return route.fulfill({ json: { job_id: 'j1', duplicado: false } }).then(() => true);
        }
        return false;
      },
    });
    await page.goto('http://operations.test/admin/prospeccao');
    await page.waitForSelector('.prospeccao-item');
    const botao = page.locator('[data-triar]');
    await botao.click({ force: true });
    await botao.click({ force: true }).catch(() => {}); // segundo clique rápido não deve duplicar
    await page.waitForTimeout(300);
    assert.equal(cliquesDisparados, 1, 'cliques repetidos não devem gerar nova requisição');
    assert.ok(await page.locator('[data-triar][disabled]').count() >= 1, 'botão deve ficar desabilitado com rótulo de aguardando');
    assert.match(await page.locator('[data-triar]').first().innerText(), /Triando marca/);
    // o worker conclui: a próxima leitura da listagem traz o resultado e encerra o pendente
    triado = true;
    await page.waitForTimeout(4500);
    assert.equal(await page.locator('[data-triar]').count(), 0, 'após concluir, o botão de triar desaparece');
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
});

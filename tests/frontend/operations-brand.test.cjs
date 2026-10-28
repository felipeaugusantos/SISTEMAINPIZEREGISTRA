const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const templates = fs.readdirSync(web).filter(f => f.endsWith('.html') && fs.readFileSync(path.join(web, f), 'utf8').includes('/static/admin-shell.js'));
const main = fs.readFileSync(path.join(web, '../main.py'), 'utf8');
const routes = [...main.matchAll(/@app.get\(\s*"([^"]+)"[\s\S]*?return FileResponse\(web_dir \/ "([^"]+)"\)/g)]
  .filter(m => templates.includes(m[2])).map(m => [m[1].replace(/\{[^}]+\}/g, 'teste'), m[2]]);
async function fixture(page, marca) {
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p === '/v1/auth/me') return route.fulfill({ json: { nome: 'Equipe teste', perfil: 'administrador', permissoes: [], organizacao: { marca } } });
    if (p.endsWith('/marca.css')) return route.fulfill({ contentType: 'text/css', body: '' });
    if (p.startsWith('/v1/')) return route.fulfill({ json: { novidades: [] } });
    const file = routes.find(([url]) => url === p)?.[1];
    if (!file) return route.fulfill({ status: 404, body: p });
    // Exercise every real template and its CSS with only the shared shell JS.
    // Module API workflows are covered by their own existing suites.
    const html = fs.readFileSync(path.join(web, file), 'utf8').replace(/<script\b[^>]*src="(?!\/static\/admin-shell\.js)[^"]*"[^>]*><\/script>/g, '');
    return route.fulfill({ contentType: 'text/html', body: html });
  });
}
async function artwork(page, expected = '/static/assets/personagem.png?v=1') {
  await page.waitForFunction(() => !document.querySelector('#admin-current-user').textContent.includes('Carregando'));
  const images = page.locator('.admin-sidebar-brand img, .admin-mobile-header img, .assistente-widget-launcher img');
  const dedicated = await page.locator('body').getAttribute('data-admin-section') === 'assistente';
  assert.equal(await images.count(), dedicated ? 2 : 3, page.url());
  for (const img of await images.all()) {
    const state = await img.evaluate(async el => {
      await el.decode(); const s = getComputedStyle(el), r = el.getBoundingClientRect();
      return { src: el.getAttribute('src'), fit: s.objectFit, position: s.objectPosition, filter: s.filter, radius: s.borderRadius, width: el.naturalWidth, height: el.naturalHeight, box: [r.width, r.height] };
    });
    assert.equal(state.src, expected); assert.equal(state.fit, 'contain');
    assert.equal(state.position, '50% 50%'); assert.equal(state.filter, 'none');
    if (await img.getAttribute('class') === 'brand-avatar') assert.equal(state.radius, '0px');
    assert.ok(state.width > 0 && state.height > 0);
  }
  if (!dedicated) assert.ok(await page.locator('.assistente-widget-launcher img').evaluate(el => {
    const r = el.getBoundingClientRect(), p = el.parentElement.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.left >= p.left && r.top >= p.top && r.right <= p.right && r.bottom <= p.bottom;
  }));
}
test('identidade compartilhada em todos os templates, rotas diretas e recarga', async () => {
  assert.deepEqual([...new Set(routes.map(r => r[1]))].sort(), templates.sort());
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      await fixture(page);
      for (const [url] of routes) { await page.goto('http://brand.test' + url); await artwork(page); }
      await page.reload(); await artwork(page);
      await page.locator('.assistente-widget-launcher').click();
      assert.equal(await page.locator('.assistente-widget-panel').isVisible(), true);
      await artwork(page);
      assert.deepEqual(errors, []); await page.close();
    }
  } finally { await browser.close(); }
});
test('navegação entre módulos mantém imagem e menu móvel funciona', async () => {
  const browser = await chromium.launch();
  try { for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } }); await fixture(page);
    await page.goto('http://brand.test/admin');
    for (const url of ['/admin/atualizacoes','/admin/relatorios','/admin/pesquisas','/admin/prospeccao','/admin/consulta','/admin']) {
      if (width === 390) await page.locator('.admin-menu-toggle').click();
      await page.locator('.admin-sidebar-nav a[href="' + url + '"]').first().click();
      await page.waitForURL('http://brand.test' + url); await artwork(page);
    }
    await page.close();
  }} finally { await browser.close(); }
});
test('marca própria e fallback conservam comportamento e proporções', async () => {
  const browser = await chromium.launch();
  try { for (const broken of [false, true]) {
    const page = await browser.newPage(); const logo = broken ? '/missing.png' : '/static/assets/ze-registra-logo-tema-claro.png';
    await fixture(page, { propria: true, nome: 'Escritório teste', logo_url: logo });
    await page.goto('http://brand.test/admin');
    await page.waitForFunction(src => [...document.querySelectorAll('.brand-avatar')].every(el => new URL(el.src).pathname + new URL(el.src).search === src && el.complete && el.naturalWidth > 0), broken ? '/static/assets/personagem.png?v=1' : logo);
    assert.equal(await page.locator('.admin-sidebar-brand .brand-wordmark').textContent(), 'Escritório teste');
    for (const img of await page.locator('.brand-avatar').all()) assert.equal(await img.evaluate(el => getComputedStyle(el).objectFit), 'contain');
    assert.equal(await page.locator('.assistente-widget-launcher img').getAttribute('src'), '/static/assets/personagem.png?v=1');
    await page.close();
  }} finally { await browser.close(); }
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const screens = { '/admin': 'admin.html', '/admin/atualizacoes': 'admin-atualizacoes.html', '/admin/relatorios': 'admin-relatorios.html', '/admin/pesquisas': 'admin-leads.html', '/admin/prospeccao': 'admin-prospeccao.html', '/admin/consulta': 'admin-consulta.html' };
async function fixture(page, baselineCss) {
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/static/styles.css' && baselineCss) return route.fulfill({ contentType: 'text/css', body: baselineCss });
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p === '/v1/auth/me') return route.fulfill({ json: { nome: 'Equipe de teste', perfil: 'administrador', permissoes: [] } });
    if (p.startsWith('/v1/')) return route.fulfill({ json: { novidades: [] } });
    const file = screens[p];
    if (!file) return route.fulfill({ status: 404, body: '' });
    const html = fs.readFileSync(path.join(web, file), 'utf8').replace(/<script\b[^>]*src="(?!\/static\/admin-shell\.js)[^"]*"[^>]*><\/script>/g, '');
    return route.fulfill({ contentType: 'text/html', body: html });
  });
}
async function metrics(page) {
  await page.locator('.admin-sidebar-brand img').evaluate(el => el.decode());
  await page.evaluate(() => document.fonts.ready);
  return page.evaluate(() => {
    const read = selector => { const el = document.querySelector(selector), r = el.getBoundingClientRect(), s = getComputedStyle(el); return { x:r.x, y:r.y, width:r.width, height:r.height, display:s.display, fit:s.objectFit, filter:s.filter, font:s.fontFamily, color:s.color, fontSize:s.fontSize, cssWidth:s.width, cssHeight:s.height }; };
    return { header:read('.admin-sidebar-brand'), image:read('.admin-sidebar-brand img'), text:read('.admin-sidebar-brand > span'), name:read('.admin-sidebar-brand strong'), subtitle:read('.admin-sidebar-brand small'), sidebar:read('.admin-sidebar'), nav:read('.admin-sidebar-nav'), mobile:read('.admin-mobile-header .brand-avatar'), widget:read('.assistente-widget-launcher') };
  });
}
test('cabeçalho mantém arte oficial, alinhamento e composição em seis páginas e quatro larguras', async () => {
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(web,'static/assets/personagem.png'))).digest('hex'), '9cd2a7e627edc066093e272c407b799d12532883dd26034f198670d5b1470442');
  const browser = await chromium.launch();
  try { for (const width of [1440,1024,820,390]) {
    const page = await browser.newPage({ viewport:{width,height:900} }); const errors=[];page.on('pageerror',e=>errors.push(e.message));await fixture(page);
    for (const url of Object.keys(screens)) {
      await page.goto('http://sidebar.test'+url);
      if (await page.locator('.admin-menu-toggle').isVisible()) await page.locator('.admin-menu-toggle').click();
      const m=await metrics(page);
      assert.equal(await page.locator('.admin-sidebar-brand img').getAttribute('src'), '/static/assets/personagem.png?v=1');
      // O Chromium pode devolver 41.999996px para uma caixa CSS de 42px.
      assert.ok(Math.abs(m.image.width-42)<0.01);assert.ok(Math.abs(m.image.height-56)<0.01);assert.equal(m.image.fit,'contain');assert.equal(m.image.filter,'none');
      assert.ok(Math.abs((m.image.y+m.image.height/2)-(m.text.y+m.text.height/2))<1);
      assert.equal(m.name.x,m.subtitle.x);assert.ok(Math.abs(m.subtitle.y-m.name.y-m.name.height-4)<1);
      assert.equal(m.header.height,69);assert.equal(m.sidebar.width,252);
      assert.equal(m.name.fontSize,'18.56px');assert.ok(m.name.font.includes('Pigura'));assert.equal(m.name.color,'rgb(255, 255, 255)');
      assert.equal(m.widget.width,60);assert.equal(m.widget.height,60);
      assert.ok(m.image.x>=m.header.x && m.image.x+m.image.width<=m.header.x+m.header.width);
    }
    assert.deepEqual(errors,[]);await page.close();
  }} finally {await browser.close();}
});
test('recolher, expandir e recarregar mantém personagem centralizado e posição do menu', async () => {
  const browser=await chromium.launch();
  try {const page=await browser.newPage({viewport:{width:1440,height:900}});await fixture(page);await page.goto('http://sidebar.test/admin');const expanded=await metrics(page);
    await page.locator('#admin-sidebar-toggle').click();const collapsed=await metrics(page);
    assert.equal(collapsed.sidebar.width,76);assert.equal(collapsed.text.display,'none');assert.equal(collapsed.image.height,56);
    assert.ok(Math.abs(collapsed.image.x+collapsed.image.width/2-(collapsed.sidebar.x+collapsed.sidebar.width/2))<1);
    assert.equal(collapsed.nav.y,expanded.nav.y);assert.deepEqual(collapsed.widget,expanded.widget);
    await page.reload();assert.equal((await metrics(page)).text.display,'none');
    await page.locator('#admin-sidebar-toggle').click();assert.deepEqual(await metrics(page),expanded);
  }finally{await browser.close();}
});

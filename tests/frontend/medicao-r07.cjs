// Medição R07: requisições disparadas ao abrir a ficha do lead (ambiente descartável).
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');
const web = path.resolve('/tmp/repo/app/web');
const read = f => fs.readFileSync(path.join(web, f), 'utf8');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const reqs = [];
  page.on('request', r => { if (r.url().includes('/v1/')) reqs.push(`${r.method()} ${new URL(r.url()).pathname}`); });
  await page.route('**/*', route => {
    const u = new URL(route.request().url()), p = u.pathname;
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) return route.fulfill({ contentType: 'text/html',
      body: read('admin-leads.html').replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '') });
    if (p === '/v1/auth/me') return route.fulfill({ json: { id: 7, nome: 'Teste', perfil: 'admin', permissoes: ['leads.view', 'leads.manage'], organizacao: { id: 1 }, mfa_ativo: false } });
    if (p === '/v1/admin/leads/42') return route.fulfill({ json: { id: 42, nome: 'Ana Souza', pesquisas: [], status: 'novo', tags: [], criado_em: '2026-01-01T12:00:00Z', responsavel_id: 7 } });
    if (p === '/v1/admin/leads/42/portal-mensagens' && route.request().method() === 'GET') return route.fulfill({ json: { mensagens: [
      { id: 1, autor_tipo: 'cliente', mensagem: 'Enviei os documentos.', criado_em: '2026-11-15T10:00:00Z', lida_em: null },
    ] } });
    if (p === '/v1/admin/leads' ) return route.fulfill({ json: { itens: [{ id: 42, nome: 'Ana Souza', status: 'novo', tags: [], criado_em: '2026-01-01T12:00:00Z', responsavel_id: 7 }], total: 1, total_global: 1, pesquisas_total: 0, por_status: { novo: 1 }, acoes: { gerenciar: true } } });
    return route.fulfill({ json: { itens: [], mensagens: [], contatos: [], total: 0, providers: [], arquivos: [], materiais: [], envios: [], documentos: [], existe: false } });
  });
  await page.goto('http://operations.test/admin/leads');
  await page.locator('.view-lead').first().waitFor();
  reqs.length = 0;
  await page.locator('.view-lead').first().click();
  await page.waitForTimeout(2000);
  const contagem = {};
  for (const r of reqs) { const k = r.replace(/\/\d+\//, '/{id}/'); contagem[k] = (contagem[k] || 0) + 1; }
  console.log('REQUISICOES AO ABRIR A FICHA:');
  Object.entries(contagem).sort().forEach(([k, v]) => console.log(` ${v}x ${k}`));
  console.log(`TOTAL: ${reqs.length}`);
  await browser.close();
})();

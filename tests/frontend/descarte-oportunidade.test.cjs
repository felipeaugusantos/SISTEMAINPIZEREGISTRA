// Correção 17/11/2026: "Descartado" na lista de Leads abre uma janela com o
// motivo de perda obrigatório (antes o PATCH só com status devolvia 422 sem
// campo para preencher). Descarte de oportunidade continua separado do
// arquivamento do contato e das solicitações de exclusão de pesquisa.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const shots = process.env.DESCARTE_SHOTS || '';
const read = file => fs.readFileSync(path.join(web, file), 'utf8');
const lead = id => ({ id, nome: `Contato ${id}`, empresa: `Empresa ${id}`, status: 'novo', pesquisas: [], total_pesquisas: 0,
  criado_em: '2026-11-15T12:00:00Z', responsavel_nome: 'Operador Teste', proxima_acao_em: null, ultimo_contato_em: null,
  telefone: '', email: `c${id}@example.com`, tags: [] });

async function fixture(page, { patchStatus = 200, patchDetail = '' } = {}) {
  page.setDefaultTimeout(7000);
  const errors = [], patches = [];
  const status = { 1: 'novo', 2: 'novo' };
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) return route.fulfill({ contentType: 'text/html', body: read('admin-leads.html').replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '') });
    if (req.method() === 'PATCH' && /\/v1\/admin\/leads\/\d+$/.test(p)) {
      const body = JSON.parse(req.postData());
      patches.push({ id: p.split('/').pop(), body });
      if (patchStatus !== 200) return route.fulfill({ status: patchStatus, json: { detail: patchDetail } });
      status[p.split('/').pop()] = body.status;
      return route.fulfill({ json: lead(1) });
    }
    let data = { itens: [], contatos: [], total: 0, deslocamento: 0, tem_mais: false };
    if (p === '/v1/auth/me') data = { id: 7, nome: 'Teste', perfil: 'operador', permissoes: ['leads.view', 'leads.manage'], organizacao: { id: 1 } };
    if (p === '/v1/admin/leads') data = { itens: [1, 2].map(i => ({ ...lead(i), status: status[i] })), total: 2, total_global: 2, pesquisas_total: 0, deslocamento: 0,
      por_status: {}, acoes: { gerenciar: true, arquivar: true, excluir_pesquisa: false }, tem_mais: false };
    await route.fulfill({ json: data });
  });
  return { errors, patches };
}
async function browserTest(fn) {
  const browser = await chromium.launch(process.env.PW_CHROME ? { executablePath: process.env.PW_CHROME } : {});
  try { await fn(browser); } finally { await browser.close(); }
}
const open = async page => { await page.goto('http://operations.test/admin/leads'); await page.locator('tr[data-lead-id="1"] .lead-status').waitFor(); };

test('descartar exige motivo, não envia sem ele e cancelar preserva o status', { timeout: 40000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const f = await fixture(page);
  await open(page);
  const select = page.locator('tr[data-lead-id="1"] .lead-status');
  await select.selectOption('descartado');
  await page.locator('#discard-dialog[open]').waitFor();
  assert.equal(await select.inputValue(), 'novo', 'status só muda depois da confirmação');
  assert.equal(f.patches.length, 0);
  assert.equal(await page.locator('#discard-motivo').evaluate(el => el === document.activeElement), true);
  if (shots) await page.screenshot({ path: path.join(shots, 'desktop-dialogo-vazio.png') });
  await page.locator('#confirm-discard').click();
  assert.match(await page.locator('#discard-message').innerText(), /Selecione o motivo da perda/);
  assert.equal(f.patches.length, 0);
  assert.equal(await page.locator('#discard-dialog').evaluate(el => el.open), true);
  await page.locator('#cancel-discard').click();
  assert.equal(await page.locator('#discard-dialog').evaluate(el => el.open), false);
  assert.equal(await select.inputValue(), 'novo');
  assert.equal(f.patches.length, 0);
  // Esc também cancela sem enviar nada.
  await select.selectOption('descartado');
  await page.locator('#discard-dialog[open]').waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#discard-dialog').evaluate(el => el.open), false);
  assert.equal(await select.inputValue(), 'novo');
  assert.equal(f.patches.length, 0);
  assert.deepEqual(f.errors, []);
}));

test('motivo informado envia status, motivo e detalhe e confirma ao usuário', { timeout: 40000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const f = await fixture(page);
  await open(page);
  await page.locator('tr[data-lead-id="1"] .lead-status').selectOption('descartado');
  await page.locator('#discard-motivo').selectOption('concorrente');
  await page.locator('#discard-detalhe').fill('  Fechou com X  ');
  if (shots) await page.screenshot({ path: path.join(shots, 'desktop-dialogo-preenchido.png') });
  await page.locator('#confirm-discard').click();
  await page.locator('#discard-dialog:not([open])').waitFor({ state: 'attached' });
  assert.deepEqual(f.patches, [{ id: '1', body: { status: 'descartado', motivo_perda: 'concorrente', motivo_perda_detalhe: 'Fechou com X' } }]);
  // A confirmação só aparece depois do recarregamento da lista; o diálogo fecha antes.
  await page.locator('#admin-message', { hasText: 'Oportunidade descartada e motivo registrado' }).waitFor();
  assert.match(await page.locator('#admin-message').innerText(), /Oportunidade descartada e motivo registrado/);
  assert.equal(await page.locator('tr[data-lead-id="1"] .lead-status').inputValue(), 'descartado');
  assert.equal(await page.locator('tr[data-lead-id="2"] .lead-status').inputValue(), 'novo');
  if (shots) await page.screenshot({ path: path.join(shots, 'desktop-descartado.png') });
  assert.deepEqual(f.errors, []);
}));

test('erro do servidor aparece na janela e não perde o preenchimento; outros status seguem diretos', { timeout: 40000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const f = await fixture(page, { patchStatus: 422, patchDetail: 'Motivo de perda inválido' });
  await open(page);
  await page.locator('tr[data-lead-id="1"] .lead-status').selectOption('descartado');
  await page.locator('#discard-motivo').selectOption('outro');
  await page.locator('#confirm-discard').click();
  await page.locator('#discard-message', { hasText: 'Motivo de perda inválido' }).waitFor();
  assert.equal(await page.locator('#discard-dialog').evaluate(el => el.open), true);
  assert.equal(await page.locator('#discard-motivo').inputValue(), 'outro');
  assert.equal(await page.locator('#confirm-discard').isDisabled(), false);
  await page.locator('#cancel-discard').click();
  assert.equal(await page.locator('tr[data-lead-id="1"] .lead-status').inputValue(), 'novo');
  // Status que não é descarte continua sem janela e sem motivo.
  await page.locator('tr[data-lead-id="2"] .lead-status').selectOption('qualificado');
  await page.locator('#admin-message.error').waitFor();
  assert.equal(await page.locator('#discard-dialog').evaluate(el => el.open), false);
  assert.deepEqual(f.patches.at(-1), { id: '2', body: { status: 'qualificado' } });
}));

test('celular 390px: janela de descarte cabe na tela e é utilizável', { timeout: 40000 }, async () => browserTest(async browser => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const f = await fixture(page);
  await open(page);
  await page.locator('tr[data-lead-id="1"] .lead-status').selectOption('descartado');
  await page.locator('#discard-dialog[open]').waitFor();
  const box = await page.locator('#discard-dialog').boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844, JSON.stringify(box));
  for (const sel of ['#discard-motivo', '#discard-detalhe', '#cancel-discard', '#confirm-discard']) {
    const b = await page.locator(sel).boundingBox();
    assert.ok(b.x >= box.x && b.x + b.width <= box.x + box.width + 1, `${sel} cabe: ${JSON.stringify(b)}`);
  }
  await page.locator('#confirm-discard').click();
  if (shots) await page.screenshot({ path: path.join(shots, 'mobile-dialogo-erro.png') });
  await page.locator('#discard-motivo').selectOption('preco');
  await page.locator('#confirm-discard').click();
  await page.locator('#discard-dialog:not([open])').waitFor({ state: 'attached' });
  assert.equal(f.patches[0].body.motivo_perda, 'preco');
  if (shots) await page.screenshot({ path: path.join(shots, 'mobile-descartado.png') });
  assert.deepEqual(f.errors, []);
}));

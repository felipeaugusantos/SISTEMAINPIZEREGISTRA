// Entrega 2 (plano de correções 17/11/2026): R11 falha acionável no banner
// de avisos e no Zezinho (pergunta recuperável); R17 rótulo de MFA reflete
// o estado real da conta.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');

async function browserTest(fn) { const browser = await chromium.launch(); try { await fn(browser); } finally { await browser.close(); } }

function rotasBase(page, handlers = {}, { shell = false } = {}) {
  page.setDefaultTimeout(7000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  return page.route('**/*', async route => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    for (const [match, handler] of handlers) {
      if ((typeof match === 'string' && p === match) || (match instanceof RegExp && match.test(p))) return handler(route, req, u);
    }
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) {
      const file = p.includes('assistente') ? 'admin-assistente.html' : p.includes('confiabilidade') ? 'admin-confiabilidade.html' : 'admin-leads.html';
      let html = read(file);
      if (!shell) html = html.replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '');
      return route.fulfill({ contentType: 'text/html', body: html });
    }
    if (p === '/v1/auth/me') return route.fulfill({ json: { id: 7, nome: 'Teste', perfil: 'operador', permissoes: ['leads.view'], organizacao: { id: 1 }, mfa_ativo: false } });
    await route.fulfill({ json: { itens: [], contatos: [], total: 0, novidades: [], lidos: [], adiados: [] } });
  }).then(() => errors);
}

test('R11: falha de rede ao dispensar aviso reativa o botão e mostra mensagem', () => browserTest(async browser => {
  const page = await browser.newPage();
  const errors = await rotasBase(page, [
    ['/v1/admin/atualizacoes', route => route.fulfill({ json: { novidades: [
      { id: 'n1', versao: 'v1.0.1', titulo: 'Aviso', impacto_usuario: 'Impacto', classificacao: 'comum', leitura_obrigatoria: false, pode_adiar: true, estado: {} },
    ], lidos: [], adiados: [] } })],
    [/\/adiar$/, route => route.abort()],
  ], { shell: true });
  await page.goto('http://operations.test/admin/pesquisas');
  await page.locator('[data-toggle-avisos]').click();
  const botao = page.locator('[data-adiar-atualizacao]');
  await botao.click();
  await page.waitForFunction(() => /tente novamente/i.test(document.querySelector('.admin-avisos-erro')?.textContent || ''));
  assert.equal(await botao.isDisabled(), false);
  assert.equal(await page.locator('[data-atualizacao-id="n1"]').count(), 1);
  assert.deepEqual(errors, []);
}));

test('R11: Zezinho restaura a pergunta e não duplica a bolha em falha', () => browserTest(async browser => {
  const page = await browser.newPage();
  const errors = await rotasBase(page, [
    ['/v1/admin/assistente/perguntar', route => route.fulfill({ status: 500, json: { detail: 'Indisponível' } })],
  ]);
  await page.goto('http://operations.test/admin/assistente');
  await page.locator('#assistente-input').fill('Qual a situação da marca X?');
  await page.locator('#assistente-form button').click();
  await page.waitForFunction(() => document.querySelector('#assistente-message').textContent.includes('Indisponível'));
  assert.equal(await page.locator('#assistente-input').inputValue(), 'Qual a situação da marca X?');
  assert.equal(await page.locator('.assistente-msg-user').count(), 0);
  assert.equal(await page.locator('#assistente-input').isDisabled(), false);
  assert.deepEqual(errors, []);
}));

const confiabilidadeMock = {
  uso: { usuarios: 3, pesquisas: 12, leads: 8 }, fila: { status: 'ok', pendentes: 0 },
  organizacao: { nome: 'Org Teste', status: 'ativa', assinatura_status: 'ativa', retencao_dados_dias: 365, branding: {} },
  retencao: { matriz: [] }, permissoes: {},
};

test('R17: rótulo do MFA reflete o estado real da conta', () => browserTest(async browser => {
  const page = await browser.newPage();
  const errors = await rotasBase(page, [
    ['/v1/auth/me', route => route.fulfill({ json: { id: 7, nome: 'Teste', perfil: 'operador', permissoes: [], organizacao: { id: 1 }, mfa_ativo: true } })],
    ['/v1/admin/confiabilidade', route => route.fulfill({ json: confiabilidadeMock })],
    ['/v1/auth/social/identities/me', route => route.fulfill({ json: { providers: [
      { nome: 'Google', linked: false, enabled: true }, { nome: 'Apple', linked: false, enabled: false },
    ] } })],
    ['/v1/admin/politicas-privacidade', route => route.fulfill({ json: {
      vigente: null, consentimentos_pendentes: 0, regra_novo_consentimento: 'Aceite exigido apenas na próxima interação pública.', politicas: [],
    } })],
  ]);
  await page.goto('http://operations.test/admin/confiabilidade');
  await page.waitForFunction(() => document.querySelector('#enable-mfa').textContent.includes('Reconfigurar'));
  assert.match(await page.locator('#mfa-status').textContent(), /MFA ativo nesta conta/);
  assert.deepEqual(errors, []);
}));

test('R17: estado do MFA indisponível é distinguido de inativo', () => browserTest(async browser => {
  const page = await browser.newPage();
  const errors = await rotasBase(page, [
    ['/v1/auth/me', route => route.fulfill({ status: 500, json: {} })],
    ['/v1/admin/confiabilidade', route => route.fulfill({ json: confiabilidadeMock })],
    ['/v1/auth/social/identities/me', route => route.fulfill({ json: { providers: [
      { nome: 'Google', linked: false, enabled: true }, { nome: 'Apple', linked: false, enabled: false },
    ] } })],
    ['/v1/admin/politicas-privacidade', route => route.fulfill({ json: {
      vigente: null, consentimentos_pendentes: 0, regra_novo_consentimento: 'Aceite exigido apenas na próxima interação pública.', politicas: [],
    } })],
  ]);
  await page.goto('http://operations.test/admin/confiabilidade');
  await page.waitForFunction(() => document.querySelector('#mfa-status').textContent.includes('indisponível'));
  assert.deepEqual(errors, []);
}));


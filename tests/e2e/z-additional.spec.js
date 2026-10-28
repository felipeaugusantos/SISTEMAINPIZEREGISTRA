const fs = require('node:fs');
const { test, expect } = require('@playwright/test');
let context, page;
test.beforeEach(async ({ browser, baseURL }) => {
  context = await browser.newContext({ baseURL, storageState: JSON.parse(fs.readFileSync('test-results/e2e-admin-state.json', 'utf8')) });
  page = await context.newPage();
});
test.afterEach(async () => { await context?.close(); });

test('Scripts: falha real de rede mantém modal, conteúdo e erro acessível para nova tentativa', async () => {
  await page.goto('/admin/configuracao/script-atendimento');
  await page.locator('#new-script').click();
  const form = page.locator('#script-form');
  const titulo = `Roteiro recuperavel ${Date.now()}`;
  await form.locator('[name=titulo]').fill(titulo);
  await form.locator('[name=corpo]').fill('Conteudo de teste que deve ser preservado.');
  await context.setOffline(true);
  await form.locator('button[type=submit]').click();
  await expect(page.locator('#script-dialog')).toBeVisible();
  await expect(page.locator('#script-dialog .status-message.error')).toBeVisible();
  await expect(form.locator('[name=titulo]')).toHaveValue(titulo);
  await context.setOffline(false);
  await form.locator('button[type=submit]').click();
  await expect(page.locator('#script-dialog')).not.toBeVisible();
  await page.reload();
  await expect(page.locator('#script-list')).toContainText(titulo);
});

for (const [name, route, open, formId, dialog, endpoint] of [
  ['Categoria', '/admin/financeiro/categorias', '#new-category', '#category-form', '#category-dialog', '/v1/admin/financeiro/categorias'],
  ['Forma de pagamento', '/admin/financeiro/formas-pagamento', '#new-payment-method', '#payment-method-form', '#payment-method-dialog', '/v1/admin/financeiro/formas-pagamento'],
]) test(`${name}: salvar repetidamente envia uma operação e persiste um cadastro`, async () => {
  let posts = 0;
  // Somente adiciona latência. A resposta vem do backend e do banco reais.
  await page.route(`**${endpoint}`, async route => {
    if (route.request().method() === 'POST') {
      posts++;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    await route.continue();
  });
  await page.goto(route);
  await page.locator(open).click();
  const form = page.locator(formId), nome = `${name} E2E ${Date.now()}`;
  await form.locator('[name=nome]').fill(nome);
  const submit = form.locator('button[type=submit]');
  await submit.click();
  await submit.dispatchEvent('click');
  await expect(page.locator(dialog)).not.toBeVisible();
  await expect.poll(() => posts).toBe(1);
  const saved = await (await context.request.get(endpoint)).json();
  expect(saved.itens.filter(item => item.nome === nome)).toHaveLength(1);
  await page.reload();
  await expect(page.getByRole('heading', { name: nome, exact: true })).toBeVisible();
});

test('Produção e auditoria carrega sem erro de elementos removidos', async () => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/admin/producao');
  await expect(page.locator('#production-environment')).not.toHaveText('Carregando…');
  await expect(page.locator('#audit-list tr').first()).toBeVisible();
  await page.waitForTimeout(200);
  expect(errors).toEqual([]);
});

test('Financeiro: filtros funcionam e permanecem dentro da tela nas três visões', async () => {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ['/admin/financeiro', '/admin/financeiro/contas-a-pagar', '/admin/financeiro/contas-a-receber']) {
      await page.goto(route);
      const filter = page.locator('#finance-filter');
      await filter.locator('[name=busca]').fill('sem-resultados-e2e');
      const response = page.waitForResponse(r => r.url().includes('/v1/admin/financeiro?') && r.url().includes('sem-resultados-e2e'));
      await filter.locator('button[type=submit]').click();
      expect((await response).ok()).toBeTruthy();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBeTruthy();
      await page.locator('#finance-clear').click();
      await expect(filter.locator('[name=busca]')).toHaveValue('');
    }
  }
});

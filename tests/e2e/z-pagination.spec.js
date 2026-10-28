const fs = require('node:fs');
const { test, expect } = require('@playwright/test');
let context, page;
test.beforeEach(async ({ browser, baseURL }) => {
  context = await browser.newContext({ baseURL, storageState: JSON.parse(fs.readFileSync('test-results/e2e-admin-state.json', 'utf8')) });
  page = await context.newPage();
});
test.afterEach(async () => { await context.close(); });
async function idle(id) {
  await expect.poll(() => page.locator(`#${id}-page-number`).evaluate(input => input.closest('.admin-pagination').getAttribute('aria-busy'))).toBe('false');
}
test('Radar: navegação direta e tamanho consultam o banco preservando filtros', async () => {
  await page.goto('/admin/prospeccao');
  await expect(page.locator('#prospeccao-page-count')).toHaveText('de 3');
  await page.locator('#prospeccao-last').click(); await idle('prospeccao');
  await expect(page.locator('#prospeccao-page-number')).toHaveValue('3');
  await expect(page.locator('#prospeccao-list [data-id]')).toHaveCount(15);
  await page.locator('#prospeccao-page-number').fill('2'); await page.locator('#prospeccao-go').click(); await idle('prospeccao');
  await expect(page.locator('#prospeccao-list [data-id]')).toHaveCount(20);
  await page.locator('#prospeccao-page-size').selectOption('50'); await idle('prospeccao');
  await expect(page.locator('#prospeccao-page-number')).toHaveValue('1');
  await expect(page.locator('#prospeccao-list [data-id]')).toHaveCount(50);
  await page.locator('#prospeccao-filter [name=busca]').fill('Prospect paginacao');
  await page.locator('#prospeccao-filter button[type=submit]').click();
  await expect(page.locator('#prospeccao-page-count')).toHaveText('de 2');
  const response = page.waitForResponse(r => r.url().includes('/v1/admin/prospects?') && r.url().includes('deslocamento=50'));
  await page.locator('#prospeccao-next').click();
  const result = await response;
  expect(result.ok()).toBeTruthy(); expect(new URL(result.url()).searchParams.get('busca')).toBe('Prospect paginacao');
  await idle('prospeccao'); await expect(page.locator('#prospeccao-list [data-id]')).toHaveCount(5);
});
test('Financeiro: páginas distintas, total sem duplicar parcelas e filtros reais', async () => {
  await page.goto('/admin/financeiro/contas-a-pagar');
  await page.locator('#finance-filter [name=busca]').fill('Paginacao financeira E2E');
  await page.locator('#finance-filter button[type=submit]').click();
  await page.locator('#finance-page-size').selectOption('20'); await idle('finance');
  const first = await page.locator('.finance-entry').first().getAttribute('data-entry');
  await expect(page.locator('#finance-page-count')).toHaveText('de 3');
  await expect(page.locator('.finance-entry')).toHaveCount(20);
  await page.locator('#finance-last').click(); await idle('finance');
  await expect(page.locator('.finance-entry')).toHaveCount(15);
  expect(await page.locator('.finance-entry').first().getAttribute('data-entry')).not.toBe(first);
  const response = await context.request.get('/v1/admin/financeiro?tipo=pagar&busca=Paginacao%20financeira%20E2E&limite=20&deslocamento=40');
  expect(response.ok()).toBeTruthy(); const data = await response.json();
  expect(data.total).toBe(55); expect(data.itens).toHaveLength(15);
  await page.locator('#finance-clear').click(); await expect(page.locator('#finance-page-number')).toHaveValue('1');
});
test('Seletores carregam nos demais módulos e respeitam limites sem erros JavaScript', async () => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  for (const [url, ids] of [
    ['/admin/prospeccao', ['supressoes']], ['/admin/crm', ['crm', 'crm-reminders']],
    ['/admin/processos-monitorados', ['portfolio']], ['/admin/operacao-juridica', ['legal']],
    ['/admin/producao', ['audit']], ['/admin/observabilidade', ['events']],
    ['/admin/financeiro/conciliacao', ['transacoes']],
    ['/admin/atualizacoes', ['updates-funcionalidade', 'updates-correcao', 'updates-melhoria']],
    ['/admin/configuracao/consulta-rpi', ['rpi-revista']],
  ]) {
    await page.goto(url);
    for (const id of ids) {
      await expect(page.locator(`#${id}-page-size`)).toBeVisible();
      const options = await page.locator(`#${id}-page-size option`).evaluateAll(options => options.map(option => option.value));
      const current = await page.locator(`#${id}-page-size`).inputValue();
      const alternative = options.find(value => value !== current);
      await page.locator(`#${id}-page-size`).selectOption(alternative); await idle(id);
      await expect(page.locator(`#${id}-page-number`)).toHaveValue('1');
      await expect(page.locator(`#${id}-page-size`)).toHaveValue(alternative);
      await expect(page.locator(`#${id}-go`)).toBeEnabled();
    }
  }
  expect(errors).toEqual([]);
});

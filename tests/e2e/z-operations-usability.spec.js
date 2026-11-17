const fs = require('node:fs');
const { test, expect } = require('@playwright/test');

// Real authenticated API and ephemeral PostgreSQL from CI. No route mocks,
// production credentials, external messages, or production records.
let context, page, headers;
test.beforeAll(async ({ browser, baseURL }) => {
  const storageState = JSON.parse(fs.readFileSync('test-results/e2e-admin-state.json', 'utf8'));
  context = await browser.newContext({ baseURL, storageState });
  headers = { 'X-CSRF-Token': storageState.cookies.find(c => c.name === 'zr_csrf').value };
  page = await context.newPage();
});
test.afterAll(async () => { await context?.close(); });
async function post(url, data) {
  const response = await context.request.post(url, { headers, data });
  expect(response.ok(), `${url}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

test('Centro: visões salvas usam identidade real e filtros sobrevivem à recarga', async () => {
  await page.goto('/admin/pesquisas');
  await expect(page.locator('[data-view-action=save]')).toBeEnabled();
  await page.locator('#lead-status-filter').selectOption('novo');
  const name = `Rotina E2E ${Date.now()}`;
  await page.locator('[data-view-name]').fill(name);
  await page.locator('[data-view-action=save]').click();
  await expect(page.locator('[data-views-status]')).toContainText('Visão salva');
  await page.locator('#clear-lead-filters').click();
  const filtered = page.waitForResponse(r => r.url().includes('/v1/admin/leads?') && new URL(r.url()).searchParams.get('status') === 'novo');
  await page.locator('[data-view-action=apply]').click();
  expect((await filtered).ok()).toBeTruthy();
  await page.reload(); await expect(page.locator('#lead-status-filter')).toHaveValue('novo');
  await page.locator('[data-views-list]').selectOption({ label: name });
  await page.locator('[data-view-action=delete]').click();
  await expect(page.locator('[data-views-status]')).toContainText('excluída');
});

test('Centro: classes selecionadas pela interface persistem no banco e retorno mantém contexto', async () => {
  const stamp = Date.now(); const email = `classes-centro-${stamp}@e2e.zeregistra.test`;
  await page.goto('/admin/consulta');
  await expect(page.locator('.nice-options input')).toHaveCount(45);
  await page.locator('[data-field=marca]').fill(`MARCA CENTRO E2E ${stamp}`);
  await page.locator('[name=nome]').fill(`Cliente Centro E2E ${stamp}`);
  await page.locator('[name=email]').fill(email);
  await page.locator('.nice-selector input[type=search]').fill('30');
  await page.locator('.nice-options input[value="30"]').check();
  await page.locator('.nice-selector input[type=search]').fill('35');
  await page.locator('.nice-options input[value="35"]').check();
  const created = page.waitForResponse(r => new URL(r.url()).pathname === '/v1/admin/consulta' && r.request().method() === 'POST');
  await page.locator('#consulta-form button[type=submit]').click();
  // R08: a gravação exige a etapa de revisão fora do formulário; o POST só
  // dispara após confirmar o resumo.
  await expect(page.locator('#consulta-revisao')).toBeVisible();
  await page.locator('#confirmar-consulta').click();
  const response = await created; expect(response.ok()).toBeTruthy(); const data = await response.json();
  expect(data.itens.map(item => item.classe_nice).sort()).toEqual(['30', '35']);
  const detail = await (await context.request.get(`/v1/admin/leads/${data.lead_id}`)).json();
  expect(detail.pesquisas.map(item => item.classe_nice).sort()).toEqual(['30', '35']);
  await page.goto('/admin/pesquisas'); await page.locator('#lead-search').fill(email);
  await page.locator('#lead-filters').evaluate(form => form.requestSubmit());
  await expect(page.locator(`#leads-list tr[data-lead-id="${data.lead_id}"][data-research-id]`)).toHaveCount(2);
  await page.locator(`#leads-list tr[data-research-id="${data.itens[0].id}"] .view-lead`).click();
  await expect(page.locator('.lead-operational-summary')).toBeVisible();
  await page.locator('#lead-dialog [data-tab=atendimento]').click();
  await page.locator('#lead-dialog a[href*="/admin/analises/"]').first().click();
  await expect(page.locator('#analysis-brand')).not.toHaveText('Carregando pesquisa…');
  await page.locator('.analysis-breadcrumb a').click();
  await expect(page.locator('#lead-dialog')).toBeVisible();
  await expect(page.locator('#lead-dialog .lead-tab[aria-selected=true]')).toHaveAttribute('data-tab', 'atendimento');
  await expect(page.locator('#lead-search')).toHaveValue(email);
  await page.keyboard.press('Escape');
});

test('Centro: busca e movimentação reais preservam pesquisa e histórico dos leads', async () => {
  const stamp = Date.now();
  const source = await post('/v1/admin/consulta', { nome: `Origem Centro ${stamp}`, email: `origem-${stamp}@e2e.zeregistra.test`, marca: `Marca Mover Centro ${stamp}`, classes_nice: ['35'] });
  const targetName = `Destino Centro ${stamp}`;
  const targetEmail = `destino-${stamp}@e2e.zeregistra.test`;
  await post('/v1/leads', { nome: targetName, email: targetEmail, telefone: '(16) 99999-8888', marca: 'Interesse sintético E2E', origem: 'geral', aceite_privacidade: true });
  const targetList = await (await context.request.get(`/v1/admin/leads?busca=${encodeURIComponent(targetEmail)}`)).json();
  const target = targetList.itens[0]; expect(target.id).not.toBe(source.lead_id);
  await page.goto(`/admin/pesquisas?lead_id=${source.lead_id}`);
  await expect(page.locator('#lead-dialog')).toBeVisible();
  await page.locator('#lead-dialog [data-tab=atendimento]').click();
  await page.locator('.move-research').first().click();
  await page.locator('#research-move-search').fill(targetName);
  await page.locator('#research-move-results button').filter({ hasText: targetName }).click();
  page.once('dialog', dialog => dialog.accept());
  const moved = page.waitForResponse(r => r.url().endsWith('/mover-lead') && r.request().method() === 'POST');
  await page.locator('#research-move-form button[type=submit]').click();
  expect((await moved).ok()).toBeTruthy();
  await expect(page.locator('#research-move-dialog')).not.toBeVisible();
  const original = await (await context.request.get(`/v1/admin/leads/${source.lead_id}`)).json();
  const destination = await (await context.request.get(`/v1/admin/leads/${target.id}`)).json();
  expect(original.pesquisas.some(item => item.id === source.itens[0].id)).toBeFalsy();
  expect(destination.pesquisas.some(item => item.id === source.itens[0].id)).toBeTruthy();
});

const fs = require('node:fs');
const { test, expect } = require('@playwright/test');

// Executado depois de smoke.spec.js, que estabelece a sessão pelo login e MFA reais.
// Não intercepta APIs. Escritas usam exclusivamente o banco efêmero de E2E.
let context, page, headers;
test.beforeAll(async ({ browser, baseURL }) => {
  const storageState = JSON.parse(fs.readFileSync('test-results/e2e-admin-state.json', 'utf8'));
  context = await browser.newContext({ baseURL, storageState });
  page = await context.newPage();
  headers = { 'X-CSRF-Token': storageState.cookies.find(c => c.name === 'zr_csrf').value };
});
test.afterAll(async () => { await context?.close(); });
async function post(url, data) {
  const response = await context.request.post(url, { headers, data });
  expect(response.ok(), `${url}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

test('Pesquisas: requisições simultâneas e reenvio mantêm uma pesquisa por classe', async () => {
  const email = `pesquisa-concorrente-${Date.now()}@e2e.zeregistra.test`;
  const data = { nome: 'Cliente Concorrencia E2E', email, marca: 'NORTE STUDIO E2E', classes_nice: ['25'] };
  const inicial = await post('/v1/admin/consulta', data);
  const classe35 = { ...data, classes_nice: ['35'] };
  const [primeira, segunda] = await Promise.all([
    post('/v1/admin/consulta', classe35), post('/v1/admin/consulta', classe35),
  ]);
  expect(primeira.lead_id).toBe(inicial.lead_id);
  expect(segunda.itens[0].id).toBe(primeira.itens[0].id);
  expect([primeira.itens[0].duplicada, segunda.itens[0].duplicada].sort()).toEqual([false, true]);
  expect(primeira.itens[0].id).not.toBe(inicial.itens[0].id);
  const reenvio = await post('/v1/admin/consulta', data);
  expect(reenvio.itens[0].id).toBe(inicial.itens[0].id);
  expect(reenvio.itens[0].duplicada).toBe(true);
  const lista = await (await context.request.get(`/v1/admin/leads?busca=${email}`)).json();
  const lead = lista.itens.find(item => item.id === inicial.lead_id);
  expect(lead.total_pesquisas).toBe(2);
  expect(lead.pesquisas.map(item => item.id).sort()).toEqual([inicial.itens[0].id, primeira.itens[0].id].sort());
  expect(lead.pesquisas.every(item => !item.duplicada)).toBe(true);
  const detalhe = await (await context.request.get(`/v1/admin/leads/${inicial.lead_id}`)).json();
  expect(detalhe.total_pesquisas).toBe(2);
  await page.goto('/admin/pesquisas');
  await page.locator('#lead-search').fill(email);
  await page.locator('#lead-filters').evaluate(form => form.requestSubmit());
  const linhas = page.locator(`#leads-list tr[data-lead-id="${inicial.lead_id}"][data-research-id]`);
  await expect(linhas).toHaveCount(2);
  await expect(page.locator(`#leads-list tr[data-research-id="${inicial.itens[0].id}"]`)).toBeVisible();
  await expect(page.locator(`#leads-list tr[data-research-id="${primeira.itens[0].id}"]`)).toBeVisible();
});

test('CRM: pesquisar, selecionar e salvar lembrete persiste no backend', async () => {
  const email = `funcional-${Date.now()}@e2e.zeregistra.test`;
  await post('/v1/leads', { nome: 'Cliente Funcional E2E', email, telefone: '(16) 99999-0000', marca: 'Marca Funcional E2E', origem: 'geral', aceite_privacidade: true });
  const leads = await (await context.request.get(`/v1/admin/leads?busca=${email}`)).json();
  const id = leads.itens[0].id;
  await page.goto('/admin/crm');
  await page.locator('#new-reminder').click();
  await page.locator('#crm-client-search').fill('Cliente Funcional E2E');
  await page.locator(`#crm-client-results [data-lead-id="${id}"]`).click();
  const form = page.locator('#reminder-form');
  await form.locator('[name=titulo]').fill('Retorno funcional E2E');
  await form.locator('button[type=submit]').click();
  await expect(page.locator('#reminder-dialog')).not.toBeVisible();
  const saved = await (await context.request.get('/v1/admin/crm/lembretes')).json();
  expect(saved.itens.some(item => item.titulo === 'Retorno funcional E2E' && item.lead_id === id)).toBeTruthy();
});

test('NFS-e: busca real seleciona empresa e carrega títulos disponíveis', async () => {
  const nome = 'Empresa Homologacao E2E';
  const data = await (await context.request.get(`/v1/admin/financeiro/empresas/pesquisar?busca=${encodeURIComponent(nome)}`)).json();
  expect(data.itens).toHaveLength(1);
  const empresa = data.itens[0];
  await page.goto('/admin/financeiro/nfse');
  await page.locator('#new-nota').click();
  await page.locator('#nfse-client-search').fill(nome);
  const response = page.waitForResponse(r => r.url().includes(`/nfse/lancamentos-disponiveis?empresa_id=${empresa.id}`));
  await page.locator(`#nfse-client-results [data-client-id="${empresa.id}"]`).click();
  expect((await response).ok()).toBeTruthy();
  await expect(page.locator('#nfse-selected-client')).toContainText(nome);
});

test('OFX: importa, recarrega e mantém transação após nova leitura', async () => {
  const fitid = `e2e-${Date.now()}`;
  const ofx = `<OFX><BANKTRANLIST><STMTTRN><TRNTYPE>CREDIT\n<DTPOSTED>20261027120000\n<TRNAMT>123.45\n<FITID>${fitid}\n<MEMO>Importacao funcional E2E\n</STMTTRN></BANKTRANLIST></OFX>`;
  await page.goto('/admin/financeiro/conciliacao');
  await page.locator('#upload-form input[type=file]').setInputFiles({ name: 'funcional.ofx', mimeType: 'application/x-ofx', buffer: Buffer.from(ofx) });
  await page.locator('#upload-form button').click();
  await expect(page.locator('#conc-message')).toContainText('1 transação');
  await expect(page.locator('#conc-message')).not.toHaveClass(/error/);
  expect(await page.locator('#upload-form input').evaluate(el => el.files.length)).toBe(0);
  await page.reload();
  await expect(page.locator('#transacoes-tbody')).toContainText('Importacao funcional E2E');
  const persisted = await (await context.request.get('/v1/admin/financeiro/conciliacao')).json();
  expect(persisted.itens.some(item => item.descricao === 'Importacao funcional E2E')).toBeTruthy();
});

test('Atualizações: relato é confirmado e fica disponível em nova consulta', async () => {
  const versao = `e2e-${Date.now()}`;
  const release = await post('/v1/admin/versoes-sistema', {
    versao, titulo: 'Validação funcional E2E', problema_identificado: 'Validação de persistência real do relato de problemas.',
    solucao_aplicada: 'Preservação da referência do formulário durante a requisição.', impacto_usuario: 'Confirmação após salvar o relato.',
    tipo_atualizacao: 'correcao', modulos_afetados: ['plataforma'], implantada_em: new Date().toISOString(), commit_sha: 'f'.repeat(40),
    evidencias_testes: [{ nome: 'Homologação E2E', resultado: 'aprovado', resumo: 'Teste isolado com banco real.' }],
    instrucoes: 'Validação em banco efêmero e sem dados reais de clientes.', plano_rollback: 'Descartar o ambiente isolado depois da validação funcional.'
  });
  await post(`/v1/admin/versoes-sistema/${release.id}/publicar`, { confirmar_publicacao: true });
  await page.goto('/admin/atualizacoes');
  await page.locator(`[data-action=report][data-id="${release.id}"]`).click();
  const descricao = `Relato funcional com persistência real ${Date.now()}`;
  await page.locator('#updates-report-description').fill(descricao);
  await page.locator('#updates-report-form button[type=submit]').click();
  await expect(page.locator('#updates-report-dialog')).not.toBeVisible();
  await expect(page.locator('#updates-message')).toContainText('Problema reportado');
  await expect(page.locator('#updates-report-description')).toHaveValue('');
  const data = await (await context.request.get('/v1/admin/atualizacoes/problemas')).json();
  expect(data.itens.some(item => item.descricao === descricao)).toBeTruthy();
});

test('WhatsApp: revogação pelo formulário persiste e atualiza a tabela', async () => {
  const telefone = '5516999990042';
  await post('/v1/admin/mensageria/consentimentos', { finalidade: 'vigilancia', telefone, origem: 'teste_e2e', evidencia: { descricao: 'Cadastro sintético de homologação.' } });
  await page.goto('/admin/configuracao/whatsapp');
  const form = page.locator('#wa-form-revogar');
  await form.locator('[name=finalidade]').selectOption('vigilancia');
  await form.locator('[name=telefone]').fill(telefone);
  page.once('dialog', dialog => dialog.accept());
  await form.locator('button[type=submit]').click();
  await expect(page.locator('#wa-revogar-msg')).toHaveText('Telefone revogado.');
  await expect(form.locator('[name=telefone]')).toHaveValue('');
  const data = await (await context.request.get('/v1/admin/mensageria/consentimentos')).json();
  expect(data.some(item => item.telefone === telefone && item.status === 'revogado')).toBeTruthy();
});

test('Notificações: botões individual e em lote alteram o estado persistido', async () => {
  await page.goto('/admin/notificacoes');
  const data = await (await context.request.get('/v1/admin/notificacoes?todas=true&limite=200')).json();
  const item = data.itens.find(item => item.mensagem === 'Notificacao sintetica de homologacao E2E');
  expect(item).toBeTruthy();
  const button = page.locator(`.notif-toggle[data-id="${item.id}"][data-fonte=sistema]`);
  await button.click();
  await expect(button).toHaveCount(0);
  await page.locator('#notif-filter-todas').check();
  await expect(button).toHaveText('Marcar como não lida');
  let saved = await (await context.request.get('/v1/admin/notificacoes?todas=true&limite=200')).json();
  expect(saved.itens.find(x => x.id === item.id && x.fonte === 'sistema').lida).toBeTruthy();
  await page.locator('#notif-marcar-todas-nao-lidas').click();
  await expect(button).toHaveText('Marcar como lida');
  await page.locator('#notif-marcar-todas-lidas').click();
  await expect(button).toHaveText('Marcar como não lida');
  saved = await (await context.request.get('/v1/admin/notificacoes?todas=true&limite=200')).json();
  expect(saved.itens.find(x => x.id === item.id && x.fonte === 'sistema').lida).toBeTruthy();
});

test('Jurídico: simular e confirmar reprocessamento conclui a operação real', async () => {
  await page.goto('/admin/operacao-juridica');
  const select = page.locator('#legal-reprocess-preview [name=regra_id]');
  await select.selectOption({ label: await select.locator('option').filter({ hasText: 'Responder exigencia E2E' }).textContent() });
  await page.locator('#legal-reprocess-preview button').click();
  await expect(page.locator('#legal-reprocess-result')).toContainText('1 publicação');
  const form = page.locator('#legal-reprocess-confirm');
  await form.locator('[name=justificativa]').fill('Homologacao funcional em banco temporario com publicacao sintetica.');
  await form.locator('[name=confirmacao]').fill('REPROCESSAR');
  const response = page.waitForResponse(r => r.url().endsWith('/motor/reprocessamento') && r.request().method() === 'POST');
  await form.locator('button').click();
  const result = await response;
  expect(result.ok(), await result.text()).toBeTruthy();
  expect((await result.json()).avaliacoes_liberadas).toBe(1);
  await expect(form).not.toBeVisible();
  await expect(page.locator('#legal-reprocess-result')).not.toBeVisible();
  const regraId = await select.inputValue();
  const persisted = await (await context.request.get(`/v1/admin/juridico/motor/reprocessamento?regra_id=${regraId}`)).json();
  expect(persisted.quantidade).toBe(0);
});

test('menu e identidade continuam funcionais ao passar do desktop para celular', async () => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin');
  await page.locator('#admin-sidebar-toggle').click();
  await page.setViewportSize({ width: 390, height: 900 });
  await page.locator('.admin-menu-toggle').click();
  const link = page.locator('.admin-sidebar-nav a[data-submenu-parent=comercial][href="/admin/consulta"]');
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/admin\/consulta$/);
  const image = page.locator('.admin-sidebar-brand img');
  const user = await (await context.request.get('/v1/auth/me')).json();
  const officialHost = new URL(page.url()).hostname === 'app.zeregistra.com.br';
  const expectedImage = officialHost ? '/static/assets/personagem.png?v=1' : user.organizacao?.marca?.logo_url || '/static/assets/personagem.png?v=1';
  await expect(image).toHaveAttribute('src', expectedImage);
  expect(await image.evaluate(el => el.complete && el.naturalWidth > 0)).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('body')).toHaveClass(/admin-sidebar-collapsed/);
});

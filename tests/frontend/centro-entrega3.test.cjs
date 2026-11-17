// Entrega 3 (plano de correções 17/11/2026): R08 consulta com cliente
// existente + revisão, R09 próxima decisão e rótulos PT, R14 busca de
// processo/lead por identidade, R15 fonte/período dos indicadores.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');

async function browserTest(fn) { const browser = await chromium.launch(); try { await fn(browser); } finally { await browser.close(); } }

function rotas(page, handlers = []) {
  page.setDefaultTimeout(8000);
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  return page.route('**/*', async route => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    requests.push({ path: p, query: u.search, method: req.method(), body: req.postData() });
    for (const [match, handler] of handlers) {
      if ((typeof match === 'string' && p === match) || (match instanceof RegExp && match.test(p))) return handler(route, req, u);
    }
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) {
      const file = p.includes('assistente') ? 'admin-assistente.html' : p.includes('confiabilidade') ? 'admin-confiabilidade.html'
        : p.includes('consulta') ? 'admin-consulta.html' : p.includes('analises') ? 'admin-analise.html'
        : p.includes('lucratividade') ? 'admin-financeiro-lucratividade.html' : p.includes('producao') ? 'admin-producao.html' : 'admin-leads.html';
      const html = read(file).replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '');
      return route.fulfill({ contentType: 'text/html', body: html });
    }
    if (p === '/v1/auth/me') return route.fulfill({ json: { id: 7, nome: 'Teste', perfil: 'operador', permissoes: ['leads.view'], organizacao: { id: 1 }, mfa_ativo: false } });
    await route.fulfill({ json: { itens: [], contatos: [], total: 0 } });
  }).then(() => ({ errors, requests }));
}

test('R08: revisão resume combinações antes de pesquisar e confirma envia', () => browserTest(async browser => {
  const page = await browser.newPage();
  const { errors, requests } = await rotas(page, [
    ['/v1/admin/consulta/classes-nice', route => route.fulfill({ json: [{ codigo: '30', titulo: 'Café' }] })],
    ['/v1/admin/consulta', (route, req) => {
      if (req.method() === 'POST') return route.fulfill({ json: { itens: [{ id: 'r1', marca: 'CAFÉ TESTE', relatorio_url: '/admin/consulta/r1' }], lead_id: 1 } });
      return route.fulfill({ json: {} });
    }],
    [/\/v1\/admin\/consulta\/[^/]+$/, route => route.fulfill({ json: { pesquisa: { id: 'r1', marca: 'CAFÉ TESTE' }, relatorio: {} } })],
    [/\/v1\/admin\/leads\/\d+\/contatos$/, route => route.fulfill({ json: { contatos: [], total: 0 } })],
  ]);
  await page.goto('http://operations.test/admin/consulta');
  await page.locator('[data-field="marca"]').fill('CAFÉ TESTE');
  await page.locator('[name="nome"]').fill('Cliente Teste');
  await page.locator('[name="email"]').fill('cliente@example.com');
  await page.locator('#consulta-form button.form-submit').first().click();
  await page.locator('#consulta-revisao:not([hidden])').waitFor();
  assert.match(await page.locator('#consulta-revisao-resumo').textContent(), /todas as classes \(1 consulta\)/);
  assert.equal(requests.filter(r => r.path === '/v1/admin/consulta' && r.method === 'POST').length, 0);
  assert.equal(await page.locator('#consulta-form').isHidden(), true, 'revisão é etapa única: formulário original some');
  await page.locator('#voltar-consulta').click();
  assert.equal(await page.locator('#consulta-form').isVisible(), true, 'voltar reexibe o formulário para ajuste');
  assert.equal(requests.filter(r => r.path === '/v1/admin/consulta' && r.method === 'POST').length, 0);
  await page.locator('#consulta-form button.form-submit').first().click();
  await page.locator('#consulta-revisao:not([hidden])').waitFor();
  await page.locator('#confirmar-consulta').click();
  await page.waitForFunction(() => true);
  assert.equal(requests.filter(r => r.path === '/v1/admin/consulta' && r.method === 'POST').length, 1);
  assert.deepEqual(errors, []);
}));

test('R08: combinação repetida bloqueia com mensagem clara', () => browserTest(async browser => {
  const page = await browser.newPage();
  await rotas(page, [
    ['/v1/admin/consulta/classes-nice', route => route.fulfill({ json: [{ codigo: '30', titulo: 'Café' }] })],
  ]);
  await page.goto('http://operations.test/admin/consulta');
  await page.locator('#adicionar-marca').click();
  await page.locator('.consulta-marca-item [data-field="marca"]').first().fill('CAFÉ TESTE');
  await page.locator('.consulta-marca-item [data-field="marca"]').nth(1).fill('café teste');
  await page.locator('[name="nome"]').fill('Cliente Teste');
  await page.locator('[name="email"]').fill('cliente@example.com');
  await page.locator('#consulta-form button.form-submit').first().click();
  await page.waitForFunction(() => document.querySelector('#consulta-marcas-status')?.textContent.includes('repete'));
  assert.equal(await page.locator('#consulta-revisao:not([hidden])').count(), 0);
}));

test('R08: busca de cliente existente preenche os dados com escolha explícita', () => browserTest(async browser => {
  const page = await browser.newPage();
  await rotas(page, [
    ['/v1/admin/consulta/classes-nice', route => route.fulfill({ json: [] })],
    ['/v1/admin/leads', (route, req, u) => route.fulfill({ json: { itens: u.search.includes('busca=') ? [
      { id: 5, nome: 'Cliente Antigo', empresa: 'Padaria Ltda', email: 'antigo@example.com', telefone: '16999990000', status: 'novo', tags: [] },
    ] : [], total: 1 } })],
  ]);
  await page.goto('http://operations.test/admin/consulta');
  await page.locator('#cliente-busca').fill('Padaria');
  await page.locator('#cliente-busca-resultados button').first().waitFor();
  await page.locator('#cliente-busca-resultados button').first().click();
  assert.equal(await page.locator('[name="nome"]').inputValue(), 'Cliente Antigo');
  assert.equal(await page.locator('[name="email"]').inputValue(), 'antigo@example.com');
  assert.match(await page.locator('#cliente-selecionado').textContent(), /Cliente selecionado/);
}));

test('R09: estado do workflow em português e próxima ação visível', () => browserTest(async browser => {
  const page = await browser.newPage();
  await rotas(page, [
    [/\/v1\/admin\/analises\//, route => route.fulfill({ json: {
      pesquisa: { id: 'r1', marca: 'Marca 1', criado_em: '2026-01-01T00:00:00Z', relatorio_token: 't' },
      lead: null, permissoes: { validacao_visualizar: true, risco_visualizar: true }, usuario: {},
      relatorio_completo: { gerado: false, base_disponivel: true },
      workflow: { state: 'PENDING_REVIEW', history: [], report_version: 1 },
      analise_consolidada: {
        versao_relatorio: 3, versao_motor: 'motor v1', revisao: { validada: false },
        conclusao_preliminar: {}, apresentacao: { situacao: { codigo: 'inconclusiva', rotulo: 'Inconclusiva', explicacao: 'A pesquisa automática encontrou anterioridades que exigem revisão humana antes do parecer.' } },
        pendencias: [{ criterio: 'Colidência', descricao: 'Revisar a marca anterior CAFÉ EXEMPLO (processo 900000000000000), classe 30.' }],
        analise_conjunto: { marca: 'Marca 1' }, matriz: null, anterioridades: [], fontes: {}, disclaimers: [],
        estatistica: { disponivel: false }, titulo: 'Análise de registrabilidade da marca Marca 1',
        recomendacao: 'Resolver a colidência apontada antes de protocolar o pedido.', diretriz_acao: null, legado: false,
      },
    } })],
  ]);
  await page.goto('http://operations.test/admin/analises/r1');
  await page.waitForFunction(() => document.body.textContent.includes('Revisão pendente'));
  assert.match(await page.locator('.analysis-next').textContent(), /pendência/);
  assert.ok(!(await page.locator('.analysis-grid').first().textContent()).includes('PENDING_REVIEW'));
}));

test('R14: busca de processo por marca preenche o ID com escolha explícita', () => browserTest(async browser => {
  const page = await browser.newPage();
  await rotas(page, [
    ['/v1/admin/carteira', route => route.fulfill({ json: { itens: [
      { id: 9, processo_id: 42, numero: '900000000000000', titulo: 'CAFÉ TESTE', titulo_exibicao: 'CAFÉ TESTE' },
    ], total: 1 } })],
  ]);
  await page.goto('http://operations.test/admin/financeiro/lucratividade');
  await page.locator('#processo-busca').fill('CAFÉ');
  await page.locator('#processo-busca-resultados button').first().waitFor();
  await page.locator('#processo-busca-resultados button').first().click();
  assert.equal(await page.locator('[name="processo_id"]').inputValue(), '42');
  assert.match(await page.locator('#processo-selecionado').textContent(), /Processo selecionado/);
}));

test('R14: legal hold localiza lead sem exigir ID de memória', () => browserTest(async browser => {
  const page = await browser.newPage();
  const { errors } = await rotas(page, [
    ['/v1/admin/confiabilidade', route => route.fulfill({ json: {
      uso: { usuarios: 3, pesquisas: 12, leads: 8 }, fila: { status: 'ok', pendentes: 0 },
      organizacao: { nome: 'Org Teste', status: 'ativa', assinatura_status: 'ativa', retencao_dados_dias: 365, branding: {} },
      retencao: { matriz: [] }, permissoes: {},
    } })],
    ['/v1/auth/social/identities/me', route => route.fulfill({ json: { providers: [
      { nome: 'Google', linked: false, enabled: true }, { nome: 'Apple', linked: false, enabled: false },
    ] } })],
    ['/v1/admin/politicas-privacidade', route => route.fulfill({ json: {
      vigente: null, consentimentos_pendentes: 0, regra_novo_consentimento: 'Aceite exigido apenas na próxima interação pública.', politicas: [],
    } })],
    ['/v1/admin/leads', route => route.fulfill({ json: { itens: [
      { id: 11, nome: 'Lead Bloqueio', empresa: 'Empresa X', email: 'x@example.com', telefone: '', status: 'novo', tags: [] },
    ], total: 1 } })],
  ]);
  await page.goto('http://operations.test/admin/confiabilidade');
  await page.locator('#legal-hold-busca').fill('Bloqueio');
  await page.locator('#legal-hold-busca-resultados button').first().waitFor();
  await page.locator('#legal-hold-busca-resultados button').first().click();
  assert.equal(await page.locator('#legal-hold-form [name="lead_id"]').inputValue(), '11');
  assert.deepEqual(errors, []);
}));

test('R15: indicadores indicam fonte/período e distinguem sem amostra', () => browserTest(async browser => {
  const page = await browser.newPage();
  await rotas(page, [
    ['/v1/admin/producao', route => route.fulfill({ json: {
      ambiente: 'produção', requisicoes_24h: 0, taxa_erros_24h: 0, duracao_media_ms_24h: 0, taxa_divergencia: 0,
      versoes_relatorio: 0, pesquisas_versionadas: 0, auditoria: [], auditoria_total: 0, auditoria_deslocamento: 0,
    } })],
    ['/v1/admin/producao/avisos', route => route.fulfill({ json: { avisos: [] } })],
  ]);
  await page.goto('http://operations.test/admin/producao');
  await page.waitForFunction(() => document.querySelector('#metrics-caption')?.textContent.includes('últimas 24 horas'));
  assert.match(await page.locator('#metric-errors').textContent(), /sem amostra/);
}));

// Captura evidências visuais das entregas 2-4 (dados fictícios, rotas mockadas).
// Uso: node tests/frontend/evidencias-e2-e4.cjs  (a partir da raiz do repo)
const fs = require('node:fs');
const path = require('path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');
const OUT = '/downloads';

function rotas(page, handlers = [], { shell = false } = {}) {
  page.setDefaultTimeout(9000);
  page.on('pageerror', error => console.error('PAGEERROR:', error.message));
  return page.route('**/*', async route => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    for (const [match, handler] of handlers) {
      if ((typeof match === 'string' && p === match) || (match instanceof RegExp && match.test(p))) return handler(route, req, u);
    }
    if (p.startsWith('/static/')) return route.fulfill({ path: path.join(web, p) });
    if (p.startsWith('/admin/')) {
      const file = p.includes('assistente') ? 'admin-assistente.html' : p.includes('confiabilidade') ? 'admin-confiabilidade.html'
        : p.includes('consulta') ? 'admin-consulta.html' : p.includes('analises') ? 'admin-analise.html'
        : p.includes('lucratividade') ? 'admin-financeiro-lucratividade.html' : p.includes('producao') ? 'admin-producao.html' : 'admin-leads.html';
      let html = read(file);
      if (!shell) html = html.replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, '');
      return route.fulfill({ contentType: 'text/html', body: html });
    }
    if (p === '/v1/auth/me') return route.fulfill({ json: { id: 7, nome: 'Teste', perfil: 'operador', permissoes: ['leads.view'], organizacao: { id: 1 }, mfa_ativo: false } });
    await route.fulfill({ json: { itens: [], contatos: [], total: 0, novidades: [], lidos: [], adiados: [] } });
  });
}

(async () => {
  const browser = await chromium.launch();

  // E2/R11: falha ao adiar aviso reativa o botão e mostra mensagem de erro.
  {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await rotas(page, [
      ['/v1/admin/atualizacoes', route => route.fulfill({ json: { novidades: [
        { id: 'n1', versao: 'v1.0.1', titulo: 'Aviso', impacto_usuario: 'Impacto', classificacao: 'comum', leitura_obrigatoria: false, pode_adiar: true, estado: {} },
      ], lidos: [], adiados: [] } })],
      [/\/adiar$/, route => route.abort()],
    ], { shell: true });
    await page.goto('http://operations.test/admin/pesquisas');
    await page.locator('[data-toggle-avisos]').click();
    await page.locator('[data-adiar-atualizacao]').click();
    await page.waitForFunction(() => /tente novamente/i.test(document.querySelector('.admin-avisos-erro')?.textContent || ''));
    await page.screenshot({ path: `${OUT}/ev-e2-r11-banner-erro.png` });
    await page.close();
  }

  // E2/R11: Zezinho restaura a pergunta em falha.
  {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await rotas(page, [
      ['/v1/admin/assistente/perguntar', route => route.fulfill({ status: 500, json: { detail: 'Indisponível no momento' } })],
    ]);
    await page.goto('http://operations.test/admin/assistente');
    await page.locator('#assistente-input').fill('Qual a situação da marca X?');
    await page.locator('#assistente-form button').click();
    await page.waitForFunction(() => document.querySelector('#assistente-message').textContent.includes('Indisponível'));
    await page.screenshot({ path: `${OUT}/ev-e2-r11-zezinho-falha.png` });
    await page.close();
  }

  // E2/R17: MFA ativo + R14: busca de lead no legal hold (mesma página).
  {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await rotas(page, [
      ['/v1/auth/me', route => route.fulfill({ json: { id: 7, nome: 'Teste', perfil: 'operador', permissoes: [], organizacao: { id: 1 }, mfa_ativo: true } })],
      ['/v1/admin/confiabilidade', route => route.fulfill({ json: {
        uso: { usuarios: 3, pesquisas: 12, leads: 8 }, fila: { status: 'ok', pendentes: 0 },
        organizacao: { nome: 'Zé Registra', status: 'ativa', assinatura_status: 'ativa', retencao_dados_dias: 365, branding: {} },
        retencao: { matriz: [{ categoria: 'lead', prazo_dias: 365 }] }, permissoes: {},
      } })],
      ['/v1/admin/leads', route => route.fulfill({ json: { itens: [
        { id: 11, nome: 'Lead Bloqueio', empresa: 'Empresa X', email: 'x@example.com', telefone: '', status: 'novo', tags: [] },
      ], total: 1 } })],
      ['/v1/auth/social/identities/me', route => route.fulfill({ json: { providers: [
      { nome: 'Google', linked: false, enabled: true }, { nome: 'Apple', linked: false, enabled: false },
      ] } })],
      ['/v1/admin/politicas-privacidade', route => route.fulfill({ json: {
      vigente: null, consentimentos_pendentes: 0, regra_novo_consentimento: 'Aceite exigido apenas na próxima interação pública.', politicas: [],
    } })],
    ]);
    await page.goto('http://operations.test/admin/confiabilidade');
    await page.waitForFunction(() => document.querySelector('#mfa-status').textContent.includes('MFA ativo'));
    await page.locator('#legal-hold-busca').fill('Bloqueio');
    await page.locator('#legal-hold-busca-resultados button').first().waitFor();
    await page.locator('#legal-hold-busca-resultados button').first().click();
    await page.waitForFunction(() => document.querySelector('#legal-hold-form [name="lead_id"]').value === '11');
    await page.screenshot({ path: `${OUT}/ev-e2-r17-mfa-e3-r14-legalhold.png`, fullPage: true });
    await page.close();
  }

  // E3/R08: etapa de revisão antes de pesquisar (desktop e mobile).
  for (const [nome, vp] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport: vp });
    await rotas(page, [
      ['/v1/admin/consulta/classes-nice', route => route.fulfill({ json: [{ codigo: '30', titulo: 'Café' }] })],
      ['/v1/admin/consulta', route => route.fulfill({ json: { itens: [], total: 0 } })],
    ]);
    await page.goto('http://operations.test/admin/consulta');
    await page.locator('[data-field="marca"]').fill('CAFÉ TESTE');
    await page.locator('[name="nome"]').fill('Cliente Teste');
    await page.locator('[name="email"]').fill('cliente@example.com');
    await page.locator('#consulta-form button.form-submit').first().click();
    await page.locator('#consulta-revisao:not([hidden])').waitFor();
    await page.screenshot({ path: `${OUT}/ev-e3-r08-revisao-${nome}.png` });
    await page.close();
  }

  // E3/R09: próxima ação e rótulo em português na análise.
  {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await rotas(page, [
      [/\/v1\/admin\/analises\//, route => route.fulfill({ json: {
        pesquisa: { id: 'r1', marca: 'Marca 1', criado_em: '2026-01-01T00:00:00Z', relatorio_token: 't' },
        lead: null, permissoes: { validacao_visualizar: true, risco_visualizar: true }, usuario: {},
        relatorio_completo: { gerado: false, base_disponivel: true },
        workflow: { state: 'PENDING_REVIEW', history: [], report_version: 1 },
        analise_consolidada: {
          versao_relatorio: 3, versao_motor: 'motor v1', revisao: { validada: false },
          conclusao_preliminar: { nivel_risco: 'moderado', cobertura: 0.92, aviso: 'Leitura automática preliminar, sujeita à revisão do especialista.' },
          apresentacao: { situacao: { codigo: 'inconclusiva', rotulo: 'Inconclusiva', explicacao: 'A pesquisa automática encontrou anterioridades que exigem revisão humana antes do parecer.' } },
          pendencias: [{ criterio: 'Colidência', descricao: 'Revisar a marca anterior CAFÉ EXEMPLO (processo 900000000000000), classe 30.' }],
          analise_conjunto: { marca: 'Marca 1' }, matriz: null, anterioridades: [], fontes: { ultima_rpi: '2700' }, disclaimers: [],
          estatistica: { disponivel: false }, titulo: 'Análise de registrabilidade da marca Marca 1',
          recomendacao: 'Resolver a colidência apontada antes de protocolar o pedido.', diretriz_acao: null, legado: false,
        },
      } })],
    ]);
    await page.goto('http://operations.test/admin/analises/r1');
    await page.waitForFunction(() => document.body.textContent.includes('Revisão pendente'));
    await page.locator('.analysis-next').waitFor();
    await page.screenshot({ path: `${OUT}/ev-e3-r09-analise.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/ev-e3-r09-analise-mobile.png` });
    await page.close();
  }

  // E3/R15: indicadores com fonte/período e distinção sem amostra.
  {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await rotas(page, [
      ['/v1/admin/producao', route => route.fulfill({ json: {
        ambiente: 'produção', requisicoes_24h: 0, taxa_erros_24h: 0, duracao_media_ms_24h: 0, taxa_divergencia: 0,
        versoes_relatorio: 0, pesquisas_versionadas: 0, auditoria: [], auditoria_total: 0, auditoria_deslocamento: 0,
      } })],
      ['/v1/admin/producao/avisos', route => route.fulfill({ json: { avisos: [] } })],
    ]);
    await page.goto('http://operations.test/admin/producao');
    await page.waitForFunction(() => document.querySelector('#metrics-caption')?.textContent.includes('últimas 24 horas'));
    await page.screenshot({ path: `${OUT}/ev-e3-r15-producao.png` });
    await page.close();
  }

  await browser.close();
  console.log('ok');
})().catch(e => { console.error(e.message); process.exit(1); });

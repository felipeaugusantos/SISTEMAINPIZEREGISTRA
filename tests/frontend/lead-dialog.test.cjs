// Run: node --test tests/frontend/lead-dialog.test.cjs
// Uses only local assets and mocked APIs; never touches production.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../../app/web");
const lead = { id: 42, nome: "Contato de teste", pesquisas: [], status: "novo", tags: [],
  criado_em: "2026-01-01T12:00:00Z", empresa: "Empresa teste" };

async function fixture(page, screen, permissions, opts = {}) {
  const requests = [], errors = [];
  const usuarioMock = { id: 1, perfil: "operador", permissoes: permissions, ...(opts.usuario || {}) };
  const leadMock = { ...lead, ...(opts.leadData || {}) };
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    requests.push({ path: p, query: url.search, method: req.method(), body: req.postDataJSON() });
    if (p === "/admin/" + screen) {
      // The shell has its own unrelated auth/navigation tests.
      const html = fs.readFileSync(path.join(web, "admin-" + screen + ".html"), "utf8")
        .replace(/<script[^>]*src="\/static\/admin-shell.js[^>]*><\/script>/g, "");
      return route.fulfill({ contentType: "text/html", body: html });
    }
    if (p.startsWith("/static/")) {
      const file = path.join(web, p);
      if (fs.existsSync(file)) return route.fulfill({ path: file });
      return route.fulfill({ status: 404, body: "" });
    }
    const base = { itens: [], contatos: [], total: 0, deslocamento: 0, tem_mais: false };
    let data = base;
    if (p === "/v1/auth/me") data = usuarioMock;
    else if (p === "/v1/admin/leads/42") data = leadMock;
    else if (p === "/v1/admin/leads/42/propostas") data = { propostas: [{ id: 7, numero: "PROP-TESTE", versao: 1,
      marca: "MARCA TESTE", status: "rascunho", total: 1500 }] };
    else if (p === "/v1/admin/propostas/7/documento") data = { texto: "Conteúdo da proposta para visualização" };
    else if (p === "/v1/admin/leads") data = { ...base, itens: [leadMock], total: 1,
      total_global: 1, pesquisas_total: 0, por_status: { novo: 1 },
      acoes: { gerenciar: permissions.includes("leads.manage") } };
    else if (p === "/v1/admin/leads-kanban") data = {
      etapas: [{ id: "contato_inicial", label: "Contato inicial" }],
      cards: [{ ...lead, etapa: "contato_inicial" }], acoes: { gerenciar: true } };
    else if (p === "/v1/admin/crm/referencias") data = {
      canais: [], operadores: [], status_clientes: [], tipos_lembrete: [], clientes: [], prioridades: [] };
    else if (p === "/v1/admin/crm/historico") data = { ...base, por_canal: {} };
    else if (p === "/v1/admin/crm/lembretes") data = { ...base,
      acoes: { gerenciar: true }, metricas: {}, cadastros_para_atualizar: [] };
    else if (p === "/v1/admin/leads-dashboard") data = { por_status: {}, prioridades: {}, responsaveis: [] };
    // Optional subsystems are unavailable in this fixture and must degrade safely.
    else if (/sugestao|qualificacao|score/.test(p)) return route.fulfill({
      status: 503, json: { detail: "Indisponível no teste" } });
    return route.fulfill({ json: data });
  });
  await page.goto("http://127.0.0.1:8000/admin/" + screen);
  return { requests, errors };
}

test("Visualizar proposta abre conteúdo sem popup em branco", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { errors } = await fixture(page, "crm", ["leads.view", "leads.manage"]);
    await page.locator("[data-open-contact]").first().click();
    await page.locator('#lead-dialog [data-tab="propostas"]').click();
    await page.locator(".proposal-preview").waitFor();
    const popupPromise = page.waitForEvent("popup");
    await page.locator(".proposal-preview").click();
    const popup = await popupPromise;
    // A aba já nasce com ".proposta-texto" mostrando "Carregando proposta…";
    // esperar o conteúdo final evita a corrida que deixava o teste intermitente.
    await popup.waitForFunction(() =>
      (document.querySelector(".proposta-texto")?.textContent || "").includes("Conteúdo da proposta"));
    assert.match(await popup.locator(".proposta-texto").textContent(), /Conteúdo da proposta para visualização/);
    assert.equal(await popup.evaluate(() => window.opener), null);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

for (const screen of ["crm", "leads"]) {
  test(screen + ": abre, salva sem PII, fecha e reabre sem duplicar handlers", async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      const { requests, errors } = await fixture(page, screen, ["leads.view", "leads.manage"]);
      if (screen === "crm") await page.locator("[data-open-contact]").first().click();
      else await page.locator("#leads-list .view-lead").first().click();
      await page.locator('#lead-dialog[open] [data-tab="atendimento"]').click();
      assert.equal(await page.locator('#lead-crm-form [name="tags"]').count(), 0);
      assert.equal(await page.locator('#lead-crm-form [name="notas"]').count(), 0);
      await page.locator('#lead-crm-form button[type="submit"]').click();
      await page.waitForFunction(() => document.querySelector("#lead-save-message").textContent.startsWith("Atendimento salvo"));
      let patches = requests.filter(r => r.path === "/v1/admin/leads/42" && r.method === "PATCH");
      assert.equal(patches.length, 1);
      assert.ok(!("tags" in patches[0].body) && !("notas" in patches[0].body));
      await page.locator("#lead-dialog .dialog-close").click();
      await page.waitForFunction(() => !document.querySelector("#lead-dialog").open);
      await page.evaluate(() => window.createLeadDialog(document.querySelector("#lead-workspace")).openLead(42));
      await page.locator('#lead-dialog [data-tab="atendimento"]').click();
      await page.locator('#lead-crm-form button[type="submit"]').click();
      await page.waitForFunction(() => document.querySelector("#lead-save-message").textContent.startsWith("Atendimento salvo"));
      assert.equal(requests.filter(r => r.path === "/v1/admin/leads/42" && r.method === "PATCH").length, 2);
      if (screen === "crm") {
        assert.equal(await page.locator("#leads-list, #lead-filters").count(), 0);
        assert.ok(!requests.some(r => r.path === "/static/admin-leads.js" || r.path === "/v1/admin/leads"));
        assert.ok(requests.filter(r => r.path === "/v1/admin/leads-kanban").length >= 3);
        assert.ok(requests.filter(r => r.path === "/v1/admin/crm/historico").length >= 3);
      } else {
        assert.ok(requests.filter(r => r.path === "/v1/admin/leads").length >= 3);
        await page.locator("#lead-dialog .dialog-close").click();
        await page.locator("#lead-status-filter").selectOption("em_contato");
        await Promise.all([
          page.waitForResponse(r => new URL(r.url()).searchParams.get("status") === "em_contato"),
          page.locator("#lead-filters").evaluate(form => form.requestSubmit()),
        ]);
      }
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}

test("CRM somente consulta e inicialização idempotente", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { errors } = await fixture(page, "crm", ["leads.view"]);
    await page.locator("[data-open-contact]").first().click();
    await page.locator('#lead-dialog [data-tab="atendimento"]').click();
    assert.equal(await page.locator("#lead-crm-form").count(), 0);
    assert.equal(await page.locator(".lead-readonly-note").count(), 1);
    assert.equal(await page.evaluate(() => {
      const root = document.querySelector("#lead-workspace");
      return window.createLeadDialog(root) === window.createLeadDialog(root);
    }), true);
    await page.locator("#lead-dialog .dialog-close").click();
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Componente pode ser carregado sem DOM de Leads ou CRM", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.setContent("<main>Página sem formulário</main>");
    await page.addScriptTag({ path: path.join(web, "static/lead-dialog.js") });
    assert.equal(await page.evaluate(() => typeof window.createLeadDialog), "function");
    assert.equal(await page.evaluate(() => typeof window.openLead), "undefined");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("CRM respeita acesso PII e mantém o payload autorizado", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { requests, errors } = await fixture(page, "crm", ["leads.view", "leads.manage", "leads.pii.view"]);
    await page.locator("[data-open-contact]").first().click();
    await page.locator('#lead-dialog [data-tab="atendimento"]').click();
    await page.locator('#lead-crm-form [name="tags"]').fill("teste, retorno");
    await page.locator('#lead-crm-form [name="notas"]').fill("Anotação fictícia");
    await page.locator('#lead-crm-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector("#lead-save-message").textContent.startsWith("Atendimento salvo"));
    const patch = requests.find(r => r.path === "/v1/admin/leads/42" && r.method === "PATCH");
    assert.deepEqual(patch.body.tags, ["teste", "retorno"]);
    assert.equal(patch.body.notas, "Anotação fictícia");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Portal do cliente some para quem não é responsável nem administrador", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { requests, errors } = await fixture(page, "crm", ["leads.view", "leads.manage"],
      { usuario: { id: 1 }, leadData: { responsavel_id: 2 } });
    await page.locator("[data-open-contact]").first().click();
    await page.locator('#lead-dialog[open] [data-tab="atendimento"]').click();
    await page.locator("#lead-crm-form").waitFor();
    assert.equal(await page.locator(".lead-portal-access").count(), 0);
    assert.equal(await page.locator(".lead-portal-messages").count(), 0);
    assert.equal(await page.locator(".lead-portal-arquivos").count(), 0);
    assert.ok(!requests.some(r => /portal-(acesso|mensagens|arquivos)/.test(r.path)));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Portal do cliente aparece para o responsável pelo lead", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { requests, errors } = await fixture(page, "crm", ["leads.view", "leads.manage"],
      { usuario: { id: 2 }, leadData: { responsavel_id: 2 } });
    await page.locator("[data-open-contact]").first().click();
    await page.locator(".lead-portal-access").waitFor();
    await page.locator(".lead-portal-messages").waitFor();
    await page.locator(".lead-portal-arquivos").waitFor();
    assert.ok(requests.some(r => r.path === "/v1/admin/leads/42/portal-acesso"));
    assert.ok(requests.some(r => r.path === "/v1/admin/leads/42/portal-mensagens"));
    assert.ok(requests.some(r => r.path === "/v1/admin/leads/42/portal-arquivos"));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Portal do cliente aparece para administrador mesmo sem ser responsável", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { requests, errors } = await fixture(page, "crm", ["leads.view", "leads.manage"],
      { usuario: { id: 1, perfil: "administrador" }, leadData: { responsavel_id: 2 } });
    await page.locator("[data-open-contact]").first().click();
    await page.locator(".lead-portal-access").waitFor();
    assert.ok(requests.some(r => r.path === "/v1/admin/leads/42/portal-acesso"));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("CRM (sem operationsMode) mantém link direto de WhatsApp sem validação de país", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    // Número de 10 dígitos sem DDI: no CRM o atalho segue o comportamento
    // anterior (wa.me com os dígitos como cadastrados, sem confirmação).
    await fixture(page, "crm", ["leads.view", "leads.manage"], { leadData: { telefone: "1632050534" } });
    await page.evaluate(() => window.createLeadDialog(document.querySelector("#lead-workspace")).openLead(42));
    const link = page.locator('#lead-dialog a[href="https://wa.me/1632050534"]');
    await link.waitFor();
    assert.equal(await link.getAttribute("data-wa-destino"), null);
    assert.equal(await link.getAttribute("data-wa-sugestao"), null);
    assert.equal(await page.locator("#lead-dialog [data-wa-invalido]").count(), 0);
  } finally { await browser.close(); }
});

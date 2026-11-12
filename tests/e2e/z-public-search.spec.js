const { test, expect } = require("@playwright/test");

// Jornada da pesquisa pública de marca: formulário da landing -> POST
// /v1/pesquisas-marca -> página do relatório -> PDF. Usa as marcas sintéticas
// MARCAE2EPUBLICA* semeadas em tests/e2e/seed_functional.py. Nenhum dado real.

const DADOS = {
  nome: "Lead E2E Pesquisa Publica",
  empresa: "Empresa E2E Pesquisa Publica",
  // E-mail único por execução: a deduplicação é por lead (e-mail) + marca,
  // e o banco E2E sobrevive entre rodadas locais.
  email: `e2e-pesquisa-publica-${Date.now()}@example.com`,
  telefone: "16999990000",
  marca: "MARCAE2EPUBLICA",
  atividade: "cafeteria e lanchonete",
};

async function preencherFormulario(page, { marca = DADOS.marca } = {}) {
  await page.goto("/");
  await page.fill("#contact-name", DADOS.nome);
  await page.fill("#company", DADOS.empresa);
  await page.fill("#corporate-email", DADOS.email);
  await page.fill("#corporate-phone", DADOS.telefone);
  await page.fill("#brand-input", marca);
  await page.fill("#business-activity", DADOS.atividade);
  await page.check("#privacy-consent");
}

// O corpo é capturado interceptando a rota: app.js navega para o relatório
// assim que a resposta chega, e a resposta navegada perde o corpo depois.
async function enviarFormulario(page) {
  let captura;
  const capturada = new Promise((resolver) => { captura = resolver; });
  await page.route("**/v1/pesquisas-marca", async (rota) => {
    const resposta = await rota.fetch();
    const corpo = await resposta.json().catch(() => null);
    captura({ status: resposta.status(), corpo });
    await rota.fulfill({ response: resposta });
  });
  await page.click("#research-form button[type='submit']");
  const resultado = await capturada;
  await page.unroute("**/v1/pesquisas-marca");
  return resultado;
}

test("pesquisa pública gera relatório com ocorrências e baixa o PDF", async ({ page }) => {
  const errosConsole = [];
  page.on("pageerror", (erro) => errosConsole.push(String(erro)));

  await preencherFormulario(page);
  const downloadPromessa = page.waitForEvent("download", { timeout: 30000 });
  const resposta = await enviarFormulario(page);
  expect(resposta.status).toBe(201);
  const corpo = resposta.corpo;
  expect(corpo.duplicada).toBe(false);
  expect(corpo.relatorio_url).toContain("/relatorios/");

  await page.waitForURL(/\/relatorios\//);
  await expect(page.locator("#report-content")).toBeVisible({ timeout: 20000 });
  await expect(page.locator("#report-brand")).toHaveText(DADOS.marca);
  await expect(page.locator("#report-count")).toContainText("ocorrência");
  await expect(page.locator("#report-metrics")).toContainText("Base atualizada até");
  await expect(page.locator("#report-empty")).toContainText("resumo não expõe");

  const download = await downloadPromessa;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const caminho = await download.path();
  const cabecalho = Buffer.alloc(4);
  const { createReadStream } = require("fs");
  await new Promise((resolver, rejeitar) => {
    const fluxo = createReadStream(caminho, { start: 0, end: 3 });
    fluxo.on("data", (trecho) => trecho.copy(cabecalho));
    fluxo.on("end", resolver);
    fluxo.on("error", rejeitar);
  });
  expect(cabecalho.toString("latin1")).toBe("%PDF");
  expect(errosConsole).toEqual([]);
});

test("reenvio da mesma marca reutiliza a pesquisa sem duplicar", async ({ page }) => {
  await preencherFormulario(page);
  const primeira = (await enviarFormulario(page)).corpo;
  // Aguarda o redirecionamento do primeiro envio antes de voltar ao formulário.
  await page.waitForURL(/\/relatorios\//);

  await preencherFormulario(page);
  const segundaResposta = await enviarFormulario(page);
  expect(segundaResposta.status).toBe(201);
  const segunda = segundaResposta.corpo;

  expect(segunda.duplicada).toBe(true);
  expect(segunda.id).toBe(primeira.id);
  await page.waitForURL(new RegExp(`/relatorios/${primeira.id}`));
  await expect(page.locator("#report-content")).toBeVisible({ timeout: 20000 });
});

test("relatório exige token: acesso direto mostra estado de erro", async ({ page, request }) => {
  const criacao = await request.post("/v1/pesquisas-marca", {
    data: { ...{
      nome: DADOS.nome,
      empresa: DADOS.empresa,
      email_corporativo: "e2e-pesquisa-sem-token@example.com",
      telefone: DADOS.telefone,
      marca: DADOS.marca,
      atividade: DADOS.atividade,
      aceite_privacidade: true,
      aceite_marketing: false,
      website: "",
    } },
  });
  expect(criacao.status()).toBe(201);
  const { id } = await criacao.json();

  await page.goto(`/relatorios/${id}`);
  await expect(page.locator("#report-error")).toBeVisible({ timeout: 20000 });
  await expect(page.locator("#report-content")).toBeHidden();
});

test("marca curta demais é barrada pela validação antes de qualquer envio", async ({ page }) => {
  let postDisparado = false;
  page.on("request", (requisicao) => {
    if (requisicao.url().includes("/v1/pesquisas-marca") && requisicao.method() === "POST") postDisparado = true;
  });
  await preencherFormulario(page, { marca: "A" });
  await page.click("#research-form button[type='submit']");
  await page.waitForTimeout(1500);
  expect(postDisparado).toBe(false);
  const mensagemValidacao = await page.locator("#brand-input").evaluate((campo) => campo.validationMessage);
  expect(mensagemValidacao).not.toBe("");
  await expect(page).toHaveURL(/\/$/);
});

test("campo-armadilha preenchido recusa o envio", async ({ page }) => {
  await preencherFormulario(page);
  // O campo é oculto por CSS; bots o preenchem via DOM, não pela interface.
  await page.locator("#website").evaluate((campo) => { campo.value = "http://spam.example"; });
  const resposta = await enviarFormulario(page);
  expect(resposta.status).toBe(400);
  await expect(page.locator("#form-status")).toContainText("Envio inválido");
});

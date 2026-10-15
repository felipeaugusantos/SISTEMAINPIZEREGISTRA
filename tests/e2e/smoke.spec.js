const crypto = require("crypto");
const fs = require("fs");
const { test, expect } = require("@playwright/test");

function base32Decode(segredo) {
  const alfabeto = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const caractere of segredo.replace(/=+$/, "").toUpperCase()) {
    const valor = alfabeto.indexOf(caractere);
    if (valor === -1) continue;
    bits += valor.toString(2).padStart(5, "0");
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    bytes.push(parseInt(bits.substring(i, i + 8), 2));
  }
  return Buffer.from(bytes);
}

function codigoTotp(segredo) {
  const contador = Math.floor(Date.now() / 1000 / 30);
  const buffer = Buffer.alloc(8);
  buffer.writeBigUInt64BE(BigInt(contador));
  const resumo = crypto.createHmac("sha1", base32Decode(segredo)).update(buffer).digest();
  const deslocamento = resumo[resumo.length - 1] & 0x0f;
  const numero =
    ((resumo[deslocamento] & 0x7f) << 24) |
    ((resumo[deslocamento + 1] & 0xff) << 16) |
    ((resumo[deslocamento + 2] & 0xff) << 8) |
    (resumo[deslocamento + 3] & 0xff);
  return (numero % 1_000_000).toString().padStart(6, "0");
}

// Gera o código TOTP com folga na janela de 30s: se faltam menos de 5s para
// virar, espera a próxima janela antes de gerar. Sem isso, um código gerado no
// fim da janela chegava ao servidor já na janela seguinte e era recusado --
// causa do teste instável de configuração do MFA (#mfa-setup-recovery).
async function codigoTotpComFolga(page, segredo) {
  const restante = 30 - (Math.floor(Date.now() / 1000) % 30);
  if (restante < 5) {
    await page.waitForTimeout((restante + 1) * 1000);
  }
  return codigoTotp(segredo);
}

test("a consulta pública apresenta o formulário essencial", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(/Zé Registra/i);
  await expect(page.getByRole("heading", { name: /Sua marca começa/i })).toBeVisible();
  await expect(page.locator("#brand-input")).toBeVisible();
  await expect(page.locator("#contact-name")).toBeVisible();
  await expect(page.locator("#corporate-email")).toBeVisible();
  await expect(page.locator("#corporate-phone")).toBeVisible();
});

test("uma área administrativa exige autenticação", async ({ request }) => {
  const response = await request.get("/admin", { maxRedirects: 0 });

  expect(response.status()).toBe(303);
  expect(response.headers().location).toBe("/login");
});

test("o portal do cliente exige autenticação própria", async ({ page }) => {
  await page.goto("/portal");
  await expect(page).toHaveURL(/\/portal/);
  await expect(page.getByRole("heading", { name: /Seu atendimento, sempre perto/i })).toBeVisible();
  await expect(page.locator("#portal-login, form").first()).toBeVisible();
});

test("telas principais geram captura visual", async ({ page }) => {
  await page.goto("/login");
  const imagem = await page.screenshot({ fullPage: true });
  expect(imagem.length).toBeGreaterThan(5000);
  await expect(page).toHaveScreenshot("login.png", { animations: "disabled" });
  await page.goto("/portal");
  await expect(page).toHaveScreenshot("portal-login.png", { animations: "disabled" });
});

test("o administrador entra pelo formulário e acessa a visão geral", async ({ page }) => {
  test.skip(!process.env.E2E_ADMIN_PASSWORD, "Credenciais E2E não configuradas.");

  await page.goto("/login");
  await page.getByLabel("Usuário ou e-mail").fill(process.env.E2E_ADMIN_USERNAME || "admin");
  await page.getByLabel("Senha", { exact: true }).fill(process.env.E2E_ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();

  await expect(page).toHaveURL(/\/admin$|\/configurar-mfa$/);
  if (page.url().endsWith("/configurar-mfa")) {
    // O segredo é preenchido de forma assíncrona após a navegação; esperar
    // ele aparecer antes de ler (senão o TOTP seria gerado do texto vazio).
    const campoSegredo = page.locator("#mfa-setup-secret");
    await expect(campoSegredo).not.toBeEmpty();
    const segredo = (await campoSegredo.textContent()).trim();
    await page.locator("#mfa-setup-code").fill(await codigoTotpComFolga(page, segredo));
    await page.getByRole("button", { name: "Confirmar" }).click();
    await expect(page.locator("#mfa-setup-recovery")).toBeVisible();
    await page.getByRole("button", { name: "Continuar" }).click();
  }

  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Pendências que pedem atenção/i })).toBeVisible();

  const me = await page.request.get("/v1/auth/me");
  expect(me.ok()).toBeTruthy();
  expect((await me.json()).usuario).toBe(process.env.E2E_ADMIN_USERNAME || "admin");

  await page.goto("/admin/crm");
  await expect(page.getByRole("heading", { name: "Pendências da equipe" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Novo lembrete" })).toBeVisible();
  await expect(page.getByLabel("Status do cliente")).toBeVisible();

  await page.goto("/admin/consulta");
  await expect(page.getByRole("heading", { name: "Consulta de marcas" })).toBeVisible();
  await page.getByRole("button", { name: "Adicionar outra marca" }).click();
  await expect(page.locator(".consulta-marca-item")).toHaveCount(2);
  await expect(page.locator('.consulta-marca-item [data-field="classes_nice"]')).toHaveCount(2);
  await page.locator(".consulta-marca-item").nth(1).getByRole("button", { name: "Remover" }).click();
  await expect(page.locator(".consulta-marca-item")).toHaveCount(1);

  await page.goto("/admin/operacao-juridica");
  await expect(page.getByRole("heading", { name: "Operação jurídica" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Executar motor de prazos" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Novo prazo" })).toBeVisible();
  await expect(page.getByText("CENTRAL DE NOTIFICAÇÕES")).toBeVisible();

  // Guarda a sessão para as jornadas de negócio abaixo (A15), sem repetir o login com MFA.
  fs.mkdirSync("test-results", { recursive: true });
  fs.writeFileSync(ARQUIVO_SESSAO, JSON.stringify(await page.context().storageState()));
});

// --- Auditoria A15 (15/10/2026): jornadas críticas com persistência real ---------------------------
// O smoke acima só prova acesso e navegação. Estes testes percorrem fluxos de negócio ponta a ponta
// contra a API e o banco reais da CI e conferem o que ficou gravado. Reaproveitam a sessão do teste do
// administrador (o MFA é configurado uma única vez, ali).

const ARQUIVO_SESSAO = "test-results/e2e-admin-state.json";

function estadoAdmin() {
  // Em arquivo (e nao em variavel) para sobreviver a reinicio do worker do Playwright apos uma falha.
  return fs.existsSync(ARQUIVO_SESSAO) ? JSON.parse(fs.readFileSync(ARQUIVO_SESSAO, "utf-8")) : null;
}

async function contextoAdmin(playwright, baseURL) {
  const estado = estadoAdmin();
  const api = await playwright.request.newContext({ baseURL, storageState: estado });
  const csrf = estado.cookies.find((c) => c.name === "zr_csrf")?.value || "";
  return { api, cabecalhos: { "X-CSRF-Token": csrf } };
}

async function responsavelId(api) {
  const resposta = await api.get("/v1/admin/leads-responsaveis");
  expect(resposta.ok()).toBeTruthy();
  return (await resposta.json()).itens[0].id;
}

function emailUnico(prefixo) {
  return `${prefixo}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.zeregistra.test`;
}

async function criarLeadPublico(request, email) {
  const resposta = await request.post("/v1/leads", {
    data: {
      nome: "Cliente E2E",
      email,
      telefone: "(16) 99999-0000",
      marca: "Marca E2E",
      origem: "geral",
      aceite_privacidade: true,
    },
  });
  expect(resposta.status()).toBe(201);
}

async function buscarLeadAdmin(api, email) {
  const resposta = await api.get(`/v1/admin/leads?busca=${encodeURIComponent(email)}`);
  expect(resposta.ok()).toBeTruthy();
  const itens = (await resposta.json()).itens;
  expect(itens.length).toBe(1);
  return itens[0];
}

test("jornada: lead público chega ao painel e a mudança de status persiste", async ({ playwright, request, baseURL }) => {
  test.skip(!estadoAdmin(), "Sessão de administrador E2E indisponível.");
  const { api, cabecalhos } = await contextoAdmin(playwright, baseURL);
  const email = emailUnico("jornada-lead");

  await criarLeadPublico(request, email);
  const lead = await buscarLeadAdmin(api, email);
  expect(lead.status).toBe("novo");

  const alteracao = await api.patch(`/v1/admin/leads/${lead.id}`, {
    headers: cabecalhos,
    data: { status: "em_contato", responsavel_id: await responsavelId(api), notas: "Contato feito no E2E" },
  });
  expect(alteracao.ok(), `PATCH lead: ${alteracao.status()} ${await alteracao.text()}`).toBeTruthy();

  // Persistência: nova leitura (outra requisição) devolve o estado gravado.
  const detalhe = await (await api.get(`/v1/admin/leads/${lead.id}`)).json();
  expect(detalhe.status).toBe("em_contato");
  await api.dispose();
});

test("jornada: exclusão pelo titular respeita atendimento ativo e anonimiza depois de descartado", async ({
  playwright,
  request,
  baseURL,
}) => {
  test.skip(!estadoAdmin(), "Sessão de administrador E2E indisponível.");
  const { api, cabecalhos } = await contextoAdmin(playwright, baseURL);
  const email = emailUnico("jornada-lgpd");
  await criarLeadPublico(request, email);
  const lead = await buscarLeadAdmin(api, email);

  // 1) Lead com atendimento ativo: o pedido é aceito, mas o cadastro é conservado.
  let pedido = await request.post("/v1/privacidade/solicitar-exclusao", { data: { email } });
  expect(pedido.ok()).toBeTruthy();
  let token = (await pedido.json()).token_teste_local;
  expect(token).toBeTruthy();
  let confirmacao = await (await request.post("/v1/privacidade/confirmar-exclusao", { data: { token } })).json();
  expect(confirmacao.status).toBe("conservado");
  expect(confirmacao.registros_conservados).toBe(1);
  const aindaIntacto = await (await api.get(`/v1/admin/leads/${lead.id}`)).json();
  expect(aindaIntacto.email).toBe(email);

  // 2) Depois de descartado (sem bloqueios), o novo pedido anonimiza de verdade.
  const descarte = await api.patch(`/v1/admin/leads/${lead.id}`, {
    headers: cabecalhos,
    data: { status: "descartado", motivo_perda: "outro", motivo_perda_detalhe: "Teste E2E" },
  });
  expect(descarte.ok(), `PATCH descarte: ${descarte.status()} ${await descarte.text()}`).toBeTruthy();
  pedido = await request.post("/v1/privacidade/solicitar-exclusao", { data: { email } });
  token = (await pedido.json()).token_teste_local;
  expect(token).toBeTruthy();
  confirmacao = await (await request.post("/v1/privacidade/confirmar-exclusao", { data: { token } })).json();
  expect(confirmacao.status).toBe("ok");
  expect(confirmacao.registros_afetados).toBe(1);
  const anonimizado = await (await api.get(`/v1/admin/leads/${lead.id}`)).json();
  expect(anonimizado.nome).toMatch(/^Titular anonimizado/);
  expect(anonimizado.email).not.toBe(email);

  // 3) O link já usado não vale de novo.
  const repetido = await (await request.post("/v1/privacidade/confirmar-exclusao", { data: { token } })).json();
  expect(repetido.status).toBe("erro");
  await api.dispose();
});

test("jornada negativa: rotas administrativas recusam quem não está autenticado", async ({ request }) => {
  for (const caminho of ["/v1/admin/leads", "/v1/auth/me"]) {
    const resposta = await request.get(caminho);
    expect([401, 403]).toContain(resposta.status());
  }
  const mutacao = await request.patch("/v1/admin/leads/1", { data: { status: "descartado" } });
  expect([401, 403]).toContain(mutacao.status());
});

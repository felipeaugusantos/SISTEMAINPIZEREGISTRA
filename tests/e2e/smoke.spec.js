const crypto = require("crypto");
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
});

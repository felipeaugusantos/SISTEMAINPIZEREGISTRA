const { test, expect } = require("@playwright/test");

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
  expect(response.headers().location).toMatch(/^\/login\?next=/);
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

  await page.goto("/admin/operacao-juridica");
  await expect(page.getByRole("heading", { name: "Operação jurídica" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Executar motor de prazos" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Novo prazo" })).toBeVisible();
  await expect(page.getByText("CENTRAL DE NOTIFICAÇÕES")).toBeVisible();
});

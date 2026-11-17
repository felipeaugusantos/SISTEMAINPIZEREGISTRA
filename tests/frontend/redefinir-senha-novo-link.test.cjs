const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../../app/web");

// Tela /redefinir-senha com o auth-pages.js real; rede simulada por rota.
async function fixture(page, respond, hash = "#token=token-de-teste") {
  const errors = [], requests = [];
  page.on("pageerror", e => errors.push(e.message));
  const html = fs.readFileSync(path.join(web, "redefinir-senha.html"), "utf8")
    .replace(/<script[\s\S]*?<\/script>/g, "").replace(/<link[^>]+>/g, "");
  await page.route("**/*", async route => {
    const req = route.request();
    if (new URL(req.url()).pathname === "/redefinir-senha") return route.fulfill({ contentType: "text/html", body: html });
    if (new URL(req.url()).pathname === "/v1/tenant/branding") return route.fulfill({ json: { marca: { propria: false } } });
    requests.push({ method: req.method(), url: req.url(), body: req.postDataJSON() });
    await respond(route, req);
  });
  await page.goto(`http://127.0.0.1:8000/redefinir-senha${hash}`);
  await page.addStyleTag({ path: path.join(web, "static/styles.css") });
  await page.addScriptTag({ path: path.join(web, "static/auth-pages.js") });
  return { errors, requests };
}

async function preencherESubmeter(page, senha = "SenhaForte!123", confirmacao = senha) {
  await page.locator("input[name=nova_senha]").fill(senha);
  await page.locator("input[name=confirmacao]").fill(confirmacao);
  await page.locator("#reset-password-form button[type=submit]").click();
}

const posts = requests => requests.filter(req => req.method === "POST" && req.url.includes("/v1/auth/recuperacao/redefinir"));

test("Redefinir: link ausente/inválido já abre oferecendo solicitar novo link", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { errors, requests } = await fixture(page, route => route.fulfill({ json: {} }), "");
    await page.waitForSelector("#solicitar-novo-link");
    assert.equal(await page.locator("#auth-message").textContent(), "Link de recuperação ausente ou inválido. Solicite um novo e-mail.");
    assert.equal(await page.locator("#auth-message").getAttribute("class"), "status-message error");
    assert.equal(await page.locator("#reset-password-form button[type=submit]").isDisabled(), true);
    const link = page.locator("#solicitar-novo-link");
    assert.equal(await link.textContent(), "Solicitar novo link");
    assert.equal(await link.getAttribute("href"), "/esqueci-senha");
    assert.equal(await link.isVisible(), true);
    assert.equal(posts(requests).length, 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Redefinir: token expirado/usado (400) oferece novo link e trava o envio", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    const { errors, requests } = await fixture(page, route =>
      route.fulfill({ status: 400, json: { detail: "Token inválido ou expirado" } }));
    await preencherESubmeter(page);
    await page.waitForSelector("#solicitar-novo-link");
    assert.equal(await page.locator("#auth-message").textContent(), "Este link expirou ou já foi usado. Solicite um novo link de recuperação.");
    assert.equal(await page.locator("#reset-password-form button[type=submit]").isDisabled(), true);
    const link = page.locator("#solicitar-novo-link");
    assert.equal(await link.getAttribute("href"), "/esqueci-senha");
    assert.equal(await link.isVisible(), true);
    if (process.env.REDEFINIR_SCREENSHOT) {
      fs.mkdirSync(path.dirname(process.env.REDEFINIR_SCREENSHOT), { recursive: true });
      await page.locator(".auth-card").screenshot({ path: process.env.REDEFINIR_SCREENSHOT });
    }
    // Sem reenvio automático nem manual: o botão segue travado após o 400.
    await page.evaluate(() => document.querySelector("#reset-password-form button[type=submit]").click());
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 50)));
    assert.equal(posts(requests).length, 1);
    assert.equal(posts(requests)[0].body.token, "token-de-teste");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Redefinir: erro 500 não oferece novo link e permite tentar de novo", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let falhar = true;
    const { errors, requests } = await fixture(page, route => {
      if (falhar) return route.fulfill({ status: 500, json: {} });
      return route.fulfill({ json: {} });
    });
    await preencherESubmeter(page);
    await page.waitForFunction(() => document.querySelector("#auth-message").classList.contains("error"));
    assert.equal(await page.locator("#auth-message").textContent(), "Erro interno (500). Tente novamente em instantes.");
    assert.equal(await page.locator("#solicitar-novo-link").count(), 0);
    assert.equal(await page.locator("#reset-password-form button[type=submit]").isDisabled(), false);
    falhar = false;
    await preencherESubmeter(page);
    await page.waitForFunction(() => document.querySelector(".status-message.success"));
    assert.equal(posts(requests).length, 2);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Redefinir: sucesso mantém o fluxo original sem novo link", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { errors, requests } = await fixture(page, route => route.fulfill({ json: {} }));
    await preencherESubmeter(page);
    await page.waitForFunction(() => document.querySelector(".status-message.success"));
    assert.equal(await page.locator(".status-message.success").textContent(), "Senha redefinida com sucesso.");
    assert.equal(await page.locator("a[href='/login']").textContent(), "Entrar com a nova senha");
    assert.equal(await page.locator("#solicitar-novo-link").count(), 0);
    assert.equal(posts(requests).length, 1);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("Redefinir: senhas diferentes não enviam requisição", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { errors, requests } = await fixture(page, route => route.fulfill({ json: {} }));
    await preencherESubmeter(page, "SenhaForte!123", "OutraSenha!456");
    await page.waitForFunction(() => document.querySelector("#auth-message").classList.contains("error"));
    assert.equal(await page.locator("#auth-message").textContent(), "As senhas não coincidem.");
    assert.equal(posts(requests).length, 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

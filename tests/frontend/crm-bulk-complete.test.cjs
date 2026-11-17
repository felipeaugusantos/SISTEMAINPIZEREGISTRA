const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../../app/web");
const item = (id, status = "pendente") => ({ id, status, prioridade: "media", tipo_nome: "Retorno",
  titulo: `Retornar contato ${id}`, cliente: `Cliente ${id}`, lembrar_em: "2026-11-18T12:00:00Z" });
const data = (itens = [item(1), item(2), item(3, "concluido")], manage = true) => ({ itens, total: itens.length,
  tem_mais: false, acoes: { gerenciar: manage }, metricas: { vencidos: 0, proximos_7_dias: 2, pendentes: 2, cadastros_para_atualizar: 0 }, cadastros_para_atualizar: [] });
async function fixture(page, respond = async route => route.fulfill({ json: data([]) })) {
  const requests = [], errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const html = fs.readFileSync(path.join(web, "admin-crm.html"), "utf8")
    .replace(/<script[\s\S]*?<\/script>/g, "").replace(/<link[^>]+>/g, "");
  await page.route("**/*", async route => {
    const req = route.request();
    if (new URL(req.url()).pathname === "/admin/crm") return route.fulfill({ contentType: "text/html", body: html });
    if (req.resourceType() === "image") return route.fulfill({ status: 204 });
    requests.push({ method: req.method(), url: req.url(), body: req.postDataJSON() });
    await respond(route, req);
  });
  await page.goto("http://127.0.0.1:8000/admin/crm");
  for (const file of ["styles.css", "ze-brand.css", "admin-crm.css", "admin-crm-overrides.css", "admin-crm-reminders.css"]) {
    await page.addStyleTag({ path: path.join(web, "static", file) });
  }
  await page.evaluate(() => { window.createLeadDialog = () => ({}); });
  const source = fs.readFileSync(path.join(web, "static/admin-crm.js"), "utf8");
  await page.addScriptTag({ content: source.slice(0, source.indexOf("\nreferences().then(")) });
  await page.evaluate(data => renderReminders(data), data());
  return { requests, errors };
}
const button = "#crm-reminder-complete-selected";
const select = "#crm-reminder-select-page";

test("bulk: explicit page selection, indeterminate state, confirmation can cancel, filter/render clears selection", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { requests, errors } = await fixture(page);
    assert.equal(await page.locator(".select-reminder").count(), 2);
    assert.equal(await page.locator(button).isDisabled(), true);
    await page.locator(".select-reminder").first().check();
    assert.equal(await page.locator(select).evaluate(el => el.indeterminate), true);
    await page.locator(select).check();
    assert.equal(await page.locator(".select-reminder:checked").count(), 2);
    let confirmation;
    page.once("dialog", async dialog => { confirmation = dialog.message(); await dialog.dismiss(); });
    await page.locator(button).click();
    assert.match(confirmation, /2 lembrete/);
    assert.equal(requests.length, 0);
    await page.evaluate(data => renderReminders(data), data([item(4)]));
    assert.equal(await page.locator(".select-reminder:checked").count(), 0);
    assert.equal(await page.locator(button).isDisabled(), true);
    await page.evaluate(data => renderReminders(data), data([item(1)], false));
    assert.equal(await page.locator("#crm-reminder-bulk").isHidden(), true);
    assert.equal(await page.locator(".select-reminder").count(), 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("bulk: exactly one POST for selected IDs, lock covers burst/single/cancel through refresh", async () => {
  const browser = await chromium.launch();
  let releasePost, releaseGet;
  const postGate = new Promise(resolve => { releasePost = resolve; });
  const getGate = new Promise(resolve => { releaseGet = resolve; });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    const { requests, errors } = await fixture(page, async (route, req) => {
      if (req.method() === "POST") { await postGate; return route.fulfill({ json: { concluidos: [1, 2], ignorados: [] } }); }
      await getGate; return route.fulfill({ json: data([]) });
    });
    page.on("dialog", dialog => dialog.accept());
    await page.locator(select).check();
    if (process.env.CRM_BULK_SCREENSHOT) {
      await page.selectOption('#crm-reminder-filter select[name="status"]', "");
      fs.mkdirSync(path.dirname(process.env.CRM_BULK_SCREENSHOT), { recursive: true });
      await page.locator(".crm-reminder-card").screenshot({ path: process.env.CRM_BULK_SCREENSHOT });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator(".crm-reminder-card").screenshot({ path: process.env.CRM_BULK_SCREENSHOT.replace(".png", "-mobile.png") });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      await page.setViewportSize({ width: 1280, height: 1000 });
    }
    await page.locator(button).click();
    await page.waitForFunction(() => completingReminder);
    const burst = () => page.evaluate(() => {
      for (let i = 0; i < 20; i++) for (const selector of ["#crm-reminder-complete-selected", ".complete-reminder", ".cancel-reminder-item", ".postpone-reminder"]) {
        document.querySelector(selector)?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      }
    });
    await burst();
    assert.equal(await page.locator(".complete-reminder:disabled").count(), 2);
    const refresh = page.waitForRequest(req => req.method() === "GET");
    releasePost(); await refresh; await burst();
    assert.equal(requests.filter(req => req.method === "POST").length, 1);
    assert.equal(requests.filter(req => req.method === "PATCH").length, 0);
    assert.deepEqual(requests[0].body, { ids: [1, 2] });
    releaseGet();
    await page.waitForFunction(() => !completingReminder);
    assert.match(await page.locator("#crm-reminder-bulk-message").textContent(), /2 lembrete\(s\) concluído/);
    assert.deepEqual(errors, []);
  } finally { releasePost(); releaseGet(); await browser.close(); }
});

for (const failure of ["429", "409", "network", "refresh"]) {
  test(`bulk: ${failure}, no retries or silent success; selection cleared`, async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      const { requests, errors } = await fixture(page, async (route, req) => {
        if (req.method() === "POST") {
          if (failure === "network") return route.abort();
          if (failure === "refresh") return route.fulfill({ json: { concluidos: [1], ignorados: [2] } });
          return route.fulfill({ status: Number(failure), headers: { "Retry-After": "47" }, json: { detail: "Falha de teste" } });
        }
        return route.fulfill({ status: failure === "refresh" ? 500 : 200, json: failure === "refresh" ? { detail: "Indisponível" } : data() });
      });
      page.on("dialog", dialog => dialog.accept());
      await page.locator(select).check(); await page.locator(button).click();
      await page.waitForFunction(() => !completingReminder && !document.querySelector("#crm-reminder-bulk-message").hidden);
      assert.equal(requests.filter(req => req.method === "POST").length, 1);
      assert.equal(requests.filter(req => req.method === "GET").length, 1);
      assert.equal(await page.locator(".select-reminder:checked").count(), 0);
      const message = await page.locator("#crm-reminder-bulk-message").textContent();
      if (failure === "429") assert.match(message, /47/);
      if (failure === "refresh") assert.match(message, /1 lembrete.*1 já.*Não foi possível atualizar/);
      assert.equal(await page.locator("#crm-reminder-bulk-message.error").count(), 1);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}

test("bulk: quoted titles remain literal in the accessible checkbox label", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const { errors } = await fixture(page);
    const title = `Contato "Empresa A" e 'Empresa B' <prioridade> & retorno`;
    await page.evaluate(data => renderReminders(data), data([{ ...item(9), titulo: title }]));
    const checkbox = page.locator('.select-reminder');
    assert.equal(await checkbox.getAttribute('aria-label'), `Selecionar lembrete: ${title}`);
    assert.equal(await checkbox.isVisible(), true);
    await checkbox.check();
    assert.equal(await checkbox.isChecked(), true);
    assert.deepEqual(await checkbox.evaluate(el => [...el.attributes].map(attr => attr.name).sort()), ['aria-label', 'class', 'data-id', 'type']);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

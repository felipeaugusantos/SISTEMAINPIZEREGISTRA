const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../../app/web");
const reminder = id => ({ id, status: "pendente", prioridade: "media", tipo_nome: "Retorno",
  titulo: "Lembrete de teste", cliente: "Cliente de teste", lembrar_em: "2026-11-18T12:00:00Z" });
const data = ids => ({ itens: ids.map(reminder), total: ids.length, tem_mais: false,
  acoes: { gerenciar: true }, metricas: {}, cadastros_para_atualizar: [] });

async function fixture(page, respond) {
  const errors = [], requests = [];
  page.on("pageerror", e => errors.push(e.message));
  const html = fs.readFileSync(path.join(web, "admin-crm.html"), "utf8")
    .replace(/<script[\s\S]*?<\/script>/g, "").replace(/<link[^>]+>/g, "");
  await page.route("**/*", async route => {
    const req = route.request();
    if (new URL(req.url()).pathname === "/admin/crm") return route.fulfill({ contentType: "text/html", body: html });
    requests.push({ method: req.method(), url: req.url(), body: req.postDataJSON() });
    await respond(route, req);
  });
  await page.goto("http://127.0.0.1:8000/admin/crm");
  for (const file of ["styles.css", "ze-brand.css", "admin-crm.css", "admin-crm-overrides.css", "admin-crm-reminders.css"]) {
    if (fs.existsSync(path.join(web, "static", file))) await page.addStyleTag({ path: path.join(web, "static", file) });
  }
  await page.evaluate(() => { window.createLeadDialog = () => ({}); });
  const source = fs.readFileSync(path.join(web, "static/admin-crm.js"), "utf8");
  // Skip unrelated initial dashboard loads; exercise the real renderer and event handler.
  await page.addScriptTag({ content: source.slice(0, source.indexOf("\nreferences().then(")) });
  await page.evaluate(data => renderReminders(data), data([1, 2]));
  return { errors, requests };
}
const tick = page => page.evaluate(() => new Promise(resolve => setTimeout(resolve, 0)));
const burst = page => page.evaluate(() => {
  for (let i = 0; i < 30; i++) document.querySelectorAll(".complete-reminder")[i % 2]
    .dispatchEvent(new MouseEvent("click", { bubbles: true }));
});

test("Concluir: one PATCH through bursts, re-render and list refresh; next reminder unlocks", async () => {
  const browser = await chromium.launch();
  let releasePatch, releaseList;
  const patchGate = new Promise(resolve => { releasePatch = resolve; });
  const listGate = new Promise(resolve => { releaseList = resolve; });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    let patches = 0, lists = 0;
    const { errors } = await fixture(page, async (route, req) => {
      if (req.method() === "PATCH") { patches++; await patchGate; return route.fulfill({ json: {} }); }
      lists++; await listGate; return route.fulfill({ json: data([2, 3]) });
    });
    await page.locator(".complete-reminder").first().click();
    await page.waitForFunction(() => document.querySelector(".complete-reminder").disabled);
    await burst(page);
    // Another filter/load may replace the DOM while the PATCH is pending.
    await page.evaluate(data => renderReminders(data), data([1, 2]));
    await burst(page);
    assert.equal(await page.locator(".complete-reminder:disabled").count(), 2);
    assert.equal(await page.locator(".complete-reminder[aria-busy=true]").count(), 2);
    await page.locator("#crm-reminders").screenshot({ path: "/downloads/crm-concluir-salvando.png" });
    const refreshed = page.waitForRequest(req => req.method() === "GET" && req.url().includes("/v1/admin/crm/lembretes"));
    releasePatch();
    await refreshed;
    await burst(page); await tick(page);
    assert.equal(patches, 1);
    assert.equal(lists, 1);
    releaseList();
    await page.waitForFunction(() => !document.querySelector(".complete-reminder").disabled);
    assert.equal(await page.locator(".complete-reminder").first().textContent(), "Concluir");
    await page.locator(".complete-reminder").first().click();
    await page.waitForFunction(() => !document.querySelector(".complete-reminder").disabled);
    assert.equal(patches, 2);
    assert.deepEqual(errors, []);
  } finally { releasePatch(); releaseList(); await browser.close(); }
});

for (const failure of ["PATCH 429", "PATCH network", "GET 500"]) {
  test(`Concluir: ${failure} restores buttons, reports error, no automatic retry`, async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      let fail = true;
      const { errors, requests } = await fixture(page, async (route, req) => {
        if (fail && failure === "PATCH network" && req.method() === "PATCH") return route.abort();
        if (fail && ((failure === "PATCH 429" && req.method() === "PATCH") || (failure === "GET 500" && req.method() === "GET"))) {
          return route.fulfill({ status: failure === "PATCH 429" ? 429 : 500, json: { detail: "Falha de teste" } });
        }
        return route.fulfill({ json: req.method() === "GET" ? data([1, 2]) : {} });
      });
      await page.locator(".complete-reminder").first().click();
      await page.waitForFunction(() => !document.querySelector("#crm-message").hidden);
      assert.equal(await page.locator(".complete-reminder:disabled").count(), 0);
      assert.equal(await page.locator(".complete-reminder").first().textContent(), "Concluir");
      await tick(page);
      assert.equal(requests.filter(req => req.method === "PATCH").length, 1);
      fail = false;
      await page.locator(".complete-reminder").first().click();
      await page.waitForFunction(() => !document.querySelector(".complete-reminder").disabled);
      assert.equal(requests.filter(req => req.method === "PATCH").length, 2);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}

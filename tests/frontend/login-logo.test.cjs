const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../../app/web");

async function fixture(page, marca = { propria: false }, origin = "http://login.test") {
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/v1/tenant/branding") return route.fulfill({ json: { marca } });
    if (url.pathname === "/v1/auth/social/providers") return route.fulfill({ json: { providers: [] } });
    const file = url.pathname === "/login" ? "login.html" : url.pathname.replace(/^\//, "");
    const local = path.join(web, file);
    if (fs.existsSync(local)) return route.fulfill({ path: local });
    return route.fulfill({ status: 404, body: "" });
  });
  await page.goto(origin + "/login");
}

test("logo oficial completa e sem filtros em ambos os temas e quatro larguras", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await fixture(page);
    for (const width of [1440, 820, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const dark of [false, true]) {
        await page.evaluate(value => document.documentElement.classList.toggle("dark", value), dark);
        const actual = await page.locator(".ops-login-logo").evaluate(async img => {
          await img.decode();
          const rect = img.getBoundingClientRect();
          const parent = img.parentElement.getBoundingClientRect();
          const style = getComputedStyle(img);
          return {
            source: img.getAttribute("src"), naturalWidth: img.naturalWidth,
            ratio: rect.width / rect.height, filter: style.filter,
            background: style.backgroundColor, padding: style.padding, border: style.borderWidth,
            within: rect.left >= parent.left && rect.right <= parent.right,
            overflow: document.documentElement.scrollWidth > innerWidth
          };
        });
        assert.match(actual.source, /ze-registra-logo-tema-escuro\.png\?v=1$/);
        assert.equal(actual.naturalWidth, 1688);
        assert.ok(Math.abs(actual.ratio - 1688 / 504) < 0.01);
        assert.ok(actual.within && !actual.overflow);
        assert.equal(actual.filter, "none");
        assert.equal(actual.background, "rgba(0, 0, 0, 0)");
        assert.equal(actual.padding, "0px");
        assert.equal(actual.border, "0px");
      }
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test("marca própria conserva cartão claro e nome do escritório sem logo", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await fixture(page, { propria: true, nome: "Escritório teste", logo_url: "/static/assets/logo-zeregistra.png" });
    await page.waitForFunction(() => document.querySelector(".ops-login-logo")?.getAttribute("src") === "/static/assets/logo-zeregistra.png");
    const style = await page.locator(".ops-login-logo").evaluate(img => ({
      background: getComputedStyle(img).backgroundColor, padding: getComputedStyle(img).padding, alt: img.alt
    }));
    assert.notEqual(style.background, "rgba(0, 0, 0, 0)");
    assert.equal(style.padding, "12px 18px");
    assert.equal(style.alt, "Escritório teste");
    await page.unrouteAll({ behavior: "wait" });
    await fixture(page, { propria: true, nome: "Escritório sem logo" });
    await page.waitForFunction(() => document.querySelector(".portal-brand-panel h2")?.textContent === "Escritório sem logo");
    assert.equal(await page.locator(".ops-login-logo").count(), 0);
  } finally { await browser.close(); }
});

test("domínio oficial mantém logo horizontal mesmo com avatar cadastrado", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await fixture(page, {
      propria: true, nome: "Marca cadastrada", logo_url: "/static/assets/logo-zeregistra.png"
    }, "https://app.zeregistra.com.br");
    await page.waitForFunction(() => document.title.includes("Marca cadastrada"));
    const img = page.locator(".ops-login-logo");
    assert.match(await img.getAttribute("src"), /ze-registra-logo-tema-escuro\.png\?v=1$/);
    await img.evaluate(el => el.decode());
    assert.equal(await img.evaluate(el => el.naturalWidth), 1688);
    assert.equal(await img.getAttribute("alt"), "Zé Registra");
  } finally { await browser.close(); }
});

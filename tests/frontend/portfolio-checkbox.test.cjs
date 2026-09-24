const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../../app/web");

test("Carteira: checkboxes compactos, alinhados e acessíveis", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const html = fs.readFileSync(path.join(web, "admin-carteira.html"), "utf8")
      .replace(/<script[\s\S]*?<\/script>/g, "")
      .replace(/<link[^>]+>/g, "");
    await page.setContent(html);
    for (const file of ["styles.css", "admin-carteira.css"]) {
      await page.addStyleTag({ path: path.join(web, "static", file) });
    }
    // Fixture do controle gerado por renderPortfolio, sem dados reais ou API.
    await page.evaluate(() => {
      document.querySelector("#portfolio-bulk-assign").hidden = false;
      document.querySelector("#portfolio-list").innerHTML =
        '<article class="portfolio-item"><div class="portfolio-process"><label class="portfolio-select-cell"><input type="checkbox" data-select-id="1" aria-label="Selecionar processo de teste"></label><h3>Marca de teste</h3></div></article>';
    });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const selector of ["#portfolio-select-page", "[data-select-id]"]) {
        const input = page.locator(selector);
        const size = await input.boundingBox();
        assert.equal(size.width, 18);
        assert.equal(size.height, 18);
        await input.focus();
        await page.keyboard.press("Space");
        assert.equal(await input.isChecked(), true);
        await page.keyboard.press("Space");
        assert.equal(await input.isChecked(), false);
        assert.equal(await input.evaluate(el => getComputedStyle(el).outlineStyle), "solid");
      }
      assert.equal(await page.locator(".portfolio-select-all").evaluate(el => getComputedStyle(el).display), "flex");
      assert.ok(await page.locator("#bulk-company").evaluate(el => el.getBoundingClientRect().height >= 40));
    }
  } finally { await browser.close(); }
});

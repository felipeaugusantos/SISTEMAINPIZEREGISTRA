const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
async function harness(page) {
  await page.setContent('<nav aria-label="Registros"><button id="prev">Anterior</button><span id="summary">123 registros</span><button id="next">Próxima</button></nav>');
  await page.addStyleTag({ path: path.join(web, 'static/admin-pagination.css') });
  await page.addScriptTag({ path: path.join(web, 'static/admin-pagination.js') });
  await page.evaluate(() => {
    window.state = { offset: 0, limit: 20 }; window.calls = []; window.fail = false;
    window.pager = ZRPagination.create({ id: 'test', previous: '#prev', next: '#next', sizes: [20,50,100,200],
      get: () => state, set: value => { state = value; }, load: async () => {
        calls.push({ ...state }); await new Promise(resolve => setTimeout(resolve, 70));
        if (fail) throw new TypeError('Failed to fetch'); pager.update(123);
      }
    }); pager.update(123);
  });
}
test('Seletores: primeira/última, entrada direta, tamanho, limites e cliques repetidos', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 900 } }); await harness(page);
    await page.locator('#test-last').click();
    await page.locator('#test-last').dispatchEvent('click');
    await page.waitForFunction(() => document.querySelector('nav').getAttribute('aria-busy') === 'false');
    assert.deepEqual(await page.evaluate(() => calls), [{ offset: 120, limit: 20 }]);
    await page.locator('#test-page-number').fill('99999'); await page.locator('#test-go').click();
    assert.equal(await page.locator('#test-page-number').inputValue(), '7');
    await page.locator('#test-first').click();
    await page.waitForFunction(() => document.querySelector('nav').getAttribute('aria-busy') === 'false');
    await page.locator('#test-page-number').fill('3'); await page.locator('#test-page-number').press('Enter');
    await page.waitForFunction(() => document.querySelector('nav').getAttribute('aria-busy') === 'false');
    assert.deepEqual(await page.evaluate(() => state), { offset: 40, limit: 20 });
    await page.locator('#test-page-size').selectOption('50');
    await page.waitForFunction(() => document.querySelector('nav').getAttribute('aria-busy') === 'false');
    assert.deepEqual(await page.evaluate(() => state), { offset: 0, limit: 50 });
    await page.locator('#next').click();
    await page.waitForFunction(() => document.querySelector('nav').getAttribute('aria-busy') === 'false');
    assert.deepEqual(await page.evaluate(() => state), { offset: 50, limit: 50 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  } finally { await browser.close(); }
});
test('Seletores: falha restaura página, permite tentar de novo e trata total desconhecido/vazio', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage(); await harness(page); await page.evaluate(() => { fail = true; });
    await page.locator('#next').click();
    await page.locator('.admin-pagination-error:not([hidden])').waitFor();
    assert.deepEqual(await page.evaluate(() => state), { offset: 0, limit: 20 });
    assert.equal(await page.locator('#next').isEnabled(), true);
    await page.evaluate(() => { fail = false; }); await page.locator('#next').click();
    await page.waitForFunction(() => document.querySelector('nav').getAttribute('aria-busy') === 'false');
    await page.evaluate(() => { pager.update(1); });
    await page.waitForFunction(() => state.offset === 0 && document.querySelector('nav').getAttribute('aria-busy') === 'false');
    await page.evaluate(() => { pager.update(null, true); });
    assert.equal(await page.locator('#test-last').isDisabled(), true);
    assert.equal(await page.locator('#next').isEnabled(), true);
    await page.evaluate(() => { pager.update(0); });
    assert.equal(await page.locator('#next').isDisabled(), true);
    assert.equal(await page.locator('#prev').isDisabled(), true);
  } finally { await browser.close(); }
});

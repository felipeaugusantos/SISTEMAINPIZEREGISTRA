const { test, expect } = require('@playwright/test');

// Sem leads reais: API e Turnstile simulados. O backend e o desafio real
// continuam cobertos separadamente pela homologação no domínio autorizado.
async function prepararPagina(page) {
  await page.route('**/v1/tenant/branding', route => route.fulfill({ json: {} }));
  await page.route('**/v1/tenant/captcha', route => route.fulfill({ json: { site_key: 'chave-de-teste' } }));
  await page.route('https://challenges.cloudflare.com/turnstile/v0/api.js?*', route => route.fulfill({
    contentType: 'application/javascript',
    body: `window.turnstile = {
      render(alvo) {
        const quadro = document.createElement('iframe');
        quadro.title = 'Turnstile simulado';
        quadro.width = '300'; quadro.height = '65';
        alvo.appendChild(quadro);
        return 'widget-teste';
      },
      getResponse() { return 'token-de-teste'; },
      reset() { window.reiniciosCaptcha = (window.reiniciosCaptcha || 0) + 1; }
    }; window.zeTurnstileCarregado();`,
  }));
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('.captcha-publico iframe')).toBeVisible();
}

for (const width of [390, 620, 860, 1440]) {
  test(`landing: pesquisa acessível sem estouro em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width > 860 ? 900 : 844 });
    await prepararPagina(page);
    const cta = page.getByRole('link', { name: 'Começar minha pesquisa', exact: true });
    await expect(cta).toBeInViewport({ ratio: 1 });
    const dimensoes = await page.evaluate(() => ({
      largura: innerWidth, conteudo: document.documentElement.scrollWidth,
      titulo: document.querySelector('h1').getBoundingClientRect().bottom,
      altura: innerHeight,
    }));
    expect(dimensoes.conteudo).toBeLessThanOrEqual(dimensoes.largura);
    expect(dimensoes.titulo).toBeLessThan(dimensoes.altura);
    await cta.click();
    await expect(page.locator('#brand-input')).toBeInViewport({ ratio: 1 });
    await expect(page.locator('#recovery-panel')).toBeHidden();
  });
}

for (const width of [390, 1440]) {
  test(`landing: validação, CAPTCHA, erro e envio em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await prepararPagina(page);
    const envios = [];
    await page.route('**/v1/pesquisas-marca', async route => {
      envios.push({ dados: route.request().postDataJSON(), headers: route.request().headers() });
      await route.fulfill(envios.length === 1
        ? { status: 422, json: { detail: 'Falha simulada. Tente novamente.' } }
        : { status: 201, json: { relatorio_url: '/relatorios/previa-local', relatorio_token: 'token-relatorio' } });
    });
    await page.route('**/relatorios/previa-local?download=1', route => route.fulfill({
      contentType: 'text/html', body: '<h1>Relatório simulado</h1>',
    }));
    const enviar = page.locator('#research-form button[type=submit]');
    await enviar.click();
    expect(envios).toHaveLength(0);
    await page.locator('#brand-input').fill('MARCA TESTE LOCAL');
    await page.locator('#business-activity').fill('Desenvolvimento de software');
    await page.locator('#contact-name').fill('Teste Local');
    await page.locator('#company').fill('Empresa Teste');
    await page.locator('#corporate-email').fill('teste@example.com');
    await page.locator('#corporate-phone').fill('16999990000');
    await page.locator('#privacy-consent').check();
    const posicaoCaptcha = await page.evaluate(() => {
      const captcha = document.querySelector('.captcha-publico');
      return captcha.nextElementSibling === document.querySelector('#research-form button[type=submit]');
    });
    expect(posicaoCaptcha).toBe(true);
    await enviar.click();
    await expect(page.locator('#form-status')).toContainText('Falha simulada');
    await expect(enviar).toBeEnabled();
    expect(await page.evaluate(() => window.reiniciosCaptcha)).toBe(1);
    expect(envios[0].headers['x-captcha-token']).toBe('token-de-teste');
    expect(envios[0].dados).toEqual({
      nome: 'Teste Local', empresa: 'Empresa Teste', email_corporativo: 'teste@example.com',
      telefone: '16999990000', marca: 'MARCA TESTE LOCAL', atividade: 'Desenvolvimento de software',
      aceite_privacidade: true, aceite_marketing: false, website: '',
    });
    await enviar.click();
    await expect(page).toHaveURL(/\/relatorios\/previa-local\?download=1#token=token-relatorio$/);
    expect(envios).toHaveLength(2);
  });
}

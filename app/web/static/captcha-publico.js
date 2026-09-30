// CAPTCHA da consulta pública (Cloudflare Turnstile) -- pendência da Fase 12,
// decisão do usuário em 29/09/2026. Só aparece quando o servidor informa uma
// chave do site (/v1/tenant/captcha); sem ela, o formulário segue como antes.
// Os formulários marcados com data-captcha recebem o widget antes do botão
// de envio, e as páginas mandam o token no cabeçalho X-Captcha-Token.
(() => {
  let widgetId = null;
  const pronto = (async () => {
    const formulario = document.querySelector("form[data-captcha]");
    if (!formulario) return;
    const resposta = await fetch("/v1/tenant/captcha").catch(() => null);
    if (!resposta?.ok) return;
    const { site_key: chaveSite } = await resposta.json().catch(() => ({}));
    if (!chaveSite) return;
    await new Promise((resolve, reject) => {
      window.zeTurnstileCarregado = resolve;
      const script = document.createElement("script");
      script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=zeTurnstileCarregado";
      script.async = true;
      script.onerror = reject;
      document.head.appendChild(script);
    });
    const alvo = document.createElement("div");
    alvo.className = "captcha-publico";
    const botao = formulario.querySelector("button[type='submit'], #submit");
    (botao || formulario.lastElementChild)?.before(alvo);
    widgetId = window.turnstile.render(alvo, { sitekey: chaveSite, language: "pt-br" });
  })().catch(() => { /* Sem o widget, o servidor recusa o envio com mensagem clara. */ });

  window.zeCaptcha = {
    pronto,
    cabecalhos() {
      if (widgetId === null || !window.turnstile) return {};
      return { "X-Captcha-Token": window.turnstile.getResponse(widgetId) || "" };
    },
    reiniciar() {
      if (widgetId !== null && window.turnstile) window.turnstile.reset(widgetId);
    },
  };
})();

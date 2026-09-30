const f = document.querySelector('#lead-form'), s = document.querySelector('#status'), b = document.querySelector('#submit');
f.addEventListener('submit', async e => {
  e.preventDefault();
  b.disabled = true;
  s.classList.add('visible');
  s.textContent = 'Enviando…';
  const d = Object.fromEntries(new FormData(f));
  d.origem = 'landing';
  d.aceite_privacidade = true;
  const p = new URLSearchParams(location.search);
  ['utm_source', 'utm_medium', 'utm_campaign'].forEach(k => { const v = p.get(k); if (v) d[k] = v });
  try {
    // CAPTCHA (Turnstile), quando ativo -- ver captcha-publico.js.
    const headers = { 'Content-Type': 'application/json', ...(window.zeCaptcha?.cabecalhos() || {}) };
    const r = await fetch('/v1/leads', { method: 'POST', headers, body: JSON.stringify(d) });
    if (!r.ok) {
      const erro = await r.json().catch(() => ({}));
      throw new Error(typeof erro.detail === 'string' ? erro.detail : '');
    }
    s.textContent = 'Recebemos seus dados. Em breve nossa equipe entrará em contato.';
    f.reset();
  } catch (erro) {
    s.textContent = erro.message || 'Não foi possível enviar agora. Tente novamente em instantes.';
  } finally {
    b.disabled = false;
    // O token do CAPTCHA vale uma vez.
    window.zeCaptcha?.reiniciar();
  }
});

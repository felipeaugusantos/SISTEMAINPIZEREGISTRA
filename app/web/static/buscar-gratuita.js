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
    const r = await fetch('/v1/leads', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) });
    if (!r.ok) throw new Error();
    s.textContent = 'Recebemos seus dados. Em breve nossa equipe entrará em contato.';
    f.reset();
  } catch (_) {
    s.textContent = 'Não foi possível enviar agora. Tente novamente em instantes.';
  } finally {
    b.disabled = false;
  }
});

(async () => {
  try {
    const response = await fetch("/v1/tenant/branding");
    if (!response.ok) return;
    const tenant = await response.json();
    const branding = tenant.branding || {};
    if (branding.cor_primaria && /^#[0-9a-f]{6}$/i.test(branding.cor_primaria)) {
      // CSP style-src estrito bloqueia element.style.setProperty (inline);
      // a cor é aplicada via folha de estilo carregada do próprio servidor ('self').
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/v1/tenant/branding.css";
      document.head.appendChild(link);
    }
    if (branding.nome_exibido) {
      document.querySelectorAll(".brand-wordmark").forEach(el => { el.textContent = branding.nome_exibido; });
    }
    if (branding.logo_url) {
      document.querySelectorAll(".brand-avatar").forEach(img => { img.src = branding.logo_url; });
    }
  } catch (_) { /* Mantém a identidade padrão se o tenant não puder ser resolvido. */ }
})();

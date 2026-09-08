(async () => {
  try {
    const response = await fetch("/v1/tenant/branding");
    if (!response.ok) return;
    const tenant = await response.json();
    if (tenant.cor_primaria && /^#[0-9a-f]{6}$/i.test(tenant.cor_primaria)) {
      // CSP style-src estrito bloqueia element.style.setProperty (inline);
      // a cor é aplicada via folha de estilo carregada do próprio servidor ('self').
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/v1/tenant/branding.css";
      document.head.appendChild(link);
    }
    if (tenant.nome_exibido) {
      document.querySelectorAll(".brand-wordmark").forEach(el => { el.textContent = tenant.nome_exibido; });
    }
    if (tenant.logo_url) {
      document.querySelectorAll(".brand-avatar").forEach(img => { img.src = tenant.logo_url; });
    }
  } catch (_) { /* Mantém a identidade padrão se o tenant não puder ser resolvido. */ }
})();

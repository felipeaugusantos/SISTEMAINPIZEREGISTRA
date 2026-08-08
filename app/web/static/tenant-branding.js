(async () => {
  try {
    const response = await fetch("/v1/tenant/branding");
    if (!response.ok) return;
    const tenant = await response.json();
    const branding = tenant.branding || {};
    if (branding.cor_primaria && /^#[0-9a-f]{6}$/i.test(branding.cor_primaria)) {
      document.documentElement.style.setProperty("--forest", branding.cor_primaria);
    }
    if (branding.nome_exibido) {
      document.querySelectorAll(".brand-wordmark").forEach(el => { el.textContent = branding.nome_exibido; });
    }
    if (branding.logo_url) {
      document.querySelectorAll(".brand-avatar").forEach(img => { img.src = branding.logo_url; });
    }
  } catch (_) { /* Mantém a identidade padrão se o tenant não puder ser resolvido. */ }
})();

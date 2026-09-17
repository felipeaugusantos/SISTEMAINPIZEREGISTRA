(async () => {
  try {
    const response = await fetch("/v1/tenant/branding");
    if (!response.ok) return;
    const tenant = await response.json();
    // Achado do usuário (17/09/2026): antes só carregava esta folha quando
    // havia cor_primaria -- uma organização com só logo_url (sem cor
    // customizada) nunca recebia a regra que tira o filtro de silhueta
    // branca da logo padrão (ver app/api/confiabilidade.py::branding_css).
    if ((tenant.cor_primaria && /^#[0-9a-f]{6}$/i.test(tenant.cor_primaria)) || tenant.logo_url) {
      // CSP style-src estrito bloqueia element.style.setProperty (inline);
      // a cor/filtro é aplicada via folha de estilo carregada do próprio servidor ('self').
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/v1/tenant/branding.css";
      document.head.appendChild(link);
    }
    if (tenant.nome_exibido) {
      document.querySelectorAll(".brand-wordmark").forEach(el => { el.textContent = tenant.nome_exibido; });
    }
    if (tenant.logo_url) {
      document.querySelectorAll(".brand-avatar").forEach(img => {
        img.referrerPolicy = "no-referrer";
        img.src = tenant.logo_url;
        img.addEventListener("error", () => {
          img.hidden = true;
          const fallback = img.closest(".portal-brand-logo")?.querySelector(".portal-brand-fallback");
          if (fallback) fallback.hidden = false;
        }, { once: true });
      });
    }
    document.querySelectorAll("[data-privacy-version]").forEach(el => {
      el.textContent = tenant.politica_privacidade_versao || "—";
    });
    const policyContainer = document.querySelector("#privacy-policy-dynamic");
    if (policyContainer) {
      const policyResponse = await fetch("/v1/tenant/politica-privacidade/vigente");
      if (policyResponse.ok) {
        const policy = await policyResponse.json();
        document.querySelectorAll("[data-privacy-version]").forEach(el => {
          el.textContent = policy.versao;
        });
        const defaultContent = document.querySelector("#privacy-policy-default");
        const content = document.querySelector("#privacy-policy-content");
        const reference = document.querySelector("#privacy-policy-reference");
        if (policy.conteudo) {
          content.textContent = policy.conteudo;
          defaultContent.hidden = true;
          policyContainer.hidden = false;
        } else if (policy.documento_referencia && policy.documento_referencia !== location.pathname) {
          reference.href = policy.documento_referencia;
          reference.referrerPolicy = "no-referrer";
          reference.hidden = false;
          defaultContent.hidden = true;
          policyContainer.hidden = false;
        }
      }
    }
  } catch (_) { /* Mantém a identidade padrão se o tenant não puder ser resolvido. */ }
})();

// Fase 19.4 (white-label): no domínio de outro escritório, a consulta
// pública e as páginas que a acompanham (início, sobre, contato,
// privacidade, relatório, processo, portal) trocam "Zé Registra" pelo nome
// do escritório em textos, atributos e título -- inclusive no conteúdo que
// os scripts das páginas montam depois (MutationObserver). A organização
// padrão sem marca própria ("propria" falso) não é tocada.
const MARCA_PLATAFORMA = /Z[ée] Registra®?|ZÉ REGISTRA®?/g;
const ATRIBUTOS_COM_TEXTO = ["alt", "title", "aria-label", "placeholder", "content"];

function aplicarNomeDoEscritorio(marca) {
  if (!marca?.propria || !marca.nome) return;
  const nome = marca.nome;
  // Nome que já contém a marca da plataforma: a troca seria inócua e
  // reacionaria o observador a cada mudança.
  const contemPlataforma = MARCA_PLATAFORMA.test(nome);
  MARCA_PLATAFORMA.lastIndex = 0;
  if (contemPlataforma) return;
  const trocar = texto => texto.replace(MARCA_PLATAFORMA, trecho => (trecho === trecho.toUpperCase() ? nome.toUpperCase() : nome));
  const trocarEm = raiz => {
    if (raiz.nodeType === Node.TEXT_NODE) {
      if (MARCA_PLATAFORMA.test(raiz.nodeValue)) raiz.nodeValue = trocar(raiz.nodeValue);
      MARCA_PLATAFORMA.lastIndex = 0;
      return;
    }
    if (raiz.nodeType !== Node.ELEMENT_NODE || ["SCRIPT", "STYLE"].includes(raiz.tagName)) return;
    // "Zé Registra<sup>®</sup>": o marcador fica sem sentido ao lado de outro nome.
    raiz.querySelectorAll?.(".brand-wordmark sup").forEach(sup => sup.remove());
    [raiz, ...raiz.querySelectorAll("*")].forEach(el => {
      ATRIBUTOS_COM_TEXTO.forEach(atributo => {
        const valor = el.getAttribute?.(atributo);
        if (valor && MARCA_PLATAFORMA.test(valor)) el.setAttribute(atributo, trocar(valor));
        MARCA_PLATAFORMA.lastIndex = 0;
      });
    });
    const caminhante = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    for (let no = caminhante.nextNode(); no; no = caminhante.nextNode()) {
      if (no.parentElement && ["SCRIPT", "STYLE"].includes(no.parentElement.tagName)) continue;
      if (MARCA_PLATAFORMA.test(no.nodeValue)) no.nodeValue = trocar(no.nodeValue);
      MARCA_PLATAFORMA.lastIndex = 0;
    }
  };
  trocarEm(document.head);
  trocarEm(document.body);
  document.title = trocar(document.title);
  new MutationObserver(mudancas => {
    mudancas.forEach(mudanca => {
      if (mudanca.type === "characterData") trocarEm(mudanca.target);
      mudanca.addedNodes.forEach(trocarEm);
    });
    if (MARCA_PLATAFORMA.test(document.title)) document.title = trocar(document.title);
    MARCA_PLATAFORMA.lastIndex = 0;
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
}

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
    aplicarNomeDoEscritorio(tenant.marca);
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

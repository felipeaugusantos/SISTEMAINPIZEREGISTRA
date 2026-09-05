const adminSections = [
  { id: "overview", label: "Visão geral", href: "/admin", symbol: "VG", permission: "dashboard.view" },
  { id: "consulta", label: "Consulta de marcas", href: "/admin/consulta", symbol: "CM", permission: "leads.view" },
  { id: "figurativa", label: "Busca figurativa", href: "/admin/figurativa", symbol: "BF", permission: "leads.view" },
  { id: "leads", label: "Leads, pesquisas e análises", href: "/admin/pesquisas", symbol: "AN", permission: "leads.view" },
  { id: "crm", label: "CRM", href: "/admin/crm", symbol: "CR", permission: "leads.view" },
  { id: "prospeccao", label: "Radar de Prospecção", href: "/admin/prospeccao", symbol: "RP", permission: "prospeccao.view" },
  { id: "portfolio", label: "Processos monitorados", href: "/admin/processos-monitorados", symbol: "PM", permission: "portfolio.view" },
  { id: "legal", label: "Operação jurídica", href: "/admin/operacao-juridica", symbol: "OJ", permission: "legal.view" },
  { id: "finance", label: "Financeiro", href: "/admin/financeiro", symbol: "FI", permission: "finance.view" },
  { id: "finance-payable", label: "Contas a pagar", href: "/admin/financeiro/contas-a-pagar", symbol: "CP", permission: "finance.view", parent: "finance" },
  { id: "finance-receivable", label: "Contas a receber", href: "/admin/financeiro/contas-a-receber", symbol: "CR", permission: "finance.view", parent: "finance" },
  { id: "finance-payment-methods", label: "Formas de pagamento", href: "/admin/financeiro/formas-pagamento", symbol: "FP", permission: "finance.view", parent: "finance" },
  { id: "finance-retribuicoes", label: "Retribuições INPI", href: "/admin/financeiro/retribuicoes", symbol: "RI", permission: "finance.view", parent: "finance" },
  { id: "finance-plano-contas", label: "Plano de contas & DRE", href: "/admin/financeiro/plano-contas", symbol: "PC", permission: "finance.view", parent: "finance" },
  { id: "finance-lucratividade", label: "Lucratividade", href: "/admin/financeiro/lucratividade", symbol: "LU", permission: "finance.view", parent: "finance" },
  { id: "finance-comissoes", label: "Comissões", href: "/admin/financeiro/comissoes", symbol: "CS", permission: "finance.view", parent: "finance" },
  { id: "finance-conciliacao", label: "Conciliação bancária", href: "/admin/financeiro/conciliacao", symbol: "CB", permission: "finance.view", parent: "finance" },
  { id: "finance-nfse", label: "NFS-e", href: "/admin/financeiro/nfse", symbol: "NF", permission: "finance.view", parent: "finance" },
  { id: "production", label: "Produção e auditoria", href: "/admin/producao", symbol: "PR", permission: "production.view" },
  { id: "finance-log", label: "Log Financeiro", href: "/admin/producao/log-financeiro", symbol: "LF", permission: "finance.view", parent: "production", profiles: ["administrador", "tech", "ceo", "financeiro"] },
  { id: "reliability", label: "Confiabilidade e LGPD", href: "/admin/confiabilidade", symbol: "CF", permission: "production.manage" },
  { id: "observability", label: "Observabilidade", href: "/admin/observabilidade", symbol: "OB", permission: "production.view", profiles: ["administrador", "tech"] },
  { id: "configuracao", label: "Configuração", href: "/admin/configuracao/regras-automaticas", symbol: "CG", permission: "leads.view" },
  { id: "config-regras", label: "Regras automáticas", href: "/admin/configuracao/regras-automaticas", symbol: "RA", permission: "leads.view", parent: "configuracao" },
  { id: "config-propostas", label: "Modelo de propostas", href: "/admin/configuracao/modelo-propostas", symbol: "PR", permission: "leads.view", parent: "configuracao" },
  { id: "config-rpi", label: "Consulta RPI", href: "/admin/configuracao/consulta-rpi?v=7", symbol: "RPI", permission: "rpi.view", parent: "configuracao" },
  { id: "config-clicksign", label: "Clicksign", href: "/admin/configuracao/clicksign", symbol: "CS", permission: "production.view", parent: "configuracao" },
  { id: "config-onboarding", label: "Onboarding SaaS", href: "/admin/configuracao/onboarding", symbol: "ON", permission: "dashboard.view", parent: "configuracao", superadmin: true },
  { id: "users", label: "Usuários e acessos", href: "/admin/usuarios", symbol: "UA", permission: "users.view" },
  { id: "saas", label: "Empresas e planos", href: "/admin/saas", symbol: "SA", superadmin: true },
];

function readCookie(name) { return decodeURIComponent(document.cookie.split("; ").find(x => x.startsWith(`${name}=`))?.split("=").slice(1).join("=") || ""); }
const originalFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const method = (init.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) init.headers = { ...(init.headers || {}), "X-CSRF-Token": readCookie("zr_csrf") };
  let response = await originalFetch(input, init);
  if (response.status === 401) { location.href = "/login"; return response; }
  if (response.status === 403 && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    const error = await response.clone().json().catch(() => ({}));
    if (String(error.detail || "").toLowerCase().includes("csrf")) {
      const renewal = await originalFetch("/v1/auth/csrf");
      if (renewal.ok) {
        const { csrf_token: token } = await renewal.json();
        init.headers = { ...(init.headers || {}), "X-CSRF-Token": token };
        response = await originalFetch(input, init);
      }
    }
  }
  return response;
};

function createAdminShell() {
  const routeSection = adminSections.find(section => section.href === location.pathname)?.id;
  const activeSection = routeSection || document.body.dataset.adminSection || "overview";
  document.querySelector(".topbar")?.remove();

  const sidebar = document.createElement("aside");
  sidebar.className = "admin-sidebar";
  sidebar.id = "admin-sidebar";
  sidebar.innerHTML = `
    <a class="admin-sidebar-brand" href="/admin">
      <img class="brand-avatar" src="/static/assets/personagem.png" alt="" />
      <span><strong class="brand-wordmark">Zé Registra<sup>®</sup></strong><small>Centro de operações</small></span>
    </a>
    <button id="admin-sidebar-toggle" class="admin-sidebar-toggle" type="button" aria-controls="admin-sidebar" aria-expanded="true" title="Recolher menu">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"></polyline></svg>
      <b class="visually-hidden">Recolher menu lateral</b>
    </button>
    <nav class="admin-sidebar-nav" aria-label="Navegação administrativa">
      <p>Operação</p>
      ${adminSections.map((section) => {
        const link = `<a href="${section.href}" data-permission="${section.permission}" title="${section.label}" ${section.parent ? `data-submenu-parent="${section.parent}"` : ""} class="${section.parent ? "admin-nav-subitem " : ""}${section.id === activeSection ? "active" : ""}" ${section.id === activeSection ? 'aria-current="page"' : ""}>
          <span class="admin-nav-symbol" aria-hidden="true">${section.symbol}</span>
          <span>${section.label}</span>
        </a>`;
        if (!["finance", "production", "configuracao"].includes(section.id)) return link;
        return `<div class="admin-nav-parent-row">${link}<button class="admin-submenu-toggle" type="button" data-submenu-toggle="${section.id}" aria-expanded="true"><span aria-hidden="true">⌄</span><b class="visually-hidden">Recolher submenu ${section.label}</b></button></div>`;
      }).join("")}
    </nav>
    <div class="admin-sidebar-footer">
      <span><i aria-hidden="true"></i> Ambiente interno</span>
      <small id="admin-current-user">Carregando conta…</small>
      <button id="admin-logout" type="button">Sair</button>
      <a href="/" target="_blank" rel="noopener">Abrir consulta pública</a>
      <a href="/docs" target="_blank" rel="noopener">API (documentação)</a>
    </div>
  `;

  const mobileHeader = document.createElement("header");
  mobileHeader.className = "admin-mobile-header";
  mobileHeader.innerHTML = `
    <a class="brand" href="/admin"><img class="brand-avatar" src="/static/assets/personagem.png" alt="" /><span class="brand-wordmark">Zé Registra<sup>®</sup></span></a>
    <button class="admin-menu-toggle" type="button" aria-controls="admin-sidebar" aria-expanded="false">
      <span></span><span></span><span></span><b class="visually-hidden">Abrir menu</b>
    </button>
  `;

  const overlay = document.createElement("button");
  overlay.className = "admin-sidebar-overlay";
  overlay.type = "button";
  overlay.setAttribute("aria-label", "Fechar menu");

  function setMenu(open) {
    document.body.classList.toggle("admin-menu-open", open);
    mobileHeader.querySelector(".admin-menu-toggle").setAttribute("aria-expanded", String(open));
  }

  mobileHeader.querySelector(".admin-menu-toggle").addEventListener("click", () => {
    setMenu(!document.body.classList.contains("admin-menu-open"));
  });
  overlay.addEventListener("click", () => setMenu(false));
  sidebar.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-submenu-toggle]");
    if (toggle) {
      const parent = toggle.dataset.submenuToggle;
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      setSubmenu(parent, !expanded);
      localStorage.setItem(`zr_admin_submenu_${parent}`, expanded ? "closed" : "open");
      return;
    }
    if (event.target.closest("a")) setMenu(false);
  });

  function setSubmenu(parent, expanded) {
    const toggle = sidebar.querySelector(`[data-submenu-toggle="${parent}"]`);
    const children = sidebar.querySelectorAll(`[data-submenu-parent="${parent}"]`);
    toggle?.setAttribute("aria-expanded", String(expanded));
    const label = adminSections.find(section => section.id === parent)?.label || parent;
    toggle?.querySelector("b")?.replaceChildren(document.createTextNode(`${expanded ? "Recolher" : "Expandir"} submenu ${label}`));
    children.forEach(link => { link.hidden = !expanded; });
  }

  function setCollapsed(collapsed) {
    document.body.classList.toggle("admin-sidebar-collapsed", collapsed);
    const toggle = sidebar.querySelector("#admin-sidebar-toggle");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.title = collapsed ? "Expandir menu" : "Recolher menu";
    toggle.querySelector("b").textContent = collapsed ? "Expandir menu lateral" : "Recolher menu lateral";
  }
  sidebar.querySelector("#admin-sidebar-toggle").addEventListener("click", () => {
    const collapsed = !document.body.classList.contains("admin-sidebar-collapsed");
    setCollapsed(collapsed);
    localStorage.setItem("zr_admin_sidebar_collapsed", collapsed ? "1" : "0");
  });
  setCollapsed(localStorage.getItem("zr_admin_sidebar_collapsed") === "1");

  const financeHasActiveChild = ["finance-payable", "finance-receivable", "finance-payment-methods"].includes(activeSection);
  const financePreference = localStorage.getItem("zr_admin_submenu_finance");
  setSubmenu("finance", financeHasActiveChild || financePreference !== "closed");
  const productionHasActiveChild = activeSection === "finance-log";
  const productionPreference = localStorage.getItem("zr_admin_submenu_production");
  setSubmenu("production", productionHasActiveChild || productionPreference !== "closed");
  const configHasActiveChild = ["config-regras", "config-rpi"].includes(activeSection);
  const configPreference = localStorage.getItem("zr_admin_submenu_configuracao");
  setSubmenu("configuracao", configHasActiveChild || configPreference !== "closed");

  document.body.prepend(overlay);
  document.body.prepend(mobileHeader);
  document.body.prepend(sidebar);
}

createAdminShell();

originalFetch("/v1/auth/me").then(async response => {
  if (!response.ok) { location.href = "/login"; return; }
  const user = await response.json();
  document.querySelector("#admin-current-user").textContent = `${user.nome} · ${user.organizacao?.nome || user.perfil}`;
  document.querySelectorAll(".admin-sidebar-brand, .admin-mobile-header .brand").forEach(link => {
    link.href = user.destino || "/admin";
  });
  document.querySelectorAll(".admin-sidebar-nav a").forEach((link) => {
    const section = adminSections.find(item => item.href === link.getAttribute("href"));
    if (section?.id === "production" && user.perfil === "financeiro") {
      link.href = "/admin/producao/log-financeiro";
      link.dataset.permission = "finance.view";
      return;
    }
    if (section?.profiles && !section.profiles.includes(user.perfil)) link.remove();
    else if (section?.superadmin && !user.superadmin) link.remove();
    else if (!section?.superadmin && user.perfil !== "administrador" && !user.superadmin && !user.permissoes.includes(link.dataset.permission)) link.remove();
  });
  document.querySelectorAll(".admin-nav-parent-row").forEach(row => {
    if (!row.querySelector("a")) row.remove();
    const toggle = row.querySelector("[data-submenu-toggle]");
    if (toggle && !document.querySelector(`[data-submenu-parent="${toggle.dataset.submenuToggle}"]`)) toggle.remove();
  });
});
document.querySelector("#admin-logout").addEventListener("click", async () => {
  await fetch("/v1/auth/logout", { method: "POST" }); location.href = "/login";
});

// Corrige textos legados com mojibake sem alterar dados persistidos.
function normalizarEncodingVisual() {
  const mapa = { "Ã§": "ç", "Ã£": "ã", "Ã¡": "á", "Ã©": "é", "Ã³": "ó", "Ãº": "ú", "Ã‰": "É", "Ãš": "Ú", "Ã§Ã£o": "ção", "â€”": "—", "â€“": "–", "â€¦": "…", "Â·": "·", "Ã—": "×" };
  const corrigir = (valor) => Object.entries(mapa).reduce((texto, [ruim, bom]) => texto.split(ruim).join(bom), valor);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const textos = [];
  while (walker.nextNode()) textos.push(walker.currentNode);
  textos.forEach(node => { node.nodeValue = corrigir(node.nodeValue); });
  document.querySelectorAll("[title], [aria-label], input[placeholder]").forEach(el => {
    ["title", "aria-label", "placeholder"].forEach(attr => { if (el.hasAttribute(attr)) el.setAttribute(attr, corrigir(el.getAttribute(attr))); });
  });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", normalizarEncodingVisual, { once: true });
else normalizarEncodingVisual();
new MutationObserver(() => normalizarEncodingVisual()).observe(document.body, { childList: true, subtree: true });

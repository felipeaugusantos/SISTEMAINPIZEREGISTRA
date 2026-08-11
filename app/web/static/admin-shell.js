const adminSections = [
  { id: "overview", label: "Visão geral", href: "/admin", symbol: "VG", permission: "dashboard.view" },
  { id: "consulta", label: "Consulta de marcas", href: "/admin/consulta", symbol: "CM", permission: "leads.view" },
  { id: "leads", label: "Leads, pesquisas e análises", href: "/admin/pesquisas", symbol: "AN", permission: "leads.view" },
  { id: "portfolio", label: "Processos monitorados", href: "/admin/processos-monitorados", symbol: "PM", permission: "portfolio.view" },
  { id: "finance", label: "Financeiro", href: "/admin/financeiro", symbol: "FI", permission: "finance.view" },
  { id: "production", label: "Produção e auditoria", href: "/admin/producao", symbol: "PR", permission: "production.view" },
  { id: "reliability", label: "Confiabilidade e LGPD", href: "/admin/confiabilidade", symbol: "CF", permission: "production.manage" },
  { id: "users", label: "Usuários e acessos", href: "/admin/usuarios", symbol: "UA", permission: "users.view" },
  { id: "saas", label: "Empresas e planos", href: "/admin/saas", symbol: "SA", superadmin: true },
];

function readCookie(name) { return decodeURIComponent(document.cookie.split("; ").find(x => x.startsWith(`${name}=`))?.split("=").slice(1).join("=") || ""); }
const originalFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const method = (init.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) init.headers = { ...(init.headers || {}), "X-CSRF-Token": readCookie("zr_csrf") };
  let response = await originalFetch(input, init);
  if (response.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname)}`; return response; }
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
  const activeSection = document.body.dataset.adminSection || "overview";
  document.querySelector(".topbar")?.remove();

  const sidebar = document.createElement("aside");
  sidebar.className = "admin-sidebar";
  sidebar.id = "admin-sidebar";
  sidebar.innerHTML = `
    <a class="admin-sidebar-brand" href="/admin">
      <img class="brand-avatar" src="/static/assets/personagem.png" alt="" />
      <span><strong class="brand-wordmark">Zé Registra<sup>®</sup></strong><small>Centro de operações</small></span>
    </a>
    <nav class="admin-sidebar-nav" aria-label="Navegação administrativa">
      <p>Operação</p>
      ${adminSections.map((section) => `
        <a href="${section.href}" data-permission="${section.permission}" class="${section.id === activeSection ? "active" : ""}" ${section.id === activeSection ? 'aria-current="page"' : ""}>
          <span class="admin-nav-symbol" aria-hidden="true">${section.symbol}</span>
          <span>${section.label}</span>
        </a>
      `).join("")}
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
    if (event.target.closest("a")) setMenu(false);
  });

  document.body.prepend(overlay);
  document.body.prepend(mobileHeader);
  document.body.prepend(sidebar);
}

createAdminShell();

originalFetch("/v1/auth/me").then(async response => {
  if (!response.ok) { location.href = `/login?next=${encodeURIComponent(location.pathname)}`; return; }
  const user = await response.json();
  document.querySelector("#admin-current-user").textContent = `${user.nome} · ${user.organizacao?.nome || user.perfil}`;
  document.querySelectorAll(".admin-sidebar-nav a").forEach((link) => {
    const section = adminSections.find(item => item.href === link.getAttribute("href"));
    if (section?.superadmin && !user.superadmin) link.remove();
    else if (!section?.superadmin && user.perfil !== "administrador" && !user.superadmin && !user.permissoes.includes(link.dataset.permission)) link.remove();
  });
});
document.querySelector("#admin-logout").addEventListener("click", async () => {
  await fetch("/v1/auth/logout", { method: "POST" }); location.href = "/login";
});

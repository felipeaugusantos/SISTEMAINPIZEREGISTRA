const adminSections = [
  { id: "overview", label: "Visão geral", href: "/admin", symbol: "VG", permission: "dashboard.view" },
  { id: "updates", label: "Atualizações", href: "/admin/atualizacoes", symbol: "AT", permission: "dashboard.view" },
  { id: "reports", label: "Relatórios", href: "/admin/relatorios", symbol: "RE", permissions: ["leads.view", "crm.view", "finance.view", "legal.view", "portfolio.view"] },
  { id: "comercial", label: "Comercial", href: "/admin/consulta", symbol: "CO", permission: "leads.view" },
  { id: "consulta", label: "Consulta de marcas", href: "/admin/consulta", symbol: "CM", permission: "leads.view", parent: "comercial" },
  { id: "figurativa", label: "Busca figurativa", href: "/admin/figurativa", symbol: "BF", permission: "leads.view", parent: "comercial" },
  { id: "leads", label: "Leads, pesquisas e análises", href: "/admin/pesquisas", symbol: "AN", permission: "leads.view", parent: "comercial" },
  { id: "crm", label: "CRM", href: "/admin/crm", symbol: "CR", permission: "leads.view", parent: "comercial" },
  { id: "assistente", label: "Zezinho das Marcas", href: "/admin/assistente", symbol: "IA", permission: "*", parent: "comercial" },
  { id: "prospeccao", label: "Radar de Prospecção", href: "/admin/prospeccao", symbol: "RP", permission: "prospeccao.view", parent: "comercial" },
  { id: "finance", label: "Financeiro", href: "/admin/financeiro", symbol: "FI", permission: "finance.view" },
  { id: "finance-payable", label: "Contas a pagar", href: "/admin/financeiro/contas-a-pagar", symbol: "CP", permission: "finance.view", parent: "finance" },
  { id: "finance-receivable", label: "Contas a receber", href: "/admin/financeiro/contas-a-receber", symbol: "CR", permission: "finance.view", parent: "finance" },
  { id: "finance-categorias", label: "Categorias", href: "/admin/financeiro/categorias", symbol: "CT", permission: "finance.view", parent: "finance" },
  { id: "finance-payment-methods", label: "Formas de pagamento", href: "/admin/financeiro/formas-pagamento", symbol: "FP", permission: "finance.view", parent: "finance" },
  { id: "finance-retribuicoes", label: "Retribuições INPI", href: "/admin/financeiro/retribuicoes", symbol: "RI", permission: "finance.view", parent: "finance" },
  { id: "finance-plano-contas", label: "Plano de contas & DRE", href: "/admin/financeiro/plano-contas", symbol: "PC", permission: "finance.view", parent: "finance" },
  { id: "finance-lucratividade", label: "Lucratividade", href: "/admin/financeiro/lucratividade", symbol: "LU", permission: "finance.view", parent: "finance" },
  { id: "finance-comissoes", label: "Comissões", href: "/admin/financeiro/comissoes", symbol: "CS", permission: "finance.view", parent: "finance" },
  { id: "finance-conciliacao", label: "Conciliação bancária", href: "/admin/financeiro/conciliacao", symbol: "CB", permission: "finance.view", parent: "finance" },
  { id: "finance-nfse", label: "NFS-e", href: "/admin/financeiro/nfse", symbol: "NF", permission: "finance.view", parent: "finance" },
  { id: "juridico", label: "Jurídico", href: "/admin/processos-monitorados", symbol: "JU", permission: "portfolio.view" },
  { id: "portfolio", label: "Processos monitorados", href: "/admin/processos-monitorados", symbol: "PM", permission: "portfolio.view", parent: "juridico" },
  { id: "legal", label: "Operação jurídica", href: "/admin/operacao-juridica", symbol: "OJ", permission: "legal.view", parent: "juridico" },
  { id: "production", label: "Produção e auditoria", href: "/admin/producao", symbol: "PR", permission: "production.view" },
  { id: "finance-log", label: "Log Financeiro", href: "/admin/producao/log-financeiro", symbol: "LF", permission: "finance.view", parent: "production", profiles: ["administrador", "tech", "ceo", "financeiro"] },
  { id: "configuracao", label: "Configuração", href: "/admin/configuracao/regras-automaticas", symbol: "CG", permission: "leads.view" },
  { id: "reliability", label: "Confiabilidade e LGPD", href: "/admin/confiabilidade", symbol: "CF", permission: "production.manage", parent: "configuracao" },
  { id: "observability", label: "Observabilidade", href: "/admin/observabilidade", symbol: "OB", permission: "production.view", profiles: ["administrador", "tech"], parent: "configuracao" },
  { id: "config-regras", label: "Regras automáticas", href: "/admin/configuracao/regras-automaticas", symbol: "RA", permission: "leads.view", parent: "configuracao" },
  { id: "config-propostas", label: "Modelo de propostas", href: "/admin/configuracao/modelo-propostas", symbol: "PR", permission: "leads.view", parent: "configuracao" },
  { id: "config-email-leads", label: "Modelo de e-mail (leads)", href: "/admin/configuracao/modelo-email-leads", symbol: "EM", permission: "leads.view", parent: "configuracao" },
  { id: "config-script-atendimento", label: "Script de atendimento", href: "/admin/configuracao/script-atendimento", symbol: "SC", permission: "leads.view", parent: "configuracao" },
  { id: "config-rpi", label: "Consulta RPI", href: "/admin/configuracao/consulta-rpi?v=7", symbol: "RPI", permission: "rpi.view", parent: "configuracao" },
  { id: "config-clicksign", label: "Clicksign", href: "/admin/configuracao/clicksign", symbol: "CS", permission: "production.view", parent: "configuracao" },
  { id: "config-onboarding", label: "Onboarding SaaS", href: "/admin/configuracao/onboarding", symbol: "ON", permission: "dashboard.view", parent: "configuracao", superadmin: true },
  { id: "config-feature-flags", label: "Feature flags", href: "/admin/feature-flags", symbol: "FF", permission: "dashboard.view", parent: "configuracao", superadmin: true },
  { id: "users", label: "Usuários e acessos", href: "/admin/usuarios", symbol: "UA", permission: "users.view", parent: "configuracao" },
  { id: "saas", label: "Empresas e planos", href: "/admin/saas", symbol: "SA", superadmin: true },
];

// Ícones dos itens de nível superior do menu (achado do usuário: a
// abreviação de 2 letras é difícil de reconhecer de relance com o menu
// recolhido -- só os itens de nível superior ficam visíveis nesse estado,
// os de submenu somem por completo). Mesmo estilo já usado no ícone do
// botão de recolher (viewBox 0 0 24 24, stroke=currentColor). Itens sem
// entrada aqui (submenus) continuam com a abreviação de texto.
const ADMIN_NAV_ICONS = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  updates: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
  reports: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/><path d="M8 7h8M8 11h8M8 15h5"/>',
  comercial: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  finance: '<rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
  juridico: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
  production: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  configuracao: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
  saas: '<rect x="4" y="3" width="16" height="18" rx="1"/><rect x="8" y="7" width="2" height="2"/><rect x="14" y="7" width="2" height="2"/><rect x="8" y="12" width="2" height="2"/><rect x="14" y="12" width="2" height="2"/><rect x="10" y="17" width="4" height="4"/>',
};

function navSymbolMarkup(section) {
  const icone = ADMIN_NAV_ICONS[section.id];
  if (!icone) return section.symbol;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icone}</svg>`;
}

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
          <span class="admin-nav-symbol${ADMIN_NAV_ICONS[section.id] ? " admin-nav-icon" : ""}" aria-hidden="true">${navSymbolMarkup(section)}</span>
          <span>${section.label}</span>
        </a>`;
        if (!["comercial", "finance", "juridico", "production", "configuracao"].includes(section.id)) return link;
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

  const comercialHasActiveChild = ["consulta", "figurativa", "leads", "crm", "prospeccao"].includes(activeSection);
  const comercialPreference = localStorage.getItem("zr_admin_submenu_comercial");
  setSubmenu("comercial", comercialHasActiveChild || comercialPreference !== "closed");
  const financeHasActiveChild = ["finance-payable", "finance-receivable", "finance-payment-methods"].includes(activeSection);
  const financePreference = localStorage.getItem("zr_admin_submenu_finance");
  setSubmenu("finance", financeHasActiveChild || financePreference !== "closed");
  const juridicoHasActiveChild = ["portfolio", "legal"].includes(activeSection);
  const juridicoPreference = localStorage.getItem("zr_admin_submenu_juridico");
  setSubmenu("juridico", juridicoHasActiveChild || juridicoPreference !== "closed");
  const productionHasActiveChild = activeSection === "finance-log";
  const productionPreference = localStorage.getItem("zr_admin_submenu_production");
  setSubmenu("production", productionHasActiveChild || productionPreference !== "closed");
  const configHasActiveChild = [
    "config-regras",
    "config-propostas",
    "config-rpi",
    "config-clicksign",
    "config-onboarding",
    "config-feature-flags",
    "reliability",
    "observability",
    "users",
  ].includes(activeSection);
  const configPreference = localStorage.getItem("zr_admin_submenu_configuracao");
  setSubmenu("configuracao", configHasActiveChild || configPreference !== "closed");

  document.body.prepend(overlay);
  document.body.prepend(mobileHeader);
  document.body.prepend(sidebar);
}

createAdminShell();

// Assistente (IA) -- widget flutuante global (achado do usuário,
// 11/09/2026): disponível em toda tela admin, para qualquer usuário
// autenticado. Não duplica a tela dedicada (/admin/assistente): lá o
// lançador flutuante fica escondido, a página já é o chat em tela cheia.
function createAssistenteWidget() {
  if (document.body.dataset.adminSection === "assistente") return;
  const historico = [];
  let aberto = false;

  const lancador = document.createElement("button");
  lancador.type = "button";
  lancador.className = "assistente-widget-launcher";
  lancador.setAttribute("aria-label", "Abrir Zezinho das Marcas");
  lancador.innerHTML = '<img src="/static/assets/personagem.png" alt="" />';

  const painel = document.createElement("section");
  painel.className = "assistente-widget-panel";
  painel.hidden = true;
  painel.innerHTML = `
    <header class="assistente-widget-header">
      <strong>Zezinho das Marcas</strong>
      <button type="button" data-widget-fechar aria-label="Fechar">×</button>
    </header>
    <div class="assistente-widget-body" id="assistente-widget-body">
      <div class="assistente-widget-msg assistente-widget-msg-model"><p>Oi! Eu sou o Zezinho das Marcas. Pergunte sobre os leads da sua organização.</p></div>
    </div>
    <form class="assistente-widget-form" id="assistente-widget-form">
      <textarea id="assistente-widget-input" maxlength="500" rows="1" placeholder="Digite sua pergunta…" required></textarea>
      <button type="submit">Enviar</button>
    </form>
  `;

  document.body.append(lancador, painel);

  const corpo = painel.querySelector("#assistente-widget-body");
  const form = painel.querySelector("#assistente-widget-form");
  const input = painel.querySelector("#assistente-widget-input");

  function escaparHtml(texto) {
    const node = document.createElement("span");
    node.textContent = texto ?? "";
    return node.innerHTML;
  }

  function adicionarMensagem(papel, texto) {
    const bloco = document.createElement("div");
    bloco.className = `assistente-widget-msg assistente-widget-msg-${papel}`;
    bloco.innerHTML = `<p>${escaparHtml(texto).replace(/\n/g, "<br>")}</p>`;
    corpo.appendChild(bloco);
    corpo.scrollTop = corpo.scrollHeight;
    return bloco;
  }

  function alternarPainel(mostrar) {
    aberto = mostrar ?? !aberto;
    painel.hidden = !aberto;
    if (aberto) input.focus();
  }

  lancador.addEventListener("click", () => alternarPainel());
  painel.querySelector("[data-widget-fechar]").addEventListener("click", () => alternarPainel(false));

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const pergunta = input.value.trim();
    if (!pergunta) return;
    adicionarMensagem("user", pergunta);
    input.value = "";
    const botao = form.querySelector("button");
    input.disabled = true;
    botao.disabled = true;
    const carregando = adicionarMensagem("model", "Consultando…");
    carregando.classList.add("assistente-widget-msg-loading");
    try {
      const response = await fetch("/v1/admin/assistente/perguntar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pergunta, historico }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Não foi possível consultar o assistente.");
      carregando.remove();
      adicionarMensagem("model", data.resposta);
      historico.push({ role: "user", texto: pergunta }, { role: "model", texto: data.resposta });
      if (historico.length > 12) historico.splice(0, historico.length - 12);
    } catch (error) {
      carregando.remove();
      adicionarMensagem("error", error.message || "Não foi possível consultar o assistente.");
    } finally {
      input.disabled = false;
      botao.disabled = false;
      input.focus();
    }
  });
}
createAssistenteWidget();

// Calculadora simples -- widget flutuante restrito ao módulo financeiro
// (pedido do usuário, 22/09/2026): aritmética básica, sem chamada nenhuma
// ao backend, pra ajudar o operador a conferir valores antes de lançar
// uma conta. Fica ao lado do lançador do Zezinho (bottom-right).
function createCalculadoraWidget() {
  if (document.body.dataset.adminSection !== "finance") return;

  const lancador = document.createElement("button");
  lancador.type = "button";
  lancador.className = "calculadora-widget-launcher";
  lancador.setAttribute("aria-label", "Abrir calculadora");
  lancador.textContent = "=";

  const painel = document.createElement("section");
  painel.className = "calculadora-widget-panel";
  painel.hidden = true;
  painel.innerHTML = `
    <header class="calculadora-widget-header">
      <strong>Calculadora</strong>
      <button type="button" data-widget-fechar aria-label="Fechar">×</button>
    </header>
    <div class="calculadora-widget-display" id="calculadora-widget-display">0</div>
    <div class="calculadora-widget-grid">
      <button type="button" data-acao="limpar" class="calculadora-op">C</button>
      <button type="button" data-acao="apagar" class="calculadora-op">⌫</button>
      <button type="button" data-acao="porcento" class="calculadora-op">%</button>
      <button type="button" data-operador="/" class="calculadora-op">÷</button>
      <button type="button" data-digito="7">7</button>
      <button type="button" data-digito="8">8</button>
      <button type="button" data-digito="9">9</button>
      <button type="button" data-operador="*" class="calculadora-op">×</button>
      <button type="button" data-digito="4">4</button>
      <button type="button" data-digito="5">5</button>
      <button type="button" data-digito="6">6</button>
      <button type="button" data-operador="-" class="calculadora-op">−</button>
      <button type="button" data-digito="1">1</button>
      <button type="button" data-digito="2">2</button>
      <button type="button" data-digito="3">3</button>
      <button type="button" data-operador="+" class="calculadora-op">+</button>
      <button type="button" data-digito="0" class="calculadora-zero">0</button>
      <button type="button" data-digito=",">,</button>
      <button type="button" data-acao="igual" class="calculadora-igual">=</button>
    </div>
  `;

  document.body.append(lancador, painel);

  const visor = painel.querySelector("#calculadora-widget-display");
  let aberto = false;
  let atual = "0";
  let acumulado = null;
  let operadorPendente = null;
  let reiniciarVisor = false;

  function atualizarVisor() {
    visor.textContent = atual.replace(".", ",");
  }

  function calcular(a, operador, b) {
    switch (operador) {
      case "+": return a + b;
      case "-": return a - b;
      case "*": return a * b;
      case "/": return b === 0 ? NaN : a / b;
      default: return b;
    }
  }

  function alternarPainel(mostrar) {
    aberto = mostrar ?? !aberto;
    painel.hidden = !aberto;
  }

  lancador.addEventListener("click", () => alternarPainel());
  painel.querySelector("[data-widget-fechar]").addEventListener("click", () => alternarPainel(false));

  painel.querySelector(".calculadora-widget-grid").addEventListener("click", event => {
    const botao = event.target.closest("button");
    if (!botao) return;
    const { digito, operador, acao } = botao.dataset;

    if (digito !== undefined) {
      if (digito === "," && atual.includes(".")) return;
      if (reiniciarVisor || atual === "0") {
        atual = digito === "," ? "0." : digito === "0" ? "0" : digito;
        reiniciarVisor = false;
      } else {
        atual += digito === "," ? "." : digito;
      }
      atualizarVisor();
      return;
    }

    if (operador) {
      if (acumulado !== null && operadorPendente && !reiniciarVisor) {
        atual = String(calcular(acumulado, operadorPendente, Number(atual.replace(",", "."))));
      }
      acumulado = Number(atual.replace(",", "."));
      operadorPendente = operador;
      reiniciarVisor = true;
      return;
    }

    if (acao === "limpar") {
      atual = "0";
      acumulado = null;
      operadorPendente = null;
      reiniciarVisor = false;
      atualizarVisor();
      return;
    }
    if (acao === "apagar") {
      atual = atual.length > 1 ? atual.slice(0, -1) : "0";
      // Apagar um resultado negativo (ex.: "-2") pode deixar só o sinal --
      // "-" sozinho quebraria Number(atual) na próxima operação.
      if (atual === "-") atual = "0";
      atualizarVisor();
      return;
    }
    if (acao === "porcento") {
      atual = String(Number(atual.replace(",", ".")) / 100);
      atualizarVisor();
      return;
    }
    if (acao === "igual") {
      if (operadorPendente !== null && acumulado !== null) {
        const resultado = calcular(acumulado, operadorPendente, Number(atual.replace(",", ".")));
        atual = Number.isFinite(resultado) ? String(Math.round(resultado * 100) / 100) : "Erro";
      }
      acumulado = null;
      operadorPendente = null;
      reiniciarVisor = true;
      atualizarVisor();
    }
  });
}
createCalculadoraWidget();

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
    else if (link.dataset.permission === "*") { /* disponível para qualquer usuário autenticado, ex.: Assistente (IA) */ }
    else if (!section?.superadmin && user.perfil !== "administrador" && !user.superadmin) {
      const permissions = section?.permissions || [link.dataset.permission];
      if (!permissions.some(permission => user.permissoes.includes(permission))) link.remove();
    }
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

// Fase 3 da central de atualizações (continuação da Fase 2,
// app.api.atualizacoes): faixa não-bloqueante no topo de toda tela admin
// avisando de atualizações pendentes de leitura, com destaque para
// críticas -- reaproveita os endpoints da Fase 2 (a Fase 2 já tinha a
// página /admin/atualizacoes, só faltava o aviso proativo). Correção
// crítica nunca tem botão de adiar (leitura_obrigatoria=true e
// pode_adiar=false vêm calculados assim pelo backend); nunca é modal.
async function carregarAtualizacoesPendentes() {
  const resposta = await originalFetch("/v1/admin/atualizacoes");
  if (!resposta.ok) return;
  const dados = await resposta.json();
  const agora = new Date();
  const pendentes = dados.novidades.filter((item) => {
    if (item.estado.confirmada_em) return false;
    if (item.estado.adiada_ate && new Date(item.estado.adiada_ate) > agora) return false;
    return true;
  });
  renderAtualizacoesBanner(pendentes);
}

function escapeAdminHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function renderAtualizacoesBanner(itens) {
  document.querySelector("#admin-atualizacoes-banner")?.remove();
  if (!itens.length) return;
  const banner = document.createElement("div");
  banner.id = "admin-atualizacoes-banner";
  banner.className = "admin-avisos-banner";
  banner.innerHTML = itens.map((item) => `
    <article class="admin-aviso-item ${item.classificacao === "critica" ? "is-critico" : ""}" data-atualizacao-id="${item.id}">
      <div>
        <strong>${item.classificacao === "critica" ? "Atualização crítica" : "Nova versão"} · ${escapeAdminHtml(item.versao)}</strong>
        <span>${escapeAdminHtml(item.titulo)}</span>
      </div>
      <p>${escapeAdminHtml(item.impacto_usuario)}</p>
      <div class="admin-aviso-acoes">
        <button type="button" class="${item.leitura_obrigatoria ? "primary-button" : "secondary-button"}" data-confirmar-atualizacao="${item.id}">${item.leitura_obrigatoria ? "Confirmar leitura" : "Dispensar"}</button>
        ${item.pode_adiar ? `<button type="button" class="secondary-button" data-adiar-atualizacao="${item.id}">Lembrar em 7 dias</button>` : ""}
        <a class="secondary-button" href="/admin/atualizacoes">Ver central de atualizações</a>
      </div>
    </article>
  `).join("");
  // Prepend dentro de .admin-main (nunca document.body): o sidebar é
  // position:fixed e todo o conteúdo já tem margin-left pra não ficar por
  // baixo dele -- um banner solto no body ficava com metade do texto
  // escondida atrás do sidebar (achado do usuário, captura de tela).
  (document.querySelector(".admin-main") || document.body).prepend(banner);
  banner.querySelectorAll("[data-confirmar-atualizacao]").forEach((botao) => {
    botao.addEventListener("click", async () => {
      botao.disabled = true;
      const resposta = await fetch(`/v1/admin/atualizacoes/${botao.dataset.confirmarAtualizacao}/confirmar-leitura`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmar: true }),
      });
      if (resposta.ok) banner.querySelector(`[data-atualizacao-id="${botao.dataset.confirmarAtualizacao}"]`)?.remove();
      else botao.disabled = false;
      if (!banner.querySelector(".admin-aviso-item")) banner.remove();
    });
  });
  banner.querySelectorAll("[data-adiar-atualizacao]").forEach((botao) => {
    botao.addEventListener("click", async () => {
      botao.disabled = true;
      const resposta = await fetch(`/v1/admin/atualizacoes/${botao.dataset.adiarAtualizacao}/adiar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dias: 7 }),
      });
      if (resposta.ok) banner.querySelector(`[data-atualizacao-id="${botao.dataset.adiarAtualizacao}"]`)?.remove();
      else botao.disabled = false;
      if (!banner.querySelector(".admin-aviso-item")) banner.remove();
    });
  });
}

carregarAtualizacoesPendentes();

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

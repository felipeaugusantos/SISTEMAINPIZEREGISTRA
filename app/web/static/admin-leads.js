const filters = document.querySelector("#lead-filters");
const searchInput = document.querySelector("#lead-search");
const statusFilter = document.querySelector("#lead-status-filter");
const ownerFilter = document.querySelector("#lead-owner-filter");
const originFilter = document.querySelector("#lead-origin-filter");
const dateStart = document.querySelector("#lead-date-start");
const dateEnd = document.querySelector("#lead-date-end");
const marketingFilter = document.querySelector("#lead-marketing-filter");
const archivedFilter = document.querySelector("#lead-archived-filter");
const leadsList = document.querySelector("#leads-list");
const message = document.querySelector("#admin-message");
const pageSize = document.querySelector("#lead-page-size");
const dialog = document.querySelector("#lead-dialog");
const dialogContent = document.querySelector("#lead-dialog-content");
const archiveDialog = document.querySelector("#archive-dialog");
const viewButtons = document.querySelectorAll(".lead-view-button");
const viewDescription = document.querySelector("#lead-view-description");
const crmPipeline = document.querySelector("#crm-pipeline");
const crmPriorities = document.querySelector("#crm-priorities");

const state = {
  offset: 0, total: 0, owners: [], archiveId: null, loading: false,
  canManage: false, canArchive: false, canExport: false, openLeadId: null,
  viewMode: "contacts", items: [], priority: "",
};
const statusLabels = {
  novo: "Novo", em_contato: "Em contato", qualificado: "Qualificado",
  proposta_enviada: "Proposta enviada", sem_retorno: "Sem retorno",
  convertido: "Convertido", descartado: "Descartado",
};
const riskLabels = { critico: "Crítico", muito_alto: "Muito alto", alto: "Alto", moderado: "Moderado", baixo: "Baixo" };

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function formatDate(value, withTime = true) {
  if (!value) return "Não informado";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short", ...(withTime ? { timeStyle: "short" } : {}), timeZone: "America/Sao_Paulo",
  }).format(new Date(value));
}

function phoneDigits(value) { return String(value || "").replace(/\D/g, ""); }
function ownerOptions(selected) {
  return `<option value="">Não atribuído</option>${state.owners.map(owner =>
    `<option value="${owner.id}" ${String(selected || "") === String(owner.id) ? "selected" : ""}>${escapeHtml(owner.nome)}</option>`
  ).join("")}`;
}
function statusOptions(selected) {
  return Object.entries(statusLabels).map(([value, label]) =>
    `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`
  ).join("");
}

function currentParams(includePagination = true) {
  const params = new URLSearchParams();
  if (searchInput.value.trim()) params.set("busca", searchInput.value.trim());
  if (statusFilter.value) params.set("status", statusFilter.value);
  if (ownerFilter.value) params.set("responsavel_id", ownerFilter.value);
  if (originFilter.value) params.set("origem", originFilter.value);
  if (dateStart.value) params.set("data_inicio", `${dateStart.value}T00:00:00-03:00`);
  if (dateEnd.value) params.set("data_fim", `${dateEnd.value}T23:59:59-03:00`);
  if (marketingFilter.value) params.set("marketing", marketingFilter.value);
  if (archivedFilter.checked) params.set("arquivados", "true");
  if (state.priority) params.set("prioridade", state.priority);
  if (includePagination) {
    params.set("limite", pageSize.value);
    params.set("deslocamento", state.offset);
  }
  return params;
}

function showMessage(text, kind = "") {
  message.className = `status-message ${kind}`.trim();
  message.textContent = text;
}

function originLabel(value) {
  return ({ relatorio: "Relatório", processo: "Página do processo", resultados: "Resultados", geral: "Contato geral" })[value] || value;
}

function fullReportStatus(item, compact = false) {
  if (!item) return "";
  if (item.relatorio_completo_gerado) {
    const detail = item.relatorio_completo_gerado_em
      ? `${compact ? "" : "Primeira geração: "}${formatDate(item.relatorio_completo_gerado_em)}`
      : "Relatório completo já gerado";
    return `<span class="full-report-status ready" title="${escapeHtml(detail)}">Completo gerado${compact ? "" : ` · ${escapeHtml(detail)}`}</span>`;
  }
  return `<span class="full-report-status pending">Completo não gerado</span>`;
}

function aggregateReportStatus(lead) {
  const generated = Number(lead.relatorios_completos_gerados || 0);
  const total = Number(lead.total_pesquisas || 0);
  const ready = total > 0 && generated === total;
  return `<span class="full-report-status ${ready ? "ready" : "pending"}">${generated}/${total} completos</span>`;
}

function quickResearchHistory(lead) {
  const researches = lead.pesquisas || [];
  const cards = researches.length
    ? researches.map((item, index) => `<li>
        <div><strong>${escapeHtml(item.marca)}</strong>${index === 0 ? `<span class="latest-badge">Última</span>` : ""}<small>${formatDate(item.criado_em)}</small></div>
        <div class="quick-research-meta">${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}</span>` : `<span>Risco não calculado</span>`}${fullReportStatus(item, true)}</div>
        <a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir análise</a>
      </li>`).join("")
    : `<li class="quick-research-empty">Nenhuma pesquisa vinculada.</li>`;
  return `<tr class="lead-researches-row" data-lead-id="${lead.id}" hidden>
    <td colspan="6" data-label="Pesquisas">
      <section class="quick-research-history" aria-label="Pesquisas de ${escapeHtml(lead.nome)}">
        <header><strong>Pesquisas realizadas</strong><span>${lead.total_pesquisas} no total</span></header>
        <ol>${cards}</ol>
      </section>
    </td>
  </tr>`;
}

function leadRow(lead) {
  const pesquisa = lead.ultima_pesquisa;
  const digits = phoneDigits(lead.telefone);
  const contactLinks = lead.email.includes("***") ? "" : `
    <span class="lead-quick-actions">
      <a href="mailto:${escapeHtml(lead.email)}" aria-label="Enviar e-mail">E-mail</a>
      ${digits ? `<a href="https://wa.me/${digits}" target="_blank" rel="noopener" aria-label="Abrir WhatsApp">WhatsApp</a>` : ""}
    </span>`;
  const highestRisk = lead.risco_mais_alto;
  return `
    <tr data-lead-id="${lead.id}" class="${lead.arquivado_em ? "archived" : ""}">
      <td data-label="Contato"><strong>${escapeHtml(lead.nome)}</strong><small>${escapeHtml(lead.empresa || "Empresa não informada")}</small><span>${escapeHtml(lead.email)}</span><span>${escapeHtml(lead.telefone)}</span>${contactLinks}</td>
      <td data-label="Histórico de pesquisas"><strong>Última: ${escapeHtml(pesquisa?.marca || lead.marca || "Interesse geral")}</strong><small>${lead.total_pesquisas} pesquisa${lead.total_pesquisas === 1 ? " realizada" : "s realizadas"}</small>${highestRisk ? `<span class="risk-pill risk-${escapeHtml(highestRisk)}">Maior risco: ${escapeHtml(riskLabels[highestRisk] || highestRisk)}${lead.risco_mais_alto_pontuacao !== null ? ` · ${lead.risco_mais_alto_pontuacao} pontos` : ""}</span>` : `<span class="risk-pill">Risco não calculado</span>`}${aggregateReportStatus(lead)}<button class="toggle-researches inline-history-button" type="button" aria-expanded="false">Ver pesquisas (${lead.total_pesquisas})</button></td>
      <td data-label="Atendimento"><strong>${escapeHtml(lead.responsavel_nome || "Não atribuído")}</strong><small>${escapeHtml(originLabel(lead.origem))}</small><small>${lead.proxima_acao_em ? `Próxima ação: ${formatDate(lead.proxima_acao_em)}` : "Sem próxima ação"}</small></td>
      <td data-label="Última pesquisa"><time datetime="${escapeHtml(lead.ultima_pesquisa_em || lead.criado_em)}">${formatDate(lead.ultima_pesquisa_em || lead.criado_em)}</time></td>
      <td data-label="Status"><select class="lead-status status-${escapeHtml(lead.status)}" data-previous="${escapeHtml(lead.status)}" aria-label="Status de ${escapeHtml(lead.nome)}" ${lead.arquivado_em || !state.canManage ? "disabled" : ""}>${statusOptions(lead.status)}</select></td>
      <td data-label="Ações"><div class="lead-row-actions"><button class="view-lead secondary-button" type="button">Abrir contato</button>${state.canArchive ? (lead.arquivado_em ? `<button class="restore-lead secondary-button" type="button">Restaurar</button>` : `<button class="archive-lead danger-link" type="button">Arquivar</button>`) : ""}</div></td>
    </tr>${quickResearchHistory(lead)}`;
}

function researchRow(lead, item) {
  const digits = phoneDigits(lead.telefone);
  return `<tr data-lead-id="${lead.id}" class="research-view-row ${lead.arquivado_em ? "archived" : ""}">
    <td data-label="Contato"><strong>${escapeHtml(lead.nome)}</strong><small>${escapeHtml(lead.empresa || "Empresa não informada")}</small><span>${escapeHtml(lead.email)}</span>${digits ? `<a href="https://wa.me/${digits}" target="_blank" rel="noopener">WhatsApp</a>` : ""}</td>
    <td data-label="Pesquisa"><strong>${escapeHtml(item.marca)}</strong><small>${escapeHtml(item.atividade || "Atividade não informada")}</small>${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}${item.risco_pontuacao !== null ? ` · ${item.risco_pontuacao} pontos` : ""}</span>` : `<span class="risk-pill">Risco não calculado</span>`}${fullReportStatus(item, true)}</td>
    <td data-label="Atendimento"><strong>${escapeHtml(lead.responsavel_nome || "Não atribuído")}</strong><small>${escapeHtml(originLabel(lead.origem))}</small></td>
    <td data-label="Data da pesquisa"><time datetime="${escapeHtml(item.criado_em)}">${formatDate(item.criado_em)}</time></td>
    <td data-label="Status"><span class="lead-status-readonly status-${escapeHtml(lead.status)}">${escapeHtml(statusLabels[lead.status] || lead.status)}</span></td>
    <td data-label="Ações"><div class="lead-row-actions"><a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir análise</a><button class="view-lead secondary-button" type="button">Abrir contato</button></div></td>
  </tr>`;
}

function renderRows() {
  if (state.viewMode === "researches") {
    leadsList.innerHTML = state.items.flatMap(lead =>
      (lead.pesquisas || []).map(item => researchRow(lead, item))
    ).join("");
    return;
  }
  leadsList.innerHTML = state.items.map(leadRow).join("");
}

async function loadOwners() {
  const response = await fetch("/v1/admin/leads-responsaveis");
  if (!response.ok) return;
  state.owners = (await response.json()).itens || [];
  ownerFilter.insertAdjacentHTML("beforeend", state.owners.map(owner => `<option value="${owner.id}">${escapeHtml(owner.nome)}</option>`).join(""));
}

function updatePagination(data) {
  const first = data.total ? data.deslocamento + 1 : 0;
  const last = Math.min(data.deslocamento + data.itens.length, data.total);
  document.querySelector("#lead-page-summary").textContent = `${first}–${last} de ${data.total}`;
  document.querySelector("#lead-prev").disabled = data.deslocamento === 0;
  document.querySelector("#lead-next").disabled = !data.tem_mais;
}

function renderPipeline(counts = {}) {
  document.querySelectorAll(".crm-stage").forEach(button => {
    button.querySelector("strong").textContent = counts[button.dataset.status] || 0;
    button.classList.toggle("active", statusFilter.value === button.dataset.status);
  });
}

function renderPrioritySelection() {
  document.querySelectorAll(".crm-priority").forEach(button => {
    button.classList.toggle("active", state.priority === button.dataset.priority);
  });
}

async function loadCrmSummary() {
  try {
    const response = await fetch("/v1/admin/leads-crm");
    if (!response.ok) return;
    const data = await response.json();
    document.querySelector("#crm-overdue").textContent = data.atrasadas || 0;
    document.querySelector("#crm-unassigned").textContent = data.sem_responsavel || 0;
    document.querySelector("#crm-no-action").textContent = data.sem_proxima_acao || 0;
  } catch (_) {
    // O carregamento principal continua disponivel se o resumo falhar.
  }
}

async function loadLeads() {
  if (state.loading) return;
  state.loading = true;
  showMessage("Carregando contatos…");
  try {
    const response = await fetch(`/v1/admin/leads?${currentParams()}`);
    if (!response.ok) throw new Error("Não foi possível carregar os contatos.");
    const data = await response.json();
    state.total = data.total;
    state.items = data.itens;
    state.canManage = Boolean(data.acoes?.gerenciar);
    state.canArchive = Boolean(data.acoes?.arquivar);
    state.canExport = Boolean(data.acoes?.exportar);
    document.querySelector("#export-leads").hidden = !state.canExport;
    document.querySelector("#metric-global").textContent = data.total_global;
    document.querySelector("#metric-total").textContent = data.total;
    document.querySelector("#metric-searches").textContent = data.pesquisas_total;
    document.querySelector("#metric-new").textContent = data.por_status.novo || 0;
    document.querySelector("#metric-contact").textContent = data.por_status.em_contato || 0;
    document.querySelector("#metric-converted").textContent = data.por_status.convertido || 0;
    renderPipeline(data.por_status);
    renderPrioritySelection();
    renderRows();
    updatePagination(data);
    showMessage(data.total ? "" : "Nenhum contato encontrado.");
  } catch (error) {
    showMessage(error.message, "error");
  } finally { state.loading = false; }
}

function researchCard(item) {
  const reportAction = !item.relatorio_disponivel
    ? `<span class="full-report-hint">Abra a análise para preparar o relatório completo.</span>`
    : state.canManage
      ? `<button class="secondary-button generate-full-report" type="button" data-research-id="${escapeHtml(item.id)}">${item.relatorio_completo_gerado ? "Baixar completo novamente" : "Gerar relatório completo"}</button>`
      : `<span class="full-report-hint">Geração disponível para operadores autorizados.</span>`;
  return `<article class="lead-research-card">
    <div><strong>${escapeHtml(item.marca)}</strong><small>${formatDate(item.criado_em)}</small></div>
    <p>${escapeHtml(item.atividade || "Atividade não informada")}</p>
    <div class="lead-research-meta">${item.classe_nice ? `<span>NCL ${escapeHtml(item.classe_nice)}</span>` : ""}${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}${item.risco_pontuacao !== null ? ` · ${item.risco_pontuacao} pontos` : ""}</span>` : `<span>Análise ainda não calculada</span>`}${fullReportStatus(item)}</div>
    <div class="lead-research-actions"><a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir Central de Análise</a>${reportAction}</div>
  </article>`;
}

async function openLead(id) {
  state.openLeadId = id;
  dialogContent.innerHTML = "<p>Carregando histórico…</p>";
  if (!dialog.open) dialog.showModal();
  const response = await fetch(`/v1/admin/leads/${id}`);
  if (!response.ok) { dialogContent.innerHTML = "<p class=\"status-message error\">Não foi possível abrir o contato.</p>"; return; }
  const lead = await response.json();
  document.querySelector("#lead-dialog-title").textContent = lead.nome;
  dialogContent.innerHTML = `
    <section class="lead-contact-summary"><div><span>E-mail</span><a href="mailto:${escapeHtml(lead.email)}">${escapeHtml(lead.email)}</a></div><div><span>Telefone</span><a href="tel:${escapeHtml(lead.telefone)}">${escapeHtml(lead.telefone)}</a></div><div><span>Empresa</span><strong>${escapeHtml(lead.empresa || "Não informada")}</strong></div><div><span>Marketing</span><strong>${lead.aceite_marketing ? "Autorizado" : "Não autorizado"}</strong></div></section>
    ${state.canManage ? `<form id="lead-crm-form" data-lead-id="${lead.id}" class="lead-crm-form">
      <label><span>Status</span><select name="status">${statusOptions(lead.status)}</select></label>
      <label><span>Responsável</span><select name="responsavel_id">${ownerOptions(lead.responsavel_id)}</select></label>
      <label><span>Próxima ação</span><input name="proxima_acao_em" type="datetime-local" value="${lead.proxima_acao_em ? new Date(lead.proxima_acao_em).toISOString().slice(0, 16) : ""}" /></label>
      <label><span>Tags, separadas por vírgula</span><input name="tags" maxlength="400" value="${escapeHtml((lead.tags || []).join(", "))}" /></label>
      <label class="lead-notes"><span>Anotações internas</span><textarea name="notas" maxlength="4000" rows="5">${escapeHtml(lead.notas || "")}</textarea></label>
      <label class="filter-check"><input name="registrar_contato" type="checkbox" /><span>Registrar contato realizado agora</span></label>
      <div><button class="primary-button" type="submit">Salvar atendimento</button><span id="lead-save-message" role="status"></span></div>
    </form>` : `<section class="lead-readonly-note">Você possui acesso somente para consulta.</section>`}
    <section class="lead-history"><header><div><p class="eyebrow">Histórico</p><h3>${lead.pesquisas.length} pesquisa${lead.pesquisas.length === 1 ? "" : "s"}</h3></div></header>${lead.pesquisas.length ? lead.pesquisas.map(researchCard).join("") : "<p>Nenhuma pesquisa vinculada.</p>"}</section>`;
}

dialogContent.addEventListener("click", async event => {
  const button = event.target.closest(".generate-full-report");
  if (!button) return;
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Gerando…";
  try {
    const response = await fetch(
      `/v1/admin/pesquisas/${encodeURIComponent(button.dataset.researchId)}/relatorio-completo.pdf`,
      { method: "POST" },
    );
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Não foi possível gerar o relatório completo.");
    }
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1]
      || `relatorio-completo-${button.dataset.researchId}.pdf`;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    showMessage("Relatório completo gerado e registrado no histórico.", "success");
    await loadLeads();
    await openLead(state.openLeadId);
  } catch (error) {
    button.disabled = false;
    button.textContent = originalLabel;
    showMessage(error.message, "error");
  }
});

filters.addEventListener("submit", event => { event.preventDefault(); state.offset = 0; loadLeads(); });
document.querySelector("#clear-lead-filters").addEventListener("click", () => { filters.reset(); state.priority = ""; state.offset = 0; loadLeads(); });
pageSize.addEventListener("change", () => { state.offset = 0; loadLeads(); });
document.querySelector("#lead-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - Number(pageSize.value)); loadLeads(); });
document.querySelector("#lead-next").addEventListener("click", () => { state.offset += Number(pageSize.value); loadLeads(); });
document.querySelector("#export-leads").addEventListener("click", () => { location.href = `/v1/admin/leads.csv?${currentParams(false)}`; });
document.querySelector(".dialog-close").addEventListener("click", () => dialog.close());

crmPipeline.addEventListener("click", event => {
  const button = event.target.closest(".crm-stage");
  if (!button) return;
  statusFilter.value = statusFilter.value === button.dataset.status ? "" : button.dataset.status;
  state.offset = 0;
  loadLeads();
});

crmPriorities.addEventListener("click", event => {
  const button = event.target.closest(".crm-priority");
  if (!button) return;
  state.priority = state.priority === button.dataset.priority ? "" : button.dataset.priority;
  state.offset = 0;
  loadLeads();
});

viewButtons.forEach(button => button.addEventListener("click", () => {
  state.viewMode = button.dataset.view;
  viewButtons.forEach(item => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  const researchMode = state.viewMode === "researches";
  document.querySelector("#research-column-title").textContent = researchMode ? "Pesquisa" : "Histórico de pesquisas";
  document.querySelector("#activity-column-title").textContent = researchMode ? "Data da pesquisa" : "Última pesquisa";
  viewDescription.textContent = researchMode
    ? "Cada linha representa uma pesquisa dos contatos exibidos nesta página."
    : "Cada linha representa um contato e resume todo o seu histórico.";
  renderRows();
}));

leadsList.addEventListener("change", async event => {
  if (!event.target.matches(".lead-status")) return;
  const select = event.target;
  const row = select.closest("tr");
  const previous = select.dataset.previous;
  select.disabled = true;
  const response = await fetch(`/v1/admin/leads/${row.dataset.leadId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: select.value }) });
  select.disabled = false;
  if (!response.ok) { select.value = previous; showMessage("Não foi possível atualizar o status.", "error"); return; }
  select.dataset.previous = select.value;
  select.className = `lead-status status-${select.value}`;
  showMessage("Status atualizado.", "success");
  await Promise.all([loadLeads(), loadCrmSummary()]);
});

leadsList.addEventListener("click", event => {
  const row = event.target.closest("tr");
  if (!row) return;
  const historyButton = event.target.closest(".toggle-researches");
  if (historyButton) {
    const historyRow = row.nextElementSibling;
    const expanded = historyButton.getAttribute("aria-expanded") === "true";
    historyButton.setAttribute("aria-expanded", String(!expanded));
    historyButton.textContent = expanded
      ? `Ver pesquisas (${historyRow.querySelectorAll("li:not(.quick-research-empty)").length})`
      : "Ocultar pesquisas";
    historyRow.hidden = expanded;
    return;
  }
  if (event.target.closest(".view-lead")) openLead(row.dataset.leadId);
  if (event.target.closest(".archive-lead")) { state.archiveId = row.dataset.leadId; archiveDialog.showModal(); }
  if (event.target.closest(".restore-lead")) fetch(`/v1/admin/leads/${row.dataset.leadId}/restaurar`, { method: "POST" }).then(response => { if (response.ok) loadLeads(); else showMessage("Não foi possível restaurar o contato.", "error"); });
});

document.querySelector("#confirm-archive").addEventListener("click", async event => {
  event.preventDefault();
  const response = await fetch(`/v1/admin/leads/${state.archiveId}`, { method: "DELETE" });
  if (response.ok) { archiveDialog.close(); showMessage("Contato arquivado; pesquisas preservadas.", "success"); loadLeads(); }
  else showMessage("Não foi possível arquivar o contato.", "error");
});

dialogContent.addEventListener("submit", async event => {
  if (!event.target.matches("#lead-crm-form")) return;
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  const payload = {
    status: data.get("status"),
    responsavel_id: data.get("responsavel_id") ? Number(data.get("responsavel_id")) : null,
    proxima_acao_em: data.get("proxima_acao_em") ? new Date(data.get("proxima_acao_em")).toISOString() : null,
    notas: data.get("notas") || null,
    tags: String(data.get("tags") || "").split(",").map(item => item.trim()).filter(Boolean),
    registrar_contato: data.get("registrar_contato") === "on",
  };
  const saveMessage = document.querySelector("#lead-save-message");
  saveMessage.textContent = "Salvando…";
  const response = await fetch(`/v1/admin/leads/${form.dataset.leadId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  saveMessage.textContent = response.ok ? "Atendimento salvo." : "Não foi possível salvar.";
  if (response.ok) await Promise.all([loadLeads(), loadCrmSummary()]);
});

loadOwners().then(() => Promise.all([loadLeads(), loadCrmSummary()]));

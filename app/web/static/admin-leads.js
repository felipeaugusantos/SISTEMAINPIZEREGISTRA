function initLeadsPage() {
const leadDialog = window.createLeadDialog(document.querySelector("#lead-workspace"), {
  message: document.querySelector("#admin-message"),
  onChange: loadLeads,
  onSummary: loadCrmSummary,
});
const { riskLabels, escapeHtml, formatDate, phoneDigits, statusOptions, showMessage, responsePayload, originLabel, fullReportStatus, aggregateReportStatus, duplicateStatus, deletionAction, abrirRegistroAtendimento, openLead, faseMini, openResearchDelete, criarProposta } = leadDialog;
const filters = document.querySelector("#lead-filters");
const searchInput = document.querySelector("#lead-search");
const statusFilter = document.querySelector("#lead-status-filter");
const ownerFilter = document.querySelector("#lead-owner-filter");
const originFilter = document.querySelector("#lead-origin-filter");
const dateStart = document.querySelector("#lead-date-start");
const dateEnd = document.querySelector("#lead-date-end");
const marketingFilter = document.querySelector("#lead-marketing-filter");
const leadsList = document.querySelector("#leads-list");
const pageSize = document.querySelector("#lead-page-size");
const archiveDialog = document.querySelector("#archive-dialog");
const viewButtons = document.querySelectorAll(".lead-view-button");
const viewDescription = document.querySelector("#lead-view-description");
const crmPipeline = document.querySelector("#crm-pipeline");
const crmPriorities = document.querySelector("#crm-priorities");

const state = {
  offset: 0, total: 0, owners: [], archiveId: null, loading: false,
  canManage: false, canArchive: false, canExport: false, canDeleteResearch: false, canPii: false,
  viewMode: "researches", items: [], priority: "", semResponsavel: 0,
};

// Achado do usuário (16/09/2026, item 3): o endpoint de distribuição em
// lote (POST /v1/admin/leads/distribuir) já existia e era usado na tela
// de CRM/Kanban, mas não tinha nenhuma ação equivalente na tela de
// Leads -- quem via o card "Sem responsável" acumular tinha que trocar
// de tela pra resolver. Mesmo botão/rótulo já usado em admin-crm.js.
function atualizarBotaoDistribuir() {
  const botao = document.querySelector("#distribute-leads");
  if (!botao) return;
  botao.hidden = !state.canManage || state.semResponsavel === 0;
  botao.textContent = `Distribuir sem responsável (${state.semResponsavel})`;
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
  if (state.viewMode === "archived") params.set("arquivados", "true");
  if (state.priority) params.set("prioridade", state.priority);
  if (includePagination) {
    params.set("limite", pageSize.value);
    params.set("deslocamento", state.offset);
  }
  return params;
}

function quickResearchHistory(lead) {
  const researches = lead.pesquisas || [];
  const cards = researches.length
    ? researches.map((item, index) => `<li>
        <div><strong>${escapeHtml(item.marca)}</strong>${index === 0 ? `<span class="latest-badge">Última</span>` : ""}${duplicateStatus(item)}<small>${formatDate(item.criado_em)}</small></div>
        <div class="quick-research-meta">${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}</span>` : `<span>Risco não calculado</span>`}${fullReportStatus(item, true)}</div>
        <a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir análise</a>${deletionAction(item)}
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
  const pendingBadge = lead.mensagens_portal_pendentes
    ? `<span class="portal-pending-badge" title="Mensagem pendente do portal" aria-label="Mensagem pendente do portal">!</span>`
    : "";
  const contactLinks = lead.email.includes("***") ? pendingBadge : `
    <span class="lead-quick-actions">
      <a href="mailto:${escapeHtml(lead.email)}" aria-label="Enviar e-mail">E-mail</a>
      ${digits ? `<a href="https://wa.me/${digits}" target="_blank" rel="noopener" aria-label="Abrir WhatsApp">WhatsApp</a>` : ""}
    ${pendingBadge}</span>`;
  const highestRisk = lead.risco_mais_alto;
  return `
    <tr data-lead-id="${lead.id}" class="${lead.arquivado_em ? "archived" : ""}">
      <td data-label="Contato"><strong>${escapeHtml(lead.nome)}</strong><small>${escapeHtml(lead.empresa || "Empresa não informada")}</small><span>${escapeHtml(lead.email)}</span><span>${escapeHtml(lead.telefone)}</span>${contactLinks}</td>
      <td data-label="Histórico de pesquisas"><strong>Última: ${escapeHtml(pesquisa?.marca || lead.marca || "Interesse geral")}</strong><small>${lead.total_pesquisas} pesquisa${lead.total_pesquisas === 1 ? " realizada" : "s realizadas"}</small>${highestRisk ? `<span class="risk-pill risk-${escapeHtml(highestRisk)}">Maior risco: ${escapeHtml(riskLabels[highestRisk] || highestRisk)}${lead.risco_mais_alto_pontuacao !== null ? ` · ${lead.risco_mais_alto_pontuacao} pontos` : ""}</span>` : `<span class="risk-pill">Risco não calculado</span>`}${aggregateReportStatus(lead)}<button class="toggle-researches inline-history-button" type="button" aria-expanded="false">Ver pesquisas (${lead.total_pesquisas})</button></td>
      <td data-label="Atendimento"><strong>${escapeHtml(lead.responsavel_nome || "Não atribuído")}</strong><small>${escapeHtml(originLabel(lead.origem))}</small><small>${lead.proxima_acao_em ? `Próxima ação: ${formatDate(lead.proxima_acao_em)}` : "Sem próxima ação"}</small>${faseMini(lead)}</td>
      <td data-label="Última pesquisa"><time datetime="${escapeHtml(lead.ultima_pesquisa_em || lead.criado_em)}">${formatDate(lead.ultima_pesquisa_em || lead.criado_em)}</time></td>
      <td data-label="Status"><select class="lead-status status-${escapeHtml(lead.status)}" data-previous="${escapeHtml(lead.status)}" aria-label="Status de ${escapeHtml(lead.nome)}" ${lead.arquivado_em || !state.canManage ? "disabled" : ""}>${statusOptions(lead.status)}</select></td>
      <td data-label="Ações"><div class="lead-row-actions"><button class="view-lead secondary-button" type="button">Abrir contato</button>${state.canArchive ? (lead.arquivado_em ? `<button class="restore-lead secondary-button" type="button">Restaurar</button>` : `<button class="archive-lead danger-link" type="button">Arquivar</button>`) : ""}</div></td>
    </tr>${quickResearchHistory(lead)}`;
}

function researchRow(lead, item) {
  const digits = phoneDigits(lead.telefone);
  // Achado do usuário (08/09/2026): leads sem nenhuma pesquisa de marca
  // (ex.: convertidos do Radar de Prospecção, que não passam pelo
  // formulário de pesquisa) simplesmente desapareciam desta visão -- cada
  // linha aqui representa uma pesquisa, então um lead com pesquisas=[]
  // nunca gerava linha nenhuma. Isso também explicava a paginação parecer
  // quebrada: com poucos itens por página, os leads mais recentes (todos
  // de prospecção) preenchiam a página inteira sem produzir nenhuma linha.
  // Ações que dependem de uma pesquisa concreta (análise, proposta,
  // exclusão, registrar atendimento) ficam de fora dessa linha-placeholder.
  const contato = `<td data-label="Contato"><strong>${escapeHtml(lead.nome)}</strong><small>${escapeHtml(lead.empresa || "Empresa não informada")}</small><span>${escapeHtml(lead.email)}</span>${digits ? `<a href="https://wa.me/${digits}" target="_blank" rel="noopener">WhatsApp</a>` : ""}</td>`;
  const atendimento = `<td data-label="Atendimento"><strong>${escapeHtml(lead.responsavel_nome || "Não atribuído")}</strong><small>${escapeHtml(originLabel(lead.origem))}</small>${faseMini(lead)}</td>`;
  const statusCol = `<td data-label="Status"><select class="lead-status status-${escapeHtml(lead.status)}" data-previous="${escapeHtml(lead.status)}" aria-label="Status de ${escapeHtml(lead.nome)}" ${lead.arquivado_em || !state.canManage ? "disabled" : ""}>${statusOptions(lead.status)}</select></td>`;
  if (!item) {
    return `<tr data-lead-id="${lead.id}" class="research-view-row ${lead.arquivado_em ? "archived" : ""}">
      ${contato}
      <td data-label="Pesquisa"><span class="risk-pill">Sem pesquisa de marca ainda</span></td>
      ${atendimento}
      <td data-label="Data da pesquisa">—</td>
      ${statusCol}
      <td data-label="Ações"><div class="lead-row-actions"><button class="view-lead secondary-button" type="button">Abrir contato</button></div></td>
    </tr>`;
  }
  return `<tr data-lead-id="${lead.id}" data-research-id="${item.id}" class="research-view-row ${lead.arquivado_em ? "archived" : ""}">
    ${contato}
    <td data-label="Pesquisa"><strong>${escapeHtml(item.marca)}</strong>${duplicateStatus(item)}<small>${escapeHtml(item.atividade || "Atividade não informada")}</small>${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}${item.risco_pontuacao !== null ? ` · ${item.risco_pontuacao} pontos` : ""}</span>` : `<span class="risk-pill">Risco não calculado</span>`}${fullReportStatus(item, true)}</td>
    ${atendimento}
    <td data-label="Data da pesquisa"><time datetime="${escapeHtml(item.criado_em)}">${formatDate(item.criado_em)}</time></td>
    ${statusCol}
    <td data-label="Ações"><div class="lead-row-actions"><a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir análise</a><button class="view-lead secondary-button" type="button">Abrir contato</button>${state.canManage ? `<button class="generate-research-proposal secondary-button" type="button" data-research-id="${escapeHtml(item.id)}">Gerar proposta</button>` : ""}${deletionAction(item)}</div></td>
  </tr>`;
}

function renderRows() {
  if (state.viewMode === "researches") {
    leadsList.innerHTML = state.items.flatMap(lead =>
      (lead.pesquisas && lead.pesquisas.length) ? lead.pesquisas.map(item => researchRow(lead, item)) : [researchRow(lead, null)]
    ).join("");
    leadsList.querySelectorAll(".research-view-row").forEach(row => {
      if (!row.dataset.researchId) return;
      const actions = row.querySelector(".lead-row-actions");
      if (actions && !actions.querySelector(".register-attendance")) {
        actions.insertAdjacentHTML("beforeend", `<button class="register-attendance secondary-button" type="button" data-research-id="${escapeHtml(row.dataset.researchId)}">Registrar atendimento</button>`);
      }
    });
    return;
  }
  leadsList.innerHTML = state.items.map(leadRow).join("");
}

async function loadOwners() {
  const response = await fetch("/v1/admin/leads-responsaveis");
  if (!response.ok) return;
  state.owners = (await response.json()).itens || [];
  leadDialog.setContext(state);
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

// Achado do usuário (17/09/2026): "ninguém usa/conhece" os filtros de
// prioridade (Ações atrasadas/Sem responsável/Sem próxima ação) -- existem
// desde antes, mas só aparecem como botões discretos no meio da tela, sem
// nada chamando atenção pra eles. Este banner aparece no topo, impossível
// de não ver, só quando há pendência de verdade (nunca aparece "vazio").
function renderAvisoAtencaoLeads(data) {
  const banner = document.querySelector("#lead-attention-banner");
  const texto = document.querySelector("#lead-attention-text");
  if (!banner || !texto) return;
  const atrasadas = data.atrasadas || 0;
  const semProximaAcao = data.sem_proxima_acao || 0;
  if (!atrasadas && !semProximaAcao) { banner.hidden = true; return; }
  const partes = [];
  if (atrasadas) partes.push(`${atrasadas} lead${atrasadas === 1 ? "" : "s"} com ação atrasada`);
  if (semProximaAcao) partes.push(`${semProximaAcao} sem próxima ação definida`);
  texto.textContent = `⚠ Você tem ${partes.join(" e ")} — precisam de contato.`;
  banner.dataset.priority = atrasadas ? "atrasadas" : "sem_proxima_acao";
  banner.hidden = false;
}

async function loadCrmSummary() {
  try {
    const response = await fetch("/v1/admin/leads-crm");
    if (!response.ok) return;
    const data = await response.json();
    document.querySelector("#crm-overdue").textContent = data.atrasadas || 0;
    document.querySelector("#crm-unassigned").textContent = data.sem_responsavel || 0;
    document.querySelector("#crm-no-action").textContent = data.sem_proxima_acao || 0;
    document.querySelector("#crm-distribuicao-desligada").hidden =
      data.distribuicao_automatica_ativa || !data.sem_responsavel;
    state.semResponsavel = data.sem_responsavel || 0;
    atualizarBotaoDistribuir();
    renderAvisoAtencaoLeads(data);
  } catch (_) {
    // O carregamento principal continua disponivel se o resumo falhar.
  }
}

async function loadDeletionRequests() {
  const section = document.querySelector("#deletion-requests");
  section.hidden = !state.canDeleteResearch;
  if (!state.canDeleteResearch) return;
  try {
    const data = await responsePayload(await fetch("/v1/admin/exclusoes-pesquisas"));
    document.querySelector("#deletion-request-count").textContent = `${data.total} pendente${data.total === 1 ? "" : "s"}`;
    const list = document.querySelector("#deletion-request-list");
    list.innerHTML = data.itens.length ? data.itens.map(item => `
      <article class="learning-review-card deletion-request-card" data-request-id="${item.id}">
        <header><div><p class="eyebrow">${escapeHtml(item.solicitado_por)}</p><h3>${escapeHtml(item.marca)}</h3></div><small>${formatDate(item.criado_em)}</small></header>
        <p>${escapeHtml(item.motivo)}</p>
        <div class="lead-research-actions">
          <button class="secondary-button decide-deletion" data-decision="reject" type="button">Rejeitar</button>
          <button class="danger-button decide-deletion" data-decision="approve" type="button">Aprovar e excluir</button>
        </div>
      </article>`).join("") : '<p class="deletion-requests-empty">Nenhuma solicitação pendente.</p>';
  } catch (error) {
    showMessage(error.message, "error");
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
    state.canDeleteResearch = Boolean(data.acoes?.excluir_pesquisa);
    state.canPii = Boolean(data.acoes?.ver_pii);
    leadDialog.setContext(state);
    atualizarBotaoDistribuir();
    if (state.viewMode === "archived") {
      document.querySelector('[data-view="archived"]').textContent = `Clientes arquivados (${data.total})`;
    }
    document.querySelector("#export-leads").hidden = !state.canExport;
    document.querySelector("#copy-emails").hidden = !state.canExport;
    // Controles exclusivos da página de Leads, nunca inicializados pelo CRM.
    const importLeadsButton = document.querySelector("#import-leads");
    if (importLeadsButton) importLeadsButton.hidden = !state.canManage;
    document.querySelector("#metric-global").textContent = data.total_global;
    document.querySelector("#metric-total").textContent = data.total;
    document.querySelector("#metric-searches").textContent = data.pesquisas_total;
    document.querySelector("#metric-new").textContent = data.por_status.novo || 0;
    document.querySelector("#metric-contact").textContent = data.por_status.em_contato || 0;
    document.querySelector("#metric-converted").textContent = data.por_status.convertido || 0;
    renderPipeline(data.por_status);
    renderPrioritySelection();
    renderRows();
    loadDeletionRequests();
    updatePagination(data);
    showMessage(data.total ? "" : state.viewMode === "archived" ? "Nenhum cliente arquivado." : "Nenhum contato encontrado.");
  } catch (error) {
    showMessage(error.message, "error");
  } finally { state.loading = false; }
}

document.querySelector("#deletion-request-list").addEventListener("click", event => {
  const button = event.target.closest(".decide-deletion");
  if (!button) return;
  const card = button.closest("[data-request-id]");
  openResearchDelete(button.dataset.decision === "approve" ? "approve" : "reject", card.dataset.requestId);
});

filters.addEventListener("submit", event => { event.preventDefault(); state.offset = 0; loadLeads(); });
document.querySelector("#clear-lead-filters").addEventListener("click", () => { filters.reset(); state.priority = ""; state.offset = 0; loadLeads(); });
pageSize.addEventListener("change", () => { state.offset = 0; loadLeads(); });
document.querySelector("#lead-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - Number(pageSize.value)); loadLeads(); });
document.querySelector("#lead-next").addEventListener("click", () => { state.offset += Number(pageSize.value); loadLeads(); });
document.querySelector("#export-leads").addEventListener("click", () => { location.href = `/v1/admin/leads.csv?${currentParams(false)}`; });
document.querySelector("#distribute-leads").addEventListener("click", async event => {
  const botao = event.currentTarget;
  botao.disabled = true;
  try {
    const resultado = await responsePayload(await fetch("/v1/admin/leads/distribuir", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }));
    const resumo = Object.entries(resultado.por_responsavel || {}).map(([nome, quantidade]) => `${nome}: ${quantidade}`).join(" · ");
    showMessage(resultado.distribuidos ? `${resultado.distribuidos} lead(s) distribuído(s) — ${resumo}` : "Nenhum lead sem responsável para distribuir.", "success");
    await Promise.all([loadLeads(), loadCrmSummary()]);
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    botao.disabled = false;
  }
});
document.querySelector("#copy-emails").addEventListener("click", async event => {
  const button = event.currentTarget;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Copiando…";
  try {
    const dados = await responsePayload(await fetch(`/v1/admin/leads-emails?${currentParams(false)}`));
    if (!dados.emails.length) { showMessage("Nenhum e-mail encontrado para o filtro atual.", "error"); return; }
    const lista = dados.emails.join(", ");
    await navigator.clipboard.writeText(lista);
    const aviso = dados.total >= dados.limite
      ? `${dados.total} e-mails copiados (limite de ${dados.limite} — refine o filtro para pegar todos).`
      : `${dados.total} e-mail${dados.total === 1 ? "" : "s"} copiado${dados.total === 1 ? "" : "s"} para a área de transferência.`;
    showMessage(aviso, "success");
  } catch (error) {
    showMessage(error.message || "Não foi possível copiar os e-mails.", "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});

crmPipeline.addEventListener("click", event => {
  const button = event.target.closest(".crm-stage");
  if (!button) return;
  statusFilter.value = statusFilter.value === button.dataset.status ? "" : button.dataset.status;
  state.offset = 0;
  loadLeads();
});

function aplicarPrioridade(nome) {
  state.priority = state.priority === nome ? "" : nome;
  state.offset = 0;
  loadLeads();
}

crmPriorities.addEventListener("click", event => {
  const button = event.target.closest(".crm-priority");
  if (!button) return;
  aplicarPrioridade(button.dataset.priority);
});

document.querySelector("#lead-attention-action")?.addEventListener("click", () => {
  const banner = document.querySelector("#lead-attention-banner");
  aplicarPrioridade(banner?.dataset.priority || "atrasadas");
  document.querySelector("#crm-priorities")?.scrollIntoView({ behavior: "smooth", block: "center" });
});

viewButtons.forEach(button => button.addEventListener("click", () => {
  state.viewMode = button.dataset.view;
  viewButtons.forEach(item => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  const researchMode = state.viewMode === "researches";
  const archivedMode = state.viewMode === "archived";
  document.querySelector("#research-column-title").textContent = researchMode ? "Pesquisa" : "Histórico de pesquisas";
  document.querySelector("#activity-column-title").textContent = researchMode ? "Data da pesquisa" : "Última pesquisa";
  viewDescription.textContent = researchMode
    ? "Cada linha representa uma pesquisa dos contatos ativos exibidos nesta página."
    : archivedMode
      ? "Clientes arquivados ficam preservados com suas pesquisas e contatos e podem ser restaurados."
      : "Cada linha representa um contato ativo e resume todo o seu histórico.";
  state.offset = 0;
  loadLeads();
}));

leadsList.addEventListener("change", async event => {
  if (!event.target.matches(".lead-status")) return;
  const select = event.target;
  const row = select.closest("tr");
  const previous = select.dataset.previous;
  select.disabled = true;
  const response = await fetch(`/v1/admin/leads/${row.dataset.leadId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: select.value }) });
  select.disabled = false;
  if (!response.ok) { const erro = await response.json().catch(() => ({})); select.value = previous; showMessage(erro.detail || "Não foi possível atualizar o status.", "error"); return; }
  select.dataset.previous = select.value;
  select.className = `lead-status status-${select.value}`;
  showMessage("Status atualizado.", "success");
  await Promise.all([loadLeads(), loadCrmSummary()]);
});

leadsList.addEventListener("click", event => {
  const deleteResearch = event.target.closest(".request-delete-research");
  if (deleteResearch) {
    openResearchDelete(state.canDeleteResearch ? "direct" : "request", deleteResearch.dataset.researchId);
    return;
  }
  const proposalButton = event.target.closest(".generate-research-proposal");
  if (proposalButton) {
    const leadRow = proposalButton.closest("tr");
    const lead = state.items.find(item => String(item.id) === String(leadRow?.dataset.leadId));
    if (lead) criarProposta(lead, null);
    return;
  }
  const attendanceButton = event.target.closest(".register-attendance");
  if (attendanceButton) {
    const attendanceRow = attendanceButton.closest("tr");
    if (attendanceRow) abrirRegistroAtendimento(attendanceRow.dataset.leadId, attendanceButton.dataset.researchId);
    return;
  }
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
  if (event.target.closest(".view-lead")) openLead(row.dataset.leadId, row.dataset.researchId || null);
  if (event.target.closest(".archive-lead")) { state.archiveId = row.dataset.leadId; archiveDialog.showModal(); }
  if (event.target.closest(".restore-lead")) fetch(`/v1/admin/leads/${row.dataset.leadId}/restaurar`, { method: "POST" }).then(response => { if (response.ok) loadLeads(); else showMessage("Não foi possível restaurar o contato.", "error"); });
});

document.querySelector("#confirm-archive").addEventListener("click", async event => {
  event.preventDefault();
  const response = await fetch(`/v1/admin/leads/${state.archiveId}`, { method: "DELETE" });
  if (response.ok) { archiveDialog.close(); showMessage("Contato arquivado; pesquisas preservadas.", "success"); loadLeads(); }
  else showMessage("Não foi possível arquivar o contato.", "error");
});


// Na tela de Pesquisas (/admin/pesquisas) começamos na lista plana de pesquisas,
// sem o agrupamento por contato — que fica reservado à tela de Leads.
if (location.pathname.startsWith("/admin/pesquisas")) {
  state.viewMode = "researches";
  viewButtons.forEach(item => {
    const active = item.dataset.view === "researches";
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  document.querySelector("#research-column-title").textContent = "Pesquisa";
  document.querySelector("#activity-column-title").textContent = "Data da pesquisa";
  viewDescription.textContent = "Cada linha representa uma pesquisa dos contatos ativos exibidos nesta página.";
}

loadOwners().then(async () => {
  await Promise.all([loadLeads(), loadCrmSummary()]);
  const leadId = new URLSearchParams(location.search).get("lead_id");
  if (/^\d+$/.test(leadId || "")) await openLead(Number(leadId));
});

// Achado da Fase 15.4 (auditoria fina de Leads, 23/09/2026):
// POST /v1/admin/leads/importar já existia pronto e testado no backend,
// mas sem nenhum botão na tela -- só dava pra importar uma carteira
// externa de leads via chamada direta à API.
const importLeadsDialog = document.querySelector("#import-leads-dialog");
const importLeadsForm = document.querySelector("#import-leads-form");

document.querySelector("#import-leads")?.addEventListener("click", () => {
  importLeadsForm.reset();
  const message = document.querySelector("#import-leads-message");
  message.hidden = true;
  importLeadsDialog.showModal();
});

document.querySelector("#cancel-import-leads")?.addEventListener("click", () => importLeadsDialog.close());

importLeadsForm?.addEventListener("submit", async event => {
  event.preventDefault();
  const button = importLeadsForm.querySelector("button[type=submit]");
  const message = document.querySelector("#import-leads-message");
  message.hidden = false;
  message.className = "status-message loading";
  message.textContent = "Importando…";
  button.disabled = true;
  try {
    const response = await fetch("/v1/admin/leads/importar", { method: "POST", body: new FormData(importLeadsForm) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Não foi possível importar a planilha.");
    message.className = "status-message success";
    message.textContent = `${data.criados} lead(s) criado(s) de ${data.total_linhas} linha(s) -- ${data.duplicados} duplicado(s) e ${data.sem_dados_essenciais} sem nome/contato foram ignorados.`;
    importLeadsForm.reset();
    await loadLeads();
  } catch (error) {
    message.className = "status-message error";
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

}
initLeadsPage();

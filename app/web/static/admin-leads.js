const filters = document.querySelector("#lead-filters");
const searchInput = document.querySelector("#lead-search");
const statusFilter = document.querySelector("#lead-status-filter");
const ownerFilter = document.querySelector("#lead-owner-filter");
const originFilter = document.querySelector("#lead-origin-filter");
const dateStart = document.querySelector("#lead-date-start");
const dateEnd = document.querySelector("#lead-date-end");
const marketingFilter = document.querySelector("#lead-marketing-filter");
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
const researchDeleteDialog = document.querySelector("#research-delete-dialog");
const researchDeleteForm = document.querySelector("#research-delete-form");

const state = {
  offset: 0, total: 0, owners: [], archiveId: null, loading: false,
  canManage: false, canArchive: false, canExport: false, canDeleteResearch: false, canPii: false,
  openLeadId: null, deleteResearchId: null, deleteRequestId: null, deleteMode: null,
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
  if (state.viewMode === "archived") params.set("arquivados", "true");
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

async function responsePayload(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Falha na operação (${response.status})`);
  return payload;
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

function duplicateStatus(item) {
  if (!item?.duplicada) return "";
  const title = item.pesquisa_original_id
    ? `Pesquisa repetida. Registro original: ${item.pesquisa_original_id}`
    : "Pesquisa repetida para esta empresa e contato";
  return `<span class="duplicate-research-badge" title="${escapeHtml(title)}">Pesquisa duplicada</span>`;
}

function deletionAction(item) {
  if (item.exclusao_status === "pendente") {
    return `<span class="full-report-status pending">Exclusão aguardando aprovação</span>`;
  }
  const label = state.canDeleteResearch ? "Excluir pesquisa" : "Solicitar exclusão";
  return `<button class="danger-button request-delete-research" type="button" data-research-id="${escapeHtml(item.id)}">${label}</button>`;
}

function contactChannelLabel(value) {
  return ({ telefone: "Telefone", whatsapp: "WhatsApp", email: "E-mail", reuniao: "Reuniao", outro: "Outro" })[value] || value;
}

function contactHistoryItem(item) {
  return `<li class="lead-contact-entry">
    <div><strong>${escapeHtml(contactChannelLabel(item.canal))}</strong><time datetime="${escapeHtml(item.criado_em)}">${formatDate(item.criado_em)}</time></div>
    <p>${escapeHtml(item.resultado || "Sem resultado informado")}</p>
    ${item.observacao ? `<p class="lead-contact-observation">${escapeHtml(item.observacao)}</p>` : ""}
    <small>${escapeHtml(item.operador || "Operador nao informado")} · ${escapeHtml(item.empresa || "Empresa nao informada")} · Pesquisa: ${escapeHtml(item.pesquisa_marca || "Nao vinculada")}</small>
  </li>`;
}

async function loadLeadContacts(leadId, pesquisaId = "") {
  const list = dialogContent.querySelector("#lead-contact-history");
  const count = dialogContent.querySelector("#lead-contact-count");
  if (!list) return;
  list.innerHTML = "<li class=\"lead-contact-empty\">Carregando contatos...</li>";
  const params = new URLSearchParams();
  if (pesquisaId) params.set("pesquisa_id", pesquisaId);
  const response = await fetch(`/v1/admin/leads/${leadId}/contatos?${params}`);
  if (!response.ok) {
    list.innerHTML = "<li class=\"lead-contact-empty error\">Nao foi possivel carregar os contatos.</li>";
    return;
  }
  const data = await response.json();
  if (count) count.textContent = `${data.total} registro${data.total === 1 ? "" : "s"}`;
  list.innerHTML = data.contatos.length
    ? data.contatos.map(contactHistoryItem).join("")
    : "<li class=\"lead-contact-empty\">Nenhum contato registrado para este filtro.</li>";
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
      <td data-label="Atendimento"><strong>${escapeHtml(lead.responsavel_nome || "Não atribuído")}</strong><small>${escapeHtml(originLabel(lead.origem))}</small><small>${lead.proxima_acao_em ? `Próxima ação: ${formatDate(lead.proxima_acao_em)}` : "Sem próxima ação"}</small>${faseMini(lead.fase)}</td>
      <td data-label="Última pesquisa"><time datetime="${escapeHtml(lead.ultima_pesquisa_em || lead.criado_em)}">${formatDate(lead.ultima_pesquisa_em || lead.criado_em)}</time></td>
      <td data-label="Status"><select class="lead-status status-${escapeHtml(lead.status)}" data-previous="${escapeHtml(lead.status)}" aria-label="Status de ${escapeHtml(lead.nome)}" ${lead.arquivado_em || !state.canManage ? "disabled" : ""}>${statusOptions(lead.status)}</select></td>
      <td data-label="Ações"><div class="lead-row-actions"><button class="view-lead secondary-button" type="button">Abrir contato</button>${state.canArchive ? (lead.arquivado_em ? `<button class="restore-lead secondary-button" type="button">Restaurar</button>` : `<button class="archive-lead danger-link" type="button">Arquivar</button>`) : ""}</div></td>
    </tr>${quickResearchHistory(lead)}`;
}

function researchRow(lead, item) {
  const digits = phoneDigits(lead.telefone);
  return `<tr data-lead-id="${lead.id}" class="research-view-row ${lead.arquivado_em ? "archived" : ""}">
    <td data-label="Contato"><strong>${escapeHtml(lead.nome)}</strong><small>${escapeHtml(lead.empresa || "Empresa não informada")}</small><span>${escapeHtml(lead.email)}</span>${digits ? `<a href="https://wa.me/${digits}" target="_blank" rel="noopener">WhatsApp</a>` : ""}</td>
    <td data-label="Pesquisa"><strong>${escapeHtml(item.marca)}</strong>${duplicateStatus(item)}<small>${escapeHtml(item.atividade || "Atividade não informada")}</small>${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}${item.risco_pontuacao !== null ? ` · ${item.risco_pontuacao} pontos` : ""}</span>` : `<span class="risk-pill">Risco não calculado</span>`}${fullReportStatus(item, true)}</td>
    <td data-label="Atendimento"><strong>${escapeHtml(lead.responsavel_nome || "Não atribuído")}</strong><small>${escapeHtml(originLabel(lead.origem))}</small>${faseMini(lead.fase)}</td>
    <td data-label="Data da pesquisa"><time datetime="${escapeHtml(item.criado_em)}">${formatDate(item.criado_em)}</time></td>
    <td data-label="Status"><select class="lead-status status-${escapeHtml(lead.status)}" data-previous="${escapeHtml(lead.status)}" aria-label="Status de ${escapeHtml(lead.nome)}" ${lead.arquivado_em || !state.canManage ? "disabled" : ""}>${statusOptions(lead.status)}</select></td>
    <td data-label="Ações"><div class="lead-row-actions"><a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir análise</a><button class="view-lead secondary-button" type="button">Abrir contato</button>${deletionAction(item)}</div></td>
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
    if (state.viewMode === "archived") {
      document.querySelector('[data-view="archived"]').textContent = `Clientes arquivados (${data.total})`;
    }
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
    loadDeletionRequests();
    updatePagination(data);
    showMessage(data.total ? "" : state.viewMode === "archived" ? "Nenhum cliente arquivado." : "Nenhum contato encontrado.");
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
    <div><strong>${escapeHtml(item.marca)}</strong>${duplicateStatus(item)}<small>${formatDate(item.criado_em)}</small></div>
    <p>${escapeHtml(item.atividade || "Atividade não informada")}</p>
    <div class="lead-research-meta">${item.classe_nice ? `<span>NCL ${escapeHtml(item.classe_nice)}</span>` : ""}${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}${item.risco_pontuacao !== null ? ` · ${item.risco_pontuacao} pontos` : ""}</span>` : `<span>Análise ainda não calculada</span>`}${fullReportStatus(item)}</div>
    <div class="lead-research-actions"><a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir Central de Análise</a>${reportAction}${deletionAction(item)}</div>
  </article>`;
}

async function openLead(id) {
  state.openLeadId = id;
  dialogContent.innerHTML = "<p>Carregando histórico…</p>";
  if (!dialog.open) dialog.showModal();
  const response = await fetch(`/v1/admin/leads/${id}`);
  if (!response.ok) { dialogContent.innerHTML = "<p class=\"status-message error\">Não foi possível abrir o contato.</p>"; return; }
  const lead = await response.json();
  const researchOptions = lead.pesquisas.map(item =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.marca)} · ${formatDate(item.criado_em, false)}</option>`
  ).join("");
  document.querySelector("#lead-dialog-title").textContent = lead.nome;
  dialogContent.innerHTML = `
    <section class="lead-contact-summary"><div><span>E-mail</span><a href="mailto:${escapeHtml(lead.email)}">${escapeHtml(lead.email)}</a></div><div><span>Telefone</span><a href="tel:${escapeHtml(lead.telefone)}">${escapeHtml(lead.telefone)}</a></div><div><span>CPF/CNPJ</span><strong>${escapeHtml(lead.documento || "Não informado")}</strong></div><div><span>Empresa</span><strong>${escapeHtml(lead.empresa || "Não informada")}</strong></div><div><span>Marketing</span><strong>${lead.aceite_marketing ? "Autorizado" : "Não autorizado"}</strong></div></section>
    <div class="lead-dialog-grid">
    <section class="lead-funil lg-full" id="lead-funil"><p class="lead-funil-loading">Carregando funil…</p></section>
    <section class="lead-history lg-full"><header><div><p class="eyebrow">Histórico</p><h3>${lead.pesquisas.length} pesquisa${lead.pesquisas.length === 1 ? "" : "s"}</h3></div></header>${lead.pesquisas.length ? lead.pesquisas.map(researchCard).join("") : "<p>Nenhuma pesquisa vinculada.</p>"}</section>
    <section class="lead-documentos lg-full" id="lead-documentos"><p class="lead-funil-loading">Carregando documentos…</p></section>
    <section class="lead-guias lg-full" id="lead-guias"><p class="lead-funil-loading">Carregando guias do INPI…</p></section>
    <section class="lead-checklist" id="lead-checklist"><p class="lead-funil-loading">Carregando checklist…</p></section>
    ${state.canManage ? `<form id="lead-crm-form" data-lead-id="${lead.id}" class="lead-crm-form">
      <label><span>Status</span><select name="status">${statusOptions(lead.status)}</select></label>
      <label><span>Responsável</span><select name="responsavel_id">${ownerOptions(lead.responsavel_id)}</select></label>
      <label><span>Próxima ação</span><input name="proxima_acao_em" type="datetime-local" value="${lead.proxima_acao_em ? new Date(lead.proxima_acao_em).toISOString().slice(0, 16) : ""}" /></label>
      <label><span>Tags, separadas por vírgula</span><input name="tags" maxlength="400" value="${escapeHtml((lead.tags || []).join(", "))}" /></label>
      ${state.canPii ? `<label><span>CPF/CNPJ</span><input name="documento" inputmode="numeric" maxlength="18" value="${escapeHtml(lead.documento || "")}" placeholder="Somente para cadastro interno" /></label>` : ""}
      <label class="lead-notes"><span>Anotações internas</span><textarea name="notas" maxlength="4000" rows="5">${escapeHtml(lead.notas || "")}</textarea></label>
      <div><button class="primary-button" type="submit">Salvar atendimento</button><a class="secondary-button" href="/admin/crm?lead_id=${lead.id}">Criar lembrete</a><span id="lead-save-message" role="status"></span></div>
    </form>` : `<section class="lead-readonly-note">Você possui acesso somente para consulta.</section>`}
    </div><aside class="lead-dialog-aside">
    <section class="lead-contact-log">
      <header><div><p class="eyebrow">CRM</p><h3>Contatos realizados</h3></div><span id="lead-contact-count">0 registros</span></header>
      <label class="lead-contact-filter"><span>Filtrar pela pesquisa</span><select id="lead-contact-filter"><option value="">Todas as pesquisas desta empresa</option>${researchOptions}</select></label>
      ${state.canManage && lead.pesquisas.length ? `<form id="lead-contact-form" data-lead-id="${lead.id}" class="lead-contact-form">
        <label><span>Pesquisa relacionada</span><select name="pesquisa_id" required><option value="">Selecione a pesquisa</option>${researchOptions}</select></label>
        <label><span>Canal</span><select name="canal"><option value="telefone">Telefone</option><option value="whatsapp">WhatsApp</option><option value="email">E-mail</option><option value="reuniao">Reunião</option><option value="outro">Outro</option></select></label>
        <label><span>Resultado</span><input name="resultado" maxlength="150" placeholder="Ex.: proposta enviada" /></label>
        <label class="lead-contact-observation-field"><span>Observações do contato</span><textarea name="observacao" maxlength="2000" rows="3" placeholder="Registre o que foi conversado e a próxima orientação."></textarea></label>
        <div><button class="primary-button" type="submit">Registrar contato</button><span id="contact-save-message" role="status"></span></div>
      </form>` : ""}
      <ol id="lead-contact-history" class="lead-contact-history"><li class="lead-contact-empty">Carregando contatos...</li></ol>
    </section>
    </aside>`;
  await loadLeadContacts(lead.id);
  await renderFunil(lead.id);
  await renderDocumentos(lead.id);
  await renderChecklistFase(lead.id);
  await renderGuiasInpi(lead.id);
}

const FASE_LABELS = {
  contato_inicial: "Contato inicial",
  relatorio_enviado: "Relatório enviado",
  proposta_enviada: "Proposta enviada",
  proposta_aceita: "Proposta aceita",
  pagamento_realizado: "Pagamento",
  protocolo_inpi: "Protocolo INPI",
  processo_inpi: "Processo no INPI",
};

function faseMini(fase) {
  const ordem = Object.keys(FASE_LABELS);
  const idx = ordem.indexOf(fase);
  const dots = ordem.map((f, i) => {
    const cls = i < idx ? "done" : i === idx ? "current" : "pending";
    return `<span class="fm-dot ${cls}" title="${escapeHtml(FASE_LABELS[f] || f)}"></span>`;
  }).join("");
  const label = FASE_LABELS[fase] || "Contato inicial";
  return `<div class="fase-mini" title="Fase do lead: ${escapeHtml(label)}"><span class="fase-mini-dots">${dots}</span><span class="fase-mini-label">${escapeHtml(label)}</span></div>`;
}

const DOC_LABELS = { procuracao: "Procuração", gru: "GRU", protocolo: "Protocolo", oposicao: "Oposição", certificado: "Certificado" };
const DOC_STATUS = [["pendente", "Pendente"], ["em_andamento", "Em andamento"], ["concluido", "Concluído"], ["nao_aplicavel", "N/A"]];

async function renderDocumentos(leadId) {
  const box = document.querySelector("#lead-documentos");
  if (!box) return;
  let data;
  try { data = await (await fetch(`/v1/admin/leads/${leadId}/documentos`)).json(); }
  catch { box.innerHTML = ""; return; }
  const canManage = state.canManage;
  const statusOpts = cur => DOC_STATUS.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("");
  const statusLabel = v => (DOC_STATUS.find(s => s[0] === v) || ["", "—"])[1];
  const rows = (data.documentos || []).map(d => {
    const dataVal = d.data ? String(d.data).slice(0, 10) : "";
    if (!canManage) {
      return `<tr><td class="doc-type" data-label="Tipo">${escapeHtml(DOC_LABELS[d.tipo] || d.tipo)}</td><td data-label="Número">${escapeHtml(d.numero || "—")}</td><td data-label="Data">${d.data ? formatDate(d.data, false) : "—"}</td><td data-label="Status">${escapeHtml(statusLabel(d.status))}</td><td data-label="Observações">${escapeHtml(d.observacoes || "")}</td></tr>`;
    }
    return `<tr data-tipo="${escapeHtml(d.tipo)}"><td class="doc-type" data-label="Tipo">${escapeHtml(DOC_LABELS[d.tipo] || d.tipo)}</td><td data-label="Número"><input data-f="numero" value="${escapeHtml(d.numero || "")}" maxlength="60" placeholder="—"></td><td data-label="Data"><input data-f="data" type="date" value="${escapeHtml(dataVal)}"></td><td data-label="Status"><select data-f="status">${statusOpts(d.status || "pendente")}</select></td><td data-label="Observações"><input data-f="observacoes" value="${escapeHtml(d.observacoes || "")}" maxlength="2000" placeholder="—"></td></tr>`;
  }).join("");
  box.innerHTML = `<header><p class="eyebrow">Documentos</p><h3>Procuração, GRU, protocolo, oposição, certificado</h3></header><div class="doc-table-scroll"><table class="lead-docs"><thead><tr><th>Tipo</th><th>Número</th><th>Data</th><th>Status</th><th>Observações</th></tr></thead><tbody>${rows}</tbody></table></div>${canManage ? `<div class="lead-docs-actions"><button class="secondary-button" id="lead-docs-save" type="button">Salvar documentos</button><span id="lead-docs-msg" role="status"></span></div>` : ""}`;
  const saveBtn = box.querySelector("#lead-docs-save");
  if (saveBtn) saveBtn.addEventListener("click", async () => {
    const documentos = [...box.querySelectorAll("tr[data-tipo]")].map(tr => ({
      tipo: tr.dataset.tipo,
      numero: tr.querySelector('[data-f="numero"]').value,
      data: tr.querySelector('[data-f="data"]').value || null,
      status: tr.querySelector('[data-f="status"]').value,
      observacoes: tr.querySelector('[data-f="observacoes"]').value,
    }));
    saveBtn.disabled = true;
    const r = await fetch(`/v1/admin/leads/${leadId}/documentos`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ documentos }) });
    saveBtn.disabled = false;
    const msg = box.querySelector("#lead-docs-msg");
    if (msg) msg.textContent = r.ok ? "Documentos salvos." : "Erro ao salvar.";
    if (r.ok) await renderDocumentos(leadId);
  });
}

async function renderChecklistFase(leadId) {
  const box = document.querySelector("#lead-checklist");
  if (!box) return;
  let data;
  try { data = await (await fetch(`/v1/admin/leads/${leadId}/checklist`)).json(); }
  catch { box.innerHTML = ""; return; }
  const canManage = state.canManage;
  const faseLabel = FASE_LABELS[data.fase] || data.fase || "—";
  const itens = data.itens || [];
  const feitos = itens.filter(i => i.concluido).length;
  const linhas = itens.map(i =>
    `<li class="chk-item${i.concluido ? " done" : ""}" data-id="${i.id}">
      <label><input type="checkbox" class="chk-toggle"${i.concluido ? " checked" : ""}${canManage ? "" : " disabled"}><span>${escapeHtml(i.descricao)}</span></label>
      ${canManage ? `<button type="button" class="chk-del" title="Remover" aria-label="Remover item">×</button>` : ""}
    </li>`
  ).join("");
  const vazio = itens.length === 0
    ? `<p class="chk-empty">Nenhum item nesta etapa.${data.tem_padrao && canManage ? " Você pode aplicar o checklist padrão." : ""}</p>`
    : "";
  box.innerHTML = `<header><p class="eyebrow">Checklist da etapa</p><h3>${escapeHtml(faseLabel)}</h3><span class="chk-count">${feitos}/${itens.length}</span></header>
    <ul class="chk-list">${linhas}</ul>${vazio}
    ${canManage ? `<div class="chk-actions">
      <form class="chk-add" autocomplete="off"><input type="text" maxlength="300" placeholder="Adicionar item…" aria-label="Novo item de checklist"><button class="secondary-button" type="submit">Adicionar</button></form>
      ${data.tem_padrao ? `<button type="button" class="secondary-button chk-padrao">Aplicar checklist padrão</button>` : ""}
      <span class="chk-msg" role="status"></span>
    </div>` : ""}`;
  if (!canManage) return;
  box.querySelectorAll(".chk-toggle").forEach(cb => cb.addEventListener("change", async () => {
    const id = cb.closest(".chk-item").dataset.id;
    cb.disabled = true;
    const r = await fetch(`/v1/admin/checklist-fase/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ concluido: cb.checked }) });
    if (r.ok) await renderChecklistFase(leadId); else { cb.checked = !cb.checked; cb.disabled = false; }
  }));
  box.querySelectorAll(".chk-del").forEach(btn => btn.addEventListener("click", async () => {
    const id = btn.closest(".chk-item").dataset.id;
    btn.disabled = true;
    const r = await fetch(`/v1/admin/checklist-fase/${id}`, { method: "DELETE" });
    if (r.ok || r.status === 204) await renderChecklistFase(leadId); else btn.disabled = false;
  }));
  const addForm = box.querySelector(".chk-add");
  if (addForm) addForm.addEventListener("submit", async e => {
    e.preventDefault();
    const input = addForm.querySelector("input");
    const descricao = input.value.trim();
    if (!descricao) return;
    input.disabled = true;
    const r = await fetch(`/v1/admin/leads/${leadId}/checklist`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ descricao }) });
    if (r.ok) await renderChecklistFase(leadId); else { input.disabled = false; const m = box.querySelector(".chk-msg"); if (m) m.textContent = "Erro ao adicionar."; }
  });
  const padraoBtn = box.querySelector(".chk-padrao");
  if (padraoBtn) padraoBtn.addEventListener("click", async () => {
    padraoBtn.disabled = true;
    const r = await fetch(`/v1/admin/leads/${leadId}/checklist/padrao`, { method: "POST" });
    if (r.ok) await renderChecklistFase(leadId); else { padraoBtn.disabled = false; const m = box.querySelector(".chk-msg"); if (m) m.textContent = "Erro ao aplicar padrão."; }
  });
}

function guiaMoney(v) { return v == null || v === "" ? "" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }); }

function guiaVencBadge(g) {
  if (g.status === "paga") return `<span class="guia-badge ok">Paga${g.pago_em ? " · " + formatDate(g.pago_em, false) : ""}</span>`;
  if (g.status === "cancelada") return `<span class="guia-badge muted">Cancelada</span>`;
  if (!g.vencimento) return `<span class="guia-badge pend">Pendente</span>`;
  const hoje = new Date(); hoje.setHours(0, 0, 0, 0);
  const venc = new Date(String(g.vencimento).slice(0, 10) + "T00:00:00");
  const dias = Math.round((venc - hoje) / 86400000);
  const dt = formatDate(g.vencimento, false);
  if (dias < 0) return `<span class="guia-badge late">Vencida · ${dt}</span>`;
  if (dias <= 7) return `<span class="guia-badge soon">Vence em ${dias}d · ${dt}</span>`;
  return `<span class="guia-badge pend">Vence ${dt}</span>`;
}

async function renderGuiasInpi(leadId) {
  const box = document.querySelector("#lead-guias");
  if (!box) return;
  let data;
  try { data = await (await fetch(`/v1/admin/leads/${leadId}/guias`)).json(); }
  catch { box.innerHTML = ""; return; }
  const canManage = state.canManage;
  const guias = data.guias || [];
  const sugestoes = (data.sugestoes || []).filter(s => !s.ja_registrada);
  const guiaRows = guias.length ? guias.map(g => {
    const tarifa = g.reduzido ? ` <span class="guia-tag">reduzido</span>` : "";
    const meta = [g.codigo ? `Cód. ${escapeHtml(g.codigo)}` : "", guiaMoney(g.valor), g.numero_gru ? `GRU ${escapeHtml(g.numero_gru)}` : ""].filter(Boolean).join(" · ");
    const acts = canManage ? `<div class="guia-acts">${g.status === "paga" ? `<button class="guia-reabrir" data-id="${g.id}" type="button">Reabrir</button>` : `<button class="guia-pagar" data-id="${g.id}" type="button">Marcar paga</button>`}<button class="guia-del" data-id="${g.id}" type="button" title="Excluir" aria-label="Excluir">×</button></div>` : "";
    return `<li class="guia-item"><div class="guia-main"><strong>${escapeHtml(g.descricao)}</strong>${tarifa}<br><small>${escapeHtml(meta) || "—"}</small></div>${guiaVencBadge(g)}${acts}</li>`;
  }).join("") : `<li class="guia-empty">Nenhuma guia registrada.</li>`;

  const sugRows = (canManage && sugestoes.length) ? `<div class="guia-sug"><p class="guia-sug-title">Esta fase costuma pedir:</p>${sugestoes.map(s => {
    const preco = [s.valor_normal != null ? `${guiaMoney(s.valor_normal)} normal` : "", s.valor_reduzido != null ? `${guiaMoney(s.valor_reduzido)} reduzido` : ""].filter(Boolean).join(" · ") || "valor a definir";
    return `<button type="button" class="guia-sug-btn" data-servico="${escapeHtml(s.servico)}" data-codigo="${escapeHtml(s.codigo || "")}" data-desc="${escapeHtml(s.descricao)}" data-vn="${s.valor_normal == null ? "" : s.valor_normal}" data-vr="${s.valor_reduzido == null ? "" : s.valor_reduzido}">+ ${escapeHtml(s.descricao)}${s.codigo ? ` (${escapeHtml(s.codigo)})` : ""} <small>${escapeHtml(preco)}</small></button>`;
  }).join("")}</div>` : "";

  const form = canManage ? `<form class="guia-form" hidden autocomplete="off">
    <div class="guia-two"><label><span>Descrição</span><input name="descricao" maxlength="200" required></label><label><span>Código INPI</span><input name="codigo" maxlength="10" placeholder="Ex.: 389"></label></div>
    <div class="guia-two"><label><span>Tarifa</span><select name="tarifa"><option value="normal">Normal</option><option value="reduzido">Reduzido (ME/EPP/PF)</option></select></label><label><span>Valor (R$)</span><input name="valor" type="number" step="0.01" min="0" placeholder="0,00"></label></div>
    <div class="guia-two"><label><span>Número da GRU</span><input name="numero_gru" maxlength="60" placeholder="Nosso número"></label><label><span>Vencimento</span><input name="vencimento" type="date"></label></div>
    <label><span>Observações</span><input name="observacoes" maxlength="2000" placeholder="opcional"></label>
    <div class="guia-form-acts"><button class="secondary-button" type="submit">Salvar guia</button><button class="guia-cancel secondary-button" type="button">Cancelar</button><span class="guia-msg" role="status"></span></div>
  </form>` : "";

  box.innerHTML = `<header><p class="eyebrow">Guias do INPI (GRU)</p><h3>Retribuições emitidas</h3>${canManage ? `<button class="guia-nova secondary-button" type="button">Registrar GRU</button>` : ""}</header>${sugRows}<ul class="guia-list">${guiaRows}</ul>${form}`;

  if (!canManage) return;
  const formEl = box.querySelector(".guia-form");
  const tarifas = { vn: "", vr: "" };
  const applyTarifa = () => {
    const v = formEl.elements.tarifa.value === "reduzido" ? tarifas.vr : tarifas.vn;
    if (v !== "") formEl.elements.valor.value = v;
  };
  const show = () => { formEl.hidden = false; formEl.querySelector('[name="descricao"]').focus(); };
  box.querySelector(".guia-nova")?.addEventListener("click", () => { formEl.reset(); tarifas.vn = ""; tarifas.vr = ""; delete formEl.dataset.servico; show(); });
  box.querySelectorAll(".guia-sug-btn").forEach(btn => btn.addEventListener("click", () => {
    formEl.reset();
    formEl.elements.descricao.value = btn.dataset.desc;
    formEl.elements.codigo.value = btn.dataset.codigo;
    tarifas.vn = btn.dataset.vn; tarifas.vr = btn.dataset.vr;
    formEl.elements.tarifa.value = "normal";
    formEl.dataset.servico = btn.dataset.servico;
    applyTarifa();
    show();
  }));
  formEl.elements.tarifa.addEventListener("change", applyTarifa);
  formEl.querySelector(".guia-cancel").addEventListener("click", () => { formEl.hidden = true; });
  formEl.addEventListener("submit", async e => {
    e.preventDefault();
    const payload = {
      descricao: formEl.elements.descricao.value.trim(),
      codigo: formEl.elements.codigo.value.trim() || null,
      servico: formEl.dataset.servico || null,
      reduzido: formEl.elements.tarifa.value === "reduzido",
      valor: formEl.elements.valor.value === "" ? null : Number(formEl.elements.valor.value),
      numero_gru: formEl.elements.numero_gru.value.trim() || null,
      vencimento: formEl.elements.vencimento.value || null,
      observacoes: formEl.elements.observacoes.value.trim() || null,
    };
    const r = await fetch(`/v1/admin/leads/${leadId}/guias`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (r.ok) await renderGuiasInpi(leadId);
    else { const m = formEl.querySelector(".guia-msg"); if (m) m.textContent = "Erro ao salvar."; }
  });
  box.querySelectorAll(".guia-pagar").forEach(b => b.addEventListener("click", async () => {
    await fetch(`/v1/admin/guias-inpi/${b.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "paga" }) });
    await renderGuiasInpi(leadId);
  }));
  box.querySelectorAll(".guia-reabrir").forEach(b => b.addEventListener("click", async () => {
    await fetch(`/v1/admin/guias-inpi/${b.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "pendente" }) });
    await renderGuiasInpi(leadId);
  }));
  box.querySelectorAll(".guia-del").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(`/v1/admin/guias-inpi/${b.dataset.id}`, { method: "DELETE" });
    if (r.ok || r.status === 204) await renderGuiasInpi(leadId);
  }));
}

async function renderFunil(leadId) {
  const box = document.querySelector("#lead-funil");
  if (!box) return;
  let data;
  try { data = await (await fetch(`/v1/admin/leads/${leadId}/funil`)).json(); }
  catch { box.innerHTML = ""; return; }
  const ordem = data.ordem || [];
  const atualIdx = ordem.indexOf(data.fase);
  const datas = {};
  (data.historico || []).forEach(h => { datas[h.fase] = h.entrou_em; });
  const steps = ordem.map((f, i) => {
    const cls = i < atualIdx ? "done" : i === atualIdx ? "current" : "pending";
    const quando = datas[f] ? formatDate(datas[f], false) : (i === atualIdx ? "atual" : "");
    return `<li class="lfs ${cls}"><span class="lfs-dot">${i < atualIdx ? "✓" : ""}</span><span class="lfs-label">${escapeHtml(FASE_LABELS[f] || f)}</span><span class="lfs-date">${escapeHtml(quando)}</span></li>`;
  }).join("");
  const controle = state.canManage
    ? `<div class="lead-funil-move"><label><span>Mover para</span><select id="lead-fase-select">${ordem.map(f => `<option value="${f}" ${f === data.fase ? "selected" : ""}>${escapeHtml(FASE_LABELS[f] || f)}</option>`).join("")}</select></label><button class="secondary-button" id="lead-fase-save" type="button">Salvar fase</button></div>`
    : "";
  box.innerHTML = `<header><p class="eyebrow">Funil de atendimento</p><h3>Fase do lead</h3></header><ol class="lead-funil-steps">${steps}</ol>${controle}`;
  const saveBtn = box.querySelector("#lead-fase-save");
  if (saveBtn) saveBtn.addEventListener("click", async () => {
    const fase = box.querySelector("#lead-fase-select").value;
    saveBtn.disabled = true;
    const r = await fetch(`/v1/admin/leads/${leadId}/fase`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fase }) });
    if (r.ok) await renderFunil(leadId); else { saveBtn.disabled = false; saveBtn.textContent = "Erro — tentar de novo"; }
  });
}

function openResearchDelete(mode, id) {
  state.deleteMode = mode;
  state.deleteResearchId = mode === "direct" || mode === "request" ? id : null;
  state.deleteRequestId = mode === "approve" || mode === "reject" ? id : null;
  researchDeleteForm.reset();
  const passwordRequired = mode === "direct" || mode === "approve";
  const reasonRequired = mode === "direct" || mode === "request" || mode === "reject";
  document.querySelector("#research-delete-password-field").hidden = !passwordRequired;
  researchDeleteForm.elements.senha.required = passwordRequired;
  researchDeleteForm.elements.motivo.required = reasonRequired;
  const titles = {
    direct: "Excluir pesquisa permanentemente?",
    request: "Solicitar exclusão da pesquisa?",
    approve: "Aprovar e excluir a pesquisa?",
    reject: "Rejeitar solicitação de exclusão?",
  };
  const descriptions = {
    direct: "Relatórios, análises e previsões vinculadas serão removidos. Confirme com sua senha atual.",
    request: "A pesquisa permanecerá disponível até um administrador analisar a solicitação.",
    approve: "A aprovação remove definitivamente a pesquisa e exige sua senha atual.",
    reject: "Informe ao solicitante por que a pesquisa deve ser preservada.",
  };
  document.querySelector("#research-delete-title").textContent = titles[mode];
  document.querySelector("#research-delete-description").textContent = descriptions[mode];
  document.querySelector("#research-delete-message").hidden = true;
  researchDeleteDialog.showModal();
}

document.querySelector("#cancel-research-delete").addEventListener("click", () => {
  researchDeleteDialog.close();
});

researchDeleteForm.addEventListener("submit", async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(researchDeleteForm));
  const statusBox = document.querySelector("#research-delete-message");
  statusBox.hidden = false;
  statusBox.className = "status-message loading";
  statusBox.textContent = "Processando…";
  try {
    if (state.deleteMode === "direct") {
      await responsePayload(await fetch(`/v1/admin/pesquisas/${encodeURIComponent(state.deleteResearchId)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ senha: data.senha, motivo: data.motivo }),
      }));
      showMessage("Pesquisa excluída e operação registrada na auditoria.", "success");
    } else if (state.deleteMode === "request") {
      await responsePayload(await fetch(`/v1/admin/pesquisas/${encodeURIComponent(state.deleteResearchId)}/solicitar-exclusao`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ motivo: data.motivo }),
      }));
      showMessage("Solicitação de exclusão enviada ao administrador.", "success");
    } else {
      await responsePayload(await fetch(`/v1/admin/exclusoes-pesquisas/${state.deleteRequestId}/decidir`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decisao: state.deleteMode === "approve" ? "aprovar" : "rejeitar",
          senha: data.senha || null,
          observacao: data.motivo || null,
        }),
      }));
      showMessage(state.deleteMode === "approve" ? "Exclusão aprovada e executada." : "Solicitação rejeitada.", "success");
    }
    researchDeleteDialog.close();
    await loadLeads();
    if (state.openLeadId && dialog.open) await openLead(state.openLeadId);
  } catch (error) {
    statusBox.className = "status-message error";
    statusBox.textContent = error.message;
  }
});

document.querySelector("#deletion-request-list").addEventListener("click", event => {
  const button = event.target.closest(".decide-deletion");
  if (!button) return;
  const card = button.closest("[data-request-id]");
  openResearchDelete(button.dataset.decision === "approve" ? "approve" : "reject", card.dataset.requestId);
});

dialogContent.addEventListener("click", async event => {
  const deleteResearch = event.target.closest(".request-delete-research");
  if (deleteResearch) {
    openResearchDelete(state.canDeleteResearch ? "direct" : "request", deleteResearch.dataset.researchId);
    return;
  }
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

dialogContent.addEventListener("change", event => {
  if (!event.target.matches("#lead-contact-filter")) return;
  loadLeadContacts(state.openLeadId, event.target.value);
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
  if (event.target.matches("#lead-contact-form")) {
    event.preventDefault();
    const form = event.target;
    const data = new FormData(form);
    const saveMessage = dialogContent.querySelector("#contact-save-message");
    const payload = {
      pesquisa_id: data.get("pesquisa_id"),
      canal: data.get("canal"),
      resultado: data.get("resultado") || null,
      observacao: data.get("observacao") || null,
    };
    saveMessage.textContent = "Salvando...";
    const response = await fetch(`/v1/admin/leads/${form.dataset.leadId}/contatos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      saveMessage.textContent = detail.detail || "Nao foi possivel registrar.";
      return;
    }
    form.reset();
    saveMessage.textContent = "Contato registrado.";
    dialogContent.querySelector("#lead-contact-filter").value = "";
    await Promise.all([loadLeadContacts(Number(form.dataset.leadId)), loadLeads(), loadCrmSummary()]);
    return;
  }
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
    registrar_contato: true,
  };
  if (state.canPii) payload.documento = data.get("documento") || null;
  const saveMessage = document.querySelector("#lead-save-message");
  saveMessage.textContent = "Salvando…";
  const response = await fetch(`/v1/admin/leads/${form.dataset.leadId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    saveMessage.textContent = detail.detail || "Não foi possível salvar.";
    return;
  }
  form.elements.proxima_acao_em.value = "";
  form.elements.tags.value = "";
  form.elements.notas.value = "";
  saveMessage.textContent = "Atendimento salvo. Formulário pronto para um novo registro.";
  await Promise.all([
    loadLeadContacts(Number(form.dataset.leadId)),
    loadLeads(),
    loadCrmSummary(),
  ]);
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

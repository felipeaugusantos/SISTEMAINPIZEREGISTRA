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
const researchMoveDialog = document.querySelector("#research-move-dialog");
const researchMoveForm = document.querySelector("#research-move-form");
const proposalDialog = document.querySelector("#proposal-dialog");
const proposalForm = document.querySelector("#proposal-form");
let proposalContext = null;

const state = {
  offset: 0, total: 0, owners: [], archiveId: null, loading: false,
  canManage: false, canArchive: false, canExport: false, canDeleteResearch: false, canPii: false,
  openLeadId: null, deleteResearchId: null, deleteRequestId: null, deleteMode: null, moveResearchId: null,
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
const MOTIVOS_PERDA_LABELS = { preco: "Preço / orçamento", concorrente: "Escolheu concorrente", sem_resposta: "Sem resposta do cliente", fora_perfil: "Fora do perfil / inviável", outro: "Outro" };
function motivoPerdaOptions(selected) {
  return `<option value="">Selecione…</option>` + Object.entries(MOTIVOS_PERDA_LABELS).map(([value, label]) =>
    `<option value="${value}" ${selected === value ? "selected" : ""}>${escapeHtml(label)}</option>`
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
  if (item.review_required) {
    return `<span class="full-report-status pending">Revisão obrigatória · ${escapeHtml(String(item.analysis_state || "PENDING_REVIEW").replaceAll("_", " "))}</span>`;
  }
  return `<span class="full-report-status pending">Validado · aguardando emissão</span>`;
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

function moveAction(item) {
  if (!state.canManage) return "";
  return `<button class="secondary-button move-research" type="button" data-research-id="${escapeHtml(item.id)}" data-research-marca="${escapeHtml(item.marca)}">Não é este cliente — mover</button>`;
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
    document.querySelector("#crm-distribuicao-desligada").hidden =
      data.distribuicao_automatica_ativa || !data.sem_responsavel;
    state.semResponsavel = data.sem_responsavel || 0;
    atualizarBotaoDistribuir();
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
    atualizarBotaoDistribuir();
    if (state.viewMode === "archived") {
      document.querySelector('[data-view="archived"]').textContent = `Clientes arquivados (${data.total})`;
    }
    document.querySelector("#export-leads").hidden = !state.canExport;
    document.querySelector("#copy-emails").hidden = !state.canExport;
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
    : item.analysis_state !== "VALIDATED"
      ? `<span class="full-report-hint">Conclua a revisão obrigatória na Central de Análise.</span>`
      : state.canManage
      ? `<button class="secondary-button generate-full-report" type="button" data-research-id="${escapeHtml(item.id)}">${item.relatorio_completo_gerado ? "Baixar completo novamente" : "Gerar relatório completo"}</button>`
      : `<span class="full-report-hint">Geração disponível para operadores autorizados.</span>`;
  return `<article class="lead-research-card">
    <div><strong>${escapeHtml(item.marca)}</strong>${duplicateStatus(item)}<small>${formatDate(item.criado_em)}</small></div>
    <p>${escapeHtml(item.atividade || "Atividade não informada")}</p>
    <div class="lead-research-meta">${item.classe_nice ? `<span>NCL ${escapeHtml(item.classe_nice)}</span>` : ""}${item.risco_nivel ? `<span class="risk-pill risk-${escapeHtml(item.risco_nivel)}">Risco ${escapeHtml(riskLabels[item.risco_nivel] || item.risco_nivel)}${item.risco_pontuacao !== null ? ` · ${item.risco_pontuacao} pontos` : ""}</span>` : `<span>Análise ainda não calculada</span>`}${fullReportStatus(item)}</div>
    <div class="lead-research-actions"><a class="secondary-button" href="/admin/analises/${encodeURIComponent(item.id)}">Abrir Central de Análise</a>${reportAction}${moveAction(item)}${deletionAction(item)}</div>
  </article>`;
}

async function renderPortalMessages(lead) {
  if (!state.canManage) return;
  const card = document.createElement("section");
  card.className = "lead-portal-messages lead-contact-log";
  card.innerHTML = `<header><div><p class="eyebrow">PORTAL DO CLIENTE</p><h3>Mensagens do cliente</h3></div><span data-portal-message-count>0 mensagens</span></header><ol class="lead-contact-history" data-portal-message-list><li class="lead-contact-empty">Carregando mensagens…</li></ol><form class="lead-portal-reply-form"><textarea name="mensagem" rows="3" maxlength="4000" placeholder="Responda ao cliente pelo portal…" required></textarea><div><button class="primary-button" type="submit">Enviar resposta</button><span class="portal-reply-status" role="status"></span></div></form>`;
  const portalCard = dialogContent.querySelector(".lead-portal-access");
  if (portalCard) portalCard.after(card); else dialogContent.prepend(card);
  const list = card.querySelector("[data-portal-message-list]");
  const count = card.querySelector("[data-portal-message-count]");
  const carregar = async () => {
    try {
      const response = await fetch(`/v1/admin/leads/${lead.id}/portal-mensagens`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Não foi possível carregar as mensagens.");
      const mensagens = data.mensagens || [];
      count.textContent = `${mensagens.length} mensagem${mensagens.length === 1 ? "" : "s"}`;
      list.innerHTML = mensagens.length ? mensagens.map(item => `<li class="lead-contact-entry ${item.autor_tipo === "cliente" ? "portal-message-client" : "portal-message-operator"}"><div><strong>${item.autor_tipo === "cliente" ? "Cliente" : "Atendimento"}</strong><time>${formatDate(item.criado_em)}</time></div><p>${escapeHtml(item.mensagem)}</p></li>`).join("") : "<li class=\"lead-contact-empty\">Nenhuma mensagem enviada pelo portal.</li>";
      if (mensagens.some(item => item.autor_tipo === "cliente" && !item.lida_em)) fetch(`/v1/admin/leads/${lead.id}/portal-mensagens/ler`, { method: "POST" }).catch(() => {});
    } catch (error) { list.innerHTML = `<li class="lead-contact-empty error">${escapeHtml(error.message)}</li>`; }
  };
  card.querySelector(".lead-portal-reply-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget; const button = form.querySelector("button"); const status = form.querySelector(".portal-reply-status");
    button.disabled = true; status.textContent = "Enviando…";
    try {
      const response = await fetch(`/v1/admin/leads/${lead.id}/portal-mensagens`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mensagem: form.elements.mensagem.value }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Não foi possível enviar a resposta.");
      form.reset(); status.textContent = "Resposta enviada."; await carregar();
    } catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
  });
  await carregar();
}

async function abrirRegistroAtendimento(leadId, pesquisaId) {
  let proximaAcao = "";
  const response = await fetch(`/v1/admin/leads/${leadId}`);
  const lead = await response.json().catch(() => ({}));
  proximaAcao = lead.proxima_acao_em ? new Date(lead.proxima_acao_em).toISOString().slice(0, 16) : "";
  if (!response.ok) { alert(lead.detail || "Não foi possível carregar o atendimento."); return; }
  document.querySelector("#lead-dialog-title").textContent = `Registrar atendimento · ${lead.nome}`;
  // Achado da auditoria de Leads/CRM (10/09/2026, Hipótese 8): este
  // formulário exige uma pesquisa vinculada (POST /leads/{id}/contatos
  // exige pesquisa_id) -- se o lead não tem nenhuma, o <select required>
  // ficava vazio e o navegador bloqueava o envio silenciosamente, sem
  // explicar nada. Em vez de montar o formulário quebrado, cai para o
  // diálogo completo de atendimento (openLead), cujo botão "Salvar
  // atendimento" já funciona para qualquer lead, com ou sem pesquisa.
  if (!(lead.pesquisas || []).length) {
    if (dialog.open) dialog.close();
    await openLead(lead.id);
    return;
  }
  const options = lead.pesquisas.map(item => `<option value="${escapeHtml(item.id)}" ${String(item.id) === String(pesquisaId) ? "selected" : ""}>${escapeHtml(item.marca)} · ${formatDate(item.criado_em, false)}</option>`).join("");
  dialogContent.innerHTML = `<section class="lead-contact-log lead-attendance-standalone"><header><div><p class="eyebrow">Atendimento comercial</p><h3>Novo registro</h3></div></header><form id="lead-contact-form" data-lead-id="${lead.id}" class="lead-contact-form"><label><span>Pesquisa relacionada</span><select name="pesquisa_id" id="lead-contact-filter" required>${options}</select></label><label><span>Canal</span><select name="canal"><option value="telefone">Telefone</option><option value="whatsapp">WhatsApp</option><option value="email">E-mail</option><option value="reuniao">Reunião</option><option value="outro">Outro</option></select></label><label><span>Resultado</span><input name="resultado" maxlength="150" placeholder="Ex.: proposta enviada" /></label><label class="lead-contact-observation-field"><span>Observação</span><textarea name="observacao" maxlength="2000" rows="5" placeholder="Registre o que foi conversado e a próxima orientação."></textarea></label><div><button class="primary-button" type="submit">Salvar atendimento</button><span id="contact-save-message" role="status"></span></div></form></section>`;
  const attendanceSection = dialogContent.querySelector(".lead-attendance-standalone");
  const attendanceForm = attendanceSection?.querySelector("#lead-contact-form");
  attendanceSection?.querySelector("header")?.insertAdjacentHTML("afterend", `<div class="lead-attendance-summary"><div><span>Telefone de contato</span><a href="tel:${escapeHtml(lead.telefone || "")}">${escapeHtml(lead.telefone || "Não informado")}</a></div><div><span>Próximo contato agendado</span><strong>${proximaAcao ? formatDate(lead.proxima_acao_em) : "Não definido"}</strong></div></div>`);
  attendanceForm?.querySelector("label:nth-child(2)")?.insertAdjacentHTML("afterend", `<label><span>Próximo contato</span><input name="proxima_acao_em" type="datetime-local" value="${proximaAcao}" /></label>`);
  if (!dialog.open) dialog.showModal();
}

async function openLead(id, selectedResearchId = null) {
  state.openLeadId = id;
  dialogContent.innerHTML = "<p>Carregando histórico…</p>";
  if (!dialog.open) dialog.showModal();
  const response = await fetch(`/v1/admin/leads/${id}`);
  if (!response.ok) { dialogContent.innerHTML = "<p class=\"status-message error\">Não foi possível abrir o contato.</p>"; return; }
  const lead = await response.json();
  const pesquisasExibidas = selectedResearchId
    ? lead.pesquisas.filter(item => String(item.id) === String(selectedResearchId))
    : lead.pesquisas;
  const researchOptions = lead.pesquisas.map(item =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.marca)} · ${formatDate(item.criado_em, false)}</option>`
  ).join("");
  document.querySelector("#lead-dialog-title").textContent = lead.nome;
  const ABAS_LEAD = [
    ["atendimento", "Atendimento Comercial"],
    ["empresa", "Empresa"],
    ["funil", "Funil do Lead"],
    ["timeline", "Linha do tempo"],
    ["documentos", "Documentos do atendimento"],
    ["guias", "Guias do INPI"],
    ["propostas", "Proposta de registro"],
  ];
  dialogContent.innerHTML = `
    <section class="lead-contact-summary"><div><span>E-mail</span><a href="mailto:${escapeHtml(lead.email)}">${escapeHtml(lead.email)}</a>${state.canManage && lead.email ? ` <button type="button" class="secondary-button lead-send-email" data-lead-id="${lead.id}">Enviar e-mail</button>` : ""}</div><div><span>Telefone</span><a href="tel:${escapeHtml(lead.telefone)}">${escapeHtml(lead.telefone)}</a></div><div><span>CPF/CNPJ</span><strong>${escapeHtml(lead.documento || "Não informado")}</strong></div><div><span>Empresa</span><strong>${escapeHtml(lead.empresa || "Não informada")}</strong></div><div><span>Marketing</span><strong>${lead.aceite_marketing ? "Autorizado" : "Não autorizado"}</strong></div><div><span>Score</span><strong id="lead-score-badge">Calculando…</strong></div><div><span>Prioridade (IA)</span><strong id="lead-qualificacao-badge">—</strong></div></section>
    <nav class="lead-tabs" role="tablist">${ABAS_LEAD.map(([id, label], i) => `<button type="button" class="lead-tab${i === 0 ? " active" : ""}" role="tab" aria-selected="${i === 0}" data-tab="${id}">${label}</button>`).join("")}</nav>
    <div class="lead-tab-panel" data-panel="atendimento">
      <section class="lead-qualificacao-ia lg-full" id="lead-qualificacao-ia" hidden></section>
      <section class="lead-sugestao-ia lg-full" id="lead-sugestao-ia" hidden></section>
      <section class="lead-history lg-full"><header><div><p class="eyebrow">${selectedResearchId ? "Pesquisa selecionada" : "Histórico"}</p><h3>${pesquisasExibidas.length} pesquisa${pesquisasExibidas.length === 1 ? "" : "s"}</h3></div></header>${pesquisasExibidas.length ? pesquisasExibidas.map(researchCard).join("") : "<p>Nenhuma pesquisa vinculada.</p>"}</section>
      <section class="lead-relacionados lg-full" id="lead-relacionados" hidden></section>
      <section class="lead-cadencia-historico lg-full" id="lead-cadencia-historico" hidden></section>
      <section class="lead-cadencia lg-full" id="lead-cadencia" hidden></section>
      ${state.canManage ? `<form id="lead-crm-form" data-lead-id="${lead.id}" class="lead-crm-form">
        <label><span>Status</span><select name="status">${statusOptions(lead.status)}</select></label>
        <div class="lead-motivo-perda" id="lead-motivo-perda"${lead.status === "descartado" ? "" : " hidden"}>
          <label><span>Motivo da perda</span><select name="motivo_perda">${motivoPerdaOptions(lead.motivo_perda)}</select></label>
          <label><span>Detalhe (opcional)</span><input name="motivo_perda_detalhe" maxlength="500" value="${escapeHtml(lead.motivo_perda_detalhe || "")}" placeholder="Ex.: fechou com concorrente X" /></label>
        </div>
        <label><span>Responsável</span><select name="responsavel_id">${ownerOptions(lead.responsavel_id)}</select></label>
        <label><span>Próxima ação</span><input name="proxima_acao_em" type="datetime-local" value="${lead.proxima_acao_em ? new Date(lead.proxima_acao_em).toISOString().slice(0, 16) : ""}" /></label>
        <label><span>Tags, separadas por vírgula</span><input name="tags" maxlength="400" value="${escapeHtml((lead.tags || []).join(", "))}" /></label>
        ${state.canPii ? `<label><span>CPF/CNPJ</span><input name="documento" inputmode="numeric" maxlength="18" value="${escapeHtml(lead.documento || "")}" placeholder="Somente para cadastro interno" /></label>` : ""}
        <label class="lead-notes"><span>Anotações internas</span><textarea name="notas" maxlength="4000" rows="5">${escapeHtml(lead.notas || "")}</textarea></label>
        <div><button class="primary-button" type="submit">Salvar atendimento</button><a class="secondary-button" href="/admin/crm?lead_id=${lead.id}">Criar lembrete</a><span id="lead-save-message" role="status"></span></div>
      </form>` : `<section class="lead-readonly-note">Você possui acesso somente para consulta.</section>`}
      <section class="lead-contact-log">
        <header><div><p class="eyebrow">CRM</p><h3>Contatos realizados</h3></div><span id="lead-contact-count">0 registros</span></header>
        <label class="lead-contact-filter"><span>Filtrar pela pesquisa</span><select id="lead-contact-filter"><option value="">Todas as pesquisas desta empresa</option>${researchOptions}</select></label>
        ${state.canManage && lead.pesquisas.length ? `<form id="lead-contact-form" data-lead-id="${lead.id}" class="lead-contact-form">
          <label><span>Pesquisa relacionada</span><select name="pesquisa_id" required><option value="">Selecione a pesquisa</option>${researchOptions}</select></label>
          <label><span>Canal</span><select name="canal"><option value="telefone">Telefone</option><option value="whatsapp">WhatsApp</option><option value="email">E-mail</option><option value="reuniao">Reunião</option><option value="outro">Outro</option></select></label>
          <label><span>Resultado</span><input name="resultado" maxlength="150" placeholder="Ex.: proposta enviada" /></label>
          <label class="lead-contact-observation-field"><span>Observações do contato</span><textarea name="observacao" maxlength="2000" rows="3" placeholder="Registre o que foi conversado e a próxima orientação."></textarea></label>
          <div><button class="primary-button" type="submit">Registrar contato</button><span id="contact-save-message" role="status"></span></div>
        </form>` : state.canManage ? `<p class="lead-contact-empty">Este lead ainda não tem pesquisa de marca vinculada, então não dá pra escolher a qual pesquisa o contato se refere. Use "Salvar atendimento" acima para registrar o atendimento mesmo assim.</p>` : ""}
        <ol id="lead-contact-history" class="lead-contact-history"><li class="lead-contact-empty">Carregando contatos...</li></ol>
      </section>
      <section class="lead-horas" id="lead-horas" data-lead-id="${lead.id}"><p class="lead-funil-loading">Carregando horas…</p></section>
    </div>
    <div class="lead-tab-panel" data-panel="empresa" hidden>
      ${lead.empresa_id ? `<section class="lead-empresa lg-full" id="lead-empresa" data-empresa-id="${lead.empresa_id}" data-contato-id="${lead.contato_id || ""}"><p class="lead-funil-loading">Carregando empresa…</p></section>` : `<p class="lead-empresa-vazia">Este lead ainda não está vinculado a uma empresa.</p>`}
    </div>
    <div class="lead-tab-panel" data-panel="funil" hidden>
      <section class="lead-funil lg-full" id="lead-funil"><p class="lead-funil-loading">Carregando funil…</p></section>
      <section class="lead-checklist" id="lead-checklist"><p class="lead-funil-loading">Carregando checklist…</p></section>
    </div>
    <div class="lead-tab-panel" data-panel="timeline" hidden>
      <section class="lead-timeline" id="lead-timeline"><p class="lead-funil-loading">Carregando linha do tempo…</p></section>
    </div>
    <div class="lead-tab-panel" data-panel="documentos" hidden>
      <section class="lead-documentos lg-full" id="lead-documentos"><p class="lead-funil-loading">Carregando documentos…</p></section>
    </div>
    <div class="lead-tab-panel" data-panel="guias" hidden>
      <section class="lead-guias" id="lead-guias"><p class="lead-funil-loading">Carregando guias do INPI…</p></section>
    </div>
    <div class="lead-tab-panel" data-panel="propostas" hidden>
      <section class="lead-propostas" id="lead-propostas"><p class="lead-funil-loading">Carregando propostas…</p></section>
    </div>`;
  dialogContent.querySelectorAll(".lead-tab").forEach(botao => botao.addEventListener("click", () => {
    dialogContent.querySelectorAll(".lead-tab").forEach(b => { const ativa = b === botao; b.classList.toggle("active", ativa); b.setAttribute("aria-selected", String(ativa)); });
    dialogContent.querySelectorAll(".lead-tab-panel").forEach(p => { p.hidden = p.dataset.panel !== botao.dataset.tab; });
  }));
  const sendEmailButton = dialogContent.querySelector(".lead-send-email");
  if (sendEmailButton) sendEmailButton.addEventListener("click", async () => {
    if (!confirm(`Enviar o e-mail comercial padrão para ${lead.email}?`)) return;
    const original = sendEmailButton.textContent;
    sendEmailButton.disabled = true;
    sendEmailButton.textContent = "Enviando…";
    try {
      await responsePayload(await fetch(`/v1/admin/leads/${lead.id}/enviar-email-prospeccao`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }));
      showMessage("E-mail enviado e registrado no histórico do lead.", "success");
      await openLead(lead.id, selectedResearchId);
    } catch (error) {
      sendEmailButton.disabled = false;
      sendEmailButton.textContent = original;
      showMessage(error.message || "Não foi possível enviar o e-mail.", "error");
    }
  });
  if (state.canManage) {
    const portalCard = document.createElement("section");
    portalCard.className = "lead-portal-access";
    portalCard.innerHTML = "<strong>Portal do cliente</strong><p>Verificando acesso…</p>";
    dialogContent.prepend(portalCard);
    fetch(`/v1/admin/leads/${lead.id}/portal-acesso`).then(async r => { const body = await r.json().catch(() => ({})); if (!r.ok) { const erro = new Error(body.detail || "Não foi possível verificar o acesso."); erro.status = r.status; throw erro; } return body; }).then(access => {
      portalCard.innerHTML = access.existe
        ? `<strong>Portal do cliente</strong><p>${access.ativo ? "Acesso ativo." : "Acesso bloqueado."}</p><div class="portal-credentials"><label>Usuário <input readonly value="${escapeHtml(access.cliente?.email || lead.email || "")}" /></label></div><a class="secondary-button" href="/portal" target="_blank" rel="noopener">Abrir portal do cliente</a><button type="button" class="primary-button" data-portal-generate>Gerar nova senha temporária</button>`
        : `<strong>Portal do cliente</strong><p>Este cliente ainda não possui acesso.</p><button type="button" class="primary-button" data-portal-generate>Gerar acesso do cliente</button>`;
      const generateButton = portalCard.querySelector("[data-portal-generate]");
      if (generateButton) generateButton.addEventListener("click", async () => {
        const actionLabel = generateButton.textContent;
        generateButton.disabled = true;
        generateButton.textContent = "Gerando…";
        try {
          const response = await fetch(`/v1/admin/leads/${lead.id}/portal-acesso`, { method: "POST", headers: { "Content-Type": "application/json" } });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw Object.assign(new Error(result.detail || "Não foi possível gerar o acesso."), { status: response.status });
          const portalPath = result.portal || "/portal";
          const portalUrl = `${window.location.origin}${portalPath}`;
          portalCard.innerHTML = `<strong>Portal do cliente</strong><p>Acesso gerado. Envie ao cliente o link e a senha temporária.</p><div class="portal-credentials"><label>Link <input readonly value="${escapeHtml(portalUrl)}" /></label><label>Usuário <input readonly value="${escapeHtml(result.cliente?.email || lead.email || "")}" /></label><label>Senha temporária <input readonly value="${escapeHtml(result.senha_temporaria || "")}" /></label></div><a class="secondary-button" href="${escapeHtml(portalPath)}" target="_blank" rel="noopener">Abrir portal do cliente</a>`;
        } catch (error) {
          generateButton.disabled = false;
          generateButton.textContent = actionLabel;
          const mensagem = error.status === 403 ? "Sem permissão para gerar este acesso." : error.status === 401 ? "Sua sessão expirou. Atualize a página e entre novamente." : (error.message || "Não foi possível gerar o acesso.");
          const aviso = portalCard.querySelector("p");
          if (aviso) aviso.textContent = mensagem;
        }
      });
    }).catch(error => { const mensagem = error.status === 403 ? "Sem permissão ou este lead não está sob sua responsabilidade." : error.status === 401 ? "Sua sessão expirou. Atualize a página e entre novamente." : (error.message || "Não foi possível verificar o acesso."); portalCard.innerHTML = `<strong>Portal do cliente</strong><p>${mensagem}</p>`; });
    renderPortalMessages(lead);
  }
  // Item 30 da auditoria completa do CRM (06/09/2026): score calculado sob
  // demanda (endpoint dedicado, não vem no payload do lead) -- busca à
  // parte, sem travar a abertura do modal se demorar ou falhar.
  fetch(`/v1/admin/leads/${lead.id}/score`).then(r => r.ok ? r.json() : Promise.reject())
    .then(dadosScore => {
      const badge = document.querySelector("#lead-score-badge");
      if (!badge) return;
      badge.textContent = `${dadosScore.score}/100`;
      const dias = dadosScore.dias_sem_interacao;
      badge.title = dias === null
        ? "Sem nenhuma interação registrada ainda"
        : `${dias} dia${dias === 1 ? "" : "s"} sem interação · fator de decaimento ${(dadosScore.fator_decaimento * 100).toFixed(0)}%`;
    })
    .catch(() => { const badge = document.querySelector("#lead-score-badge"); if (badge) badge.textContent = "Indisponível"; });
  renderSugestaoIA(lead.id);
  renderQualificacaoIA(lead.id);
  await renderEmpresa(lead);
  await renderCadenciaLead(lead);
  await loadLeadContacts(lead.id);
  await renderTimeline(lead.id);
  await renderFunil(lead.id);
  await renderRelacionados(lead.id);
  await renderDocumentos(lead.id);
  await renderChecklistFase(lead.id);
  await renderGuiasInpi(lead.id);
  await renderPropostas(lead);
  await renderHoras(lead.id);
}

// Criação direta: mantém o botão sem prompts e usa os dados já disponíveis.
// Achado CRM-11 da auditoria (04/09/2026): funil expandido de 7 para 10
// fases -- mantido em sincronia com FASE_LABELS/KANBAN_ETAPAS em
// app/api/leads.py.
const FASE_LABELS = {
  contato_inicial: "Contato inicial",
  qualificado: "Qualificado",
  relatorio_enviado: "Relatório enviado",
  proposta_enviada: "Proposta enviada",
  proposta_aceita: "Proposta aceita",
  aguardando_pagamento: "Aguardando pagamento",
  pagamento_confirmado: "Pagamento confirmado",
  ganho: "Ganho",
  protocolo_inpi: "Protocolo INPI",
  processo_inpi: "Processo no INPI",
};

// Rotulos das etapas do Kanban de CRM (deve espelhar KANBAN_ETAPAS em app/api/leads.py)
// -- a fase sozinha nao distingue "Primeiro contato" de "Aguardando contato nosso"
// nem "Relatorio enviado" de "Aguardando retorno do cliente"; por isso o status
// tambem entra no calculo, senao a pagina do lead mostra uma etapa diferente da
// que aparece no board do CRM para o mesmo lead.
const ETAPA_KANBAN_LABELS = {
  primeiro_contato: "Primeiro contato",
  aguardando_contato_nosso: "Aguardando contato nosso",
  qualificado: "Qualificado",
  aguardando_retorno_cliente: "Aguardando retorno do cliente",
  proposta_enviada: "Proposta enviada",
  proposta_aceita: "Proposta aceita",
  aguardando_pagamento: "Aguardando pagamento",
  pagamento_confirmado: "Pagamento confirmado",
  ganho: "Ganho",
  protocolo_inpi: "Protocolo no INPI gerado",
  processo_inpi: "Processo no INPI",
  perdidos: "Perdidos",
};

function etapaKanbanLead(lead) {
  if (lead.status === "descartado") return "perdidos";
  if (lead.fase === "contato_inicial") return lead.status === "em_contato" ? "aguardando_contato_nosso" : "primeiro_contato";
  if (lead.fase === "relatorio_enviado" && lead.status === "sem_retorno") return "aguardando_retorno_cliente";
  return ETAPA_KANBAN_LABELS[lead.fase] ? lead.fase : "primeiro_contato";
}

function faseMini(lead) {
  const ordem = Object.keys(FASE_LABELS);
  const idx = ordem.indexOf(lead.fase);
  const dots = ordem.map((f, i) => {
    const cls = i < idx ? "done" : i === idx ? "current" : "pending";
    return `<span class="fm-dot ${cls}" title="${escapeHtml(FASE_LABELS[f] || f)}"></span>`;
  }).join("");
  const label = ETAPA_KANBAN_LABELS[etapaKanbanLead(lead)] || FASE_LABELS[lead.fase] || "Contato inicial";
  return `<div class="fase-mini" title="Etapa do lead: ${escapeHtml(label)}"><span class="fase-mini-dots">${dots}</span><span class="fase-mini-label">${escapeHtml(label)}</span></div>`;
}

const DOC_LABELS = { procuracao: "Procuração", gru: "GRU", protocolo: "Protocolo", oposicao: "Oposição", certificado: "Certificado" };
const DOC_STATUS = [["pendente", "Pendente"], ["em_andamento", "Em andamento"], ["concluido", "Concluído"], ["nao_aplicavel", "N/A"]];

// Achado do usuário (08/09/2026): o motor de cadência já registra status,
// abertura e resposta de cada e-mail (EnvioCadenciaEmail), mas nenhuma tela
// mostrava isso pro operador -- os dados existiam, só ficavam invisíveis.
const STATUS_ENVIO_CADENCIA_LABELS = { pendente: "Agendado", enviado: "Enviado", falhou: "Falhou", pausado: "Pausado" };

function envioCadenciaRow(item) {
  const statusLabel = STATUS_ENVIO_CADENCIA_LABELS[item.status] || item.status;
  const sinais = [];
  if (item.enviado_em) sinais.push(`Enviado: ${formatDate(item.enviado_em)}`);
  if (item.aberto_em) sinais.push(`Aberto: ${formatDate(item.aberto_em)}`);
  if (item.respondido_em) sinais.push(`Respondido: ${formatDate(item.respondido_em)}`);
  if (item.status === "pendente") sinais.push(`Agendado para: ${formatDate(item.agendado_para)}`);
  if (item.status === "falhou" && item.ultimo_erro) sinais.push(`Erro: ${escapeHtml(item.ultimo_erro)}`);
  return `<li class="lead-cad-envio-item status-${escapeHtml(item.status)}">
    <div><strong>${escapeHtml(item.cadencia_nome)}</strong> · ${escapeHtml(item.passo_titulo)} <small>(dia ${item.passo_dia})</small></div>
    <span class="lead-cad-envio-status">${escapeHtml(statusLabel)}</span>
    <small>${sinais.join(" · ") || "—"}</small>
  </li>`;
}

async function renderHistoricoCadenciaLead(lead) {
  const box = document.querySelector("#lead-cadencia-historico");
  if (!box) return;
  let itens;
  try { itens = (await (await fetch(`/v1/admin/leads/${lead.id}/envios-cadencia`)).json()).itens || []; }
  catch { box.hidden = true; return; }
  if (!itens.length) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<header><p class="eyebrow">Cadência</p><h3>Histórico de envios</h3></header><ul class="lead-cad-envios">${itens.map(envioCadenciaRow).join("")}</ul>`;
}

async function renderCadenciaLead(lead) {
  await renderHistoricoCadenciaLead(lead);
  const box = document.querySelector("#lead-cadencia");
  if (!box || !state.canManage) return;
  let cads;
  try { cads = (((await (await fetch("/v1/admin/crm/cadencias")).json()).itens) || []).filter(c => c.ativo); }
  catch { box.hidden = true; return; }
  if (!cads.length) { box.hidden = true; return; }
  box.hidden = false;
  const opts = cads.map(c => `<option value="${c.id}">${escapeHtml(c.nome)} (${c.passos.length} passo${c.passos.length === 1 ? "" : "s"})</option>`).join("");
  box.innerHTML = `<header><p class="eyebrow">Cadência</p><h3>Aplicar sequência de atendimento</h3></header><div class="lead-cad-apply"><select id="lead-cad-select">${opts}</select><button class="secondary-button" id="lead-cad-apply-btn" type="button">Aplicar</button><span class="lead-cad-msg" role="status"></span></div>`;
  box.querySelector("#lead-cad-apply-btn").addEventListener("click", async () => {
    const cadenciaId = Number(box.querySelector("#lead-cad-select").value);
    const btn = box.querySelector("#lead-cad-apply-btn");
    btn.disabled = true;
    const r = await fetch(`/v1/admin/leads/${lead.id}/aplicar-cadencia`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cadencia_id: cadenciaId }) });
    const msg = box.querySelector(".lead-cad-msg");
    btn.disabled = false;
    if (r.ok) { const d = await r.json(); if (msg) msg.textContent = `${d.criados} tarefa(s) agendada(s).`; await renderHistoricoCadenciaLead(lead); }
    else if (msg) msg.textContent = "Erro ao aplicar.";
  });
}

async function renderEmpresa(lead) {
  const box = document.querySelector("#lead-empresa");
  if (!box) return;
  const empresaId = box.dataset.empresaId;
  const contatoVinculado = box.dataset.contatoId;
  let data;
  try { data = await (await fetch(`/v1/admin/crm/empresas/${empresaId}`)).json(); }
  catch { box.innerHTML = ""; return; }
  const canManage = state.canManage;
  const campo = (label, name, value, type = "text") => `<label><span>${escapeHtml(label)}</span><input name="${name}" type="${type}" value="${escapeHtml(value || "")}" ${canManage ? "" : "readonly"} maxlength="200"></label>`;
  const contatos = (data.contatos || []).map(c => {
    const tags = [c.principal ? `<span class="emp-tag">principal</span>` : "", String(c.id) === contatoVinculado ? `<span class="emp-tag vinc">nesta oportunidade</span>` : ""].join("");
    const meta = [c.cargo, c.email, c.telefone].filter(Boolean).map(escapeHtml).join(" · ");
    const vincular = (canManage && String(c.id) !== contatoVinculado) ? `<button class="emp-contato-vinc" data-id="${c.id}" type="button" title="Usar este contato nesta oportunidade">Vincular</button>` : "";
    const acts = canManage ? `<div class="emp-contato-acts">${vincular}<button class="emp-contato-edit" data-id="${c.id}" type="button">Editar</button><button class="emp-contato-del" data-id="${c.id}" type="button" aria-label="Remover">×</button></div>` : "";
    return `<li class="emp-contato" data-id="${c.id}"><div><strong>${escapeHtml(c.nome)}</strong> ${tags}<br><small>${meta || "—"}</small></div>${acts}</li>`;
  }).join("");
  box.innerHTML = `<header><p class="eyebrow">Empresa</p><h3>${escapeHtml(data.nome)}</h3></header>
    <form class="emp-form">
      <div class="emp-grid">
        ${campo("Documento (CNPJ/CPF)", "documento", data.documento)}
        ${campo("Segmento", "segmento", data.segmento)}
        ${campo("Telefone", "telefone", data.telefone)}
        ${campo("E-mail", "email", data.email, "email")}
        ${campo("Site", "site", data.site)}
      </div>
      ${canManage ? `<div class="emp-form-acts"><button class="secondary-button" type="submit">Salvar empresa</button><span class="emp-msg" role="status"></span></div>` : ""}
    </form>
    <div class="emp-contatos">
      <div class="emp-contatos-head"><p class="eyebrow">Contatos</p>${canManage ? `<button class="secondary-button emp-add" type="button">Adicionar contato</button>` : ""}</div>
      <ul class="emp-contato-list">${contatos || `<li class="emp-empty">Nenhum contato cadastrado.</li>`}</ul>
      ${canManage ? `<form class="emp-contato-form" hidden autocomplete="off">
        <input type="hidden" name="id">
        <div class="emp-grid">
          <label><span>Nome</span><input name="nome" maxlength="150" required></label>
          <label><span>Cargo</span><input name="cargo" maxlength="80"></label>
          <label><span>E-mail</span><input name="email" type="email" maxlength="254"></label>
          <label><span>Telefone</span><input name="telefone" maxlength="30"></label>
        </div>
        <label class="emp-check"><input type="checkbox" name="principal"><span>Contato principal</span></label>
        <div class="emp-form-acts"><button class="secondary-button" type="submit">Salvar contato</button><button class="secondary-button emp-contato-cancel" type="button">Cancelar</button></div>
      </form>` : ""}
    </div>
    <div class="emp-relacionamento">
      <p class="eyebrow">Todas as oportunidades desta empresa (${(data.leads || []).length})</p>
      <ul class="emp-lead-list">${(data.leads || []).map(item => `
        <li class="emp-lead-item${String(item.id) === String(lead.id) ? " emp-lead-atual" : ""}">
          <a href="#" class="emp-lead-open" data-id="${item.id}"><strong>${escapeHtml(item.marca || "Interesse geral")}</strong></a>
          <small>${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(FASE_LABELS[item.fase] || item.fase)} · ${escapeHtml(item.responsavel_nome || "Sem responsável")}${item.proxima_acao_em ? "" : " · sem próxima ação"}</small>
        </li>`).join("") || `<li class="emp-empty">Nenhuma outra oportunidade.</li>`}
      </ul>
      <p class="eyebrow">Processos monitorados desta empresa (${(data.processos || []).length})</p>
      <ul class="emp-processo-list">${(data.processos || []).map(item => `
        <li class="emp-processo-item">
          <a href="/processos/${encodeURIComponent(item.numero)}" target="_blank" rel="noopener"><strong>${escapeHtml(item.numero)}</strong></a>
          <small>${escapeHtml(item.titulo || "Título não informado pelo INPI")} · ${escapeHtml(item.situacao || "Situação não informada")}${item.lead_id ? "" : " · sem lead de origem vinculado"}</small>
        </li>`).join("") || `<li class="emp-empty">Nenhum processo monitorado.</li>`}
      </ul>
    </div>`;
  box.querySelectorAll(".emp-lead-open").forEach(a => a.addEventListener("click", event => {
    event.preventDefault();
    openLead(Number(a.dataset.id));
  }));
  if (!canManage) return;
  box.querySelector(".emp-form").addEventListener("submit", async e => {
    e.preventDefault();
    const f = e.target;
    const payload = {};
    ["documento", "segmento", "telefone", "email", "site"].forEach(k => { payload[k] = f.elements[k].value || null; });
    const r = await fetch(`/v1/admin/crm/empresas/${empresaId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const m = box.querySelector(".emp-msg");
    if (m) m.textContent = r.ok ? "Empresa salva." : "Erro ao salvar.";
  });
  const cform = box.querySelector(".emp-contato-form");
  const openC = (c) => {
    cform.reset(); cform.hidden = false; cform.elements.id.value = c?.id || "";
    if (c) { cform.elements.nome.value = c.nome || ""; cform.elements.cargo.value = c.cargo || ""; cform.elements.email.value = c.email || ""; cform.elements.telefone.value = c.telefone || ""; cform.elements.principal.checked = !!c.principal; }
    cform.elements.nome.focus();
  };
  box.querySelector(".emp-add")?.addEventListener("click", () => openC());
  box.querySelector(".emp-contato-cancel")?.addEventListener("click", () => { cform.hidden = true; });
  box.querySelectorAll(".emp-contato-edit").forEach(b => b.addEventListener("click", () => openC((data.contatos || []).find(c => String(c.id) === b.dataset.id))));
  box.querySelectorAll(".emp-contato-del").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(`/v1/admin/crm/contatos/${b.dataset.id}`, { method: "DELETE" });
    if (r.ok || r.status === 204) await renderEmpresa(lead);
  }));
  box.querySelectorAll(".emp-contato-vinc").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(`/v1/admin/leads/${lead.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ contato_id: Number(b.dataset.id) }) });
    if (r.ok) { box.dataset.contatoId = b.dataset.id; lead.contato_id = Number(b.dataset.id); await renderEmpresa(lead); }
  }));
  cform.addEventListener("submit", async e => {
    e.preventDefault();
    const id = cform.elements.id.value;
    const payload = { nome: cform.elements.nome.value.trim(), cargo: cform.elements.cargo.value || null, email: cform.elements.email.value || null, telefone: cform.elements.telefone.value || null, principal: cform.elements.principal.checked, observacoes: null };
    const url = id ? `/v1/admin/crm/contatos/${id}` : `/v1/admin/crm/empresas/${empresaId}/contatos`;
    const r = await fetch(url, { method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (r.ok) await renderEmpresa(lead);
  });
}

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

const TIMELINE_ICONES = { criado: "✦", fase: "→", contato: "☎", pesquisa: "🔍", documento: "📄", guia: "R$", guia_paga: "✓", ganho: "🏆", perdido: "✕" };
async function renderTimeline(leadId) {
  const box = document.querySelector("#lead-timeline");
  if (!box) return;
  let data;
  try { data = await (await fetch(`/v1/admin/leads/${leadId}/timeline`)).json(); }
  catch { box.innerHTML = ""; return; }
  const eventos = data.eventos || [];
  const linhas = eventos.map(ev => {
    const icone = TIMELINE_ICONES[ev.tipo] || "•";
    const partes = [ev.detalhe, ev.autor].filter(Boolean).map(escapeHtml).join(" · ");
    const detalhe = partes ? `<span class="tl-detalhe">${partes}</span>` : "";
    return `<li class="tl-item tl-${escapeHtml(ev.tipo)}"><span class="tl-icone" aria-hidden="true">${icone}</span><div class="tl-corpo"><div class="tl-topo"><strong>${escapeHtml(ev.titulo)}</strong><time>${formatDate(ev.data)}</time></div>${detalhe}</div></li>`;
  }).join("");
  box.innerHTML = `<header><p class="eyebrow">Linha do tempo</p><h3>Atividade da oportunidade</h3></header>${eventos.length ? `<ul class="tl-list">${linhas}</ul>` : `<p class="tl-empty">Sem eventos ainda.</p>`}`;
}

// Achado FASE-A da auditoria do CRM (05/09/2026): nao havia nenhum controle
// de horas trabalhadas -- apontamento manual por lead, sem calculo de valor.
async function renderHoras(leadId) {
  const box = document.querySelector("#lead-horas");
  if (!box) return;
  let data;
  try { data = await (await fetch(`/v1/admin/horas?lead_id=${leadId}`)).json(); }
  catch { box.innerHTML = ""; return; }
  const itens = data.itens || [];
  const linhas = itens.map(item => `<li class="lead-hora-item" data-id="${item.id}"><div><strong>${Number(item.horas).toLocaleString("pt-BR", { minimumFractionDigits: 2 })}h</strong><span>${formatDate(item.data, false)} · ${escapeHtml(item.usuario_nome || "—")}${item.faturavel ? "" : " · não faturável"}</span></div><p>${escapeHtml(item.descricao)}</p>${state.canManage ? `<button class="hora-del" data-id="${item.id}" type="button" title="Excluir" aria-label="Excluir">×</button>` : ""}</li>`).join("");
  box.innerHTML = `<header><div><p class="eyebrow">Horas</p><h3>Apontamento de horas</h3></div><span>${data.total_horas || "0"}h · ${data.total_horas_faturaveis || "0"}h faturáveis</span></header>
    ${state.canManage ? `<form id="lead-horas-form" class="lead-horas-form"><label><span>Data</span><input name="data" type="date" required value="${new Date().toISOString().slice(0, 10)}" /></label><label><span>Horas</span><input name="horas" type="number" min="0.25" max="24" step="0.25" required /></label><label class="lead-horas-check"><input name="faturavel" type="checkbox" checked /><span>Faturável</span></label><label class="lead-horas-desc"><span>Descrição</span><input name="descricao" maxlength="500" minlength="3" placeholder="Ex.: análise de viabilidade da marca" required /></label><button class="secondary-button" type="submit">Lançar</button></form>` : ""}
    ${itens.length ? `<ul class="lead-horas-list">${linhas}</ul>` : `<p class="lead-horas-empty">Nenhuma hora lançada.</p>`}`;
  const form = box.querySelector("#lead-horas-form");
  if (form) form.addEventListener("submit", async event => {
    event.preventDefault();
    const dados = Object.fromEntries(new FormData(form));
    try {
      const response = await fetch("/v1/admin/horas", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lead_id: Number(leadId), data: dados.data, horas: Number(dados.horas), descricao: dados.descricao, faturavel: form.elements.faturavel.checked }) });
      if (!response.ok) { const erro = await response.json().catch(() => ({})); alert(erro.detail || "Não foi possível lançar as horas."); return; }
      await renderHoras(leadId);
    } catch { alert("Não foi possível lançar as horas."); }
  });
  box.querySelectorAll(".hora-del").forEach(btn => btn.addEventListener("click", async () => {
    if (!confirm("Excluir este apontamento de horas?")) return;
    const response = await fetch(`/v1/admin/horas/${btn.dataset.id}`, { method: "DELETE" });
    if (response.ok || response.status === 204) await renderHoras(leadId);
    else alert("Não foi possível excluir.");
  }));
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

// IA em sombra na captação: prioridade + observação sugeridas uma única
// vez, no momento em que o lead chegou -- gerada em segundo plano (job sob
// demanda), pode ainda não existir (a chamada é assíncrona) ou nunca
// existir (lead antigo, de antes da funcionalidade). Some nesses casos.
const PRIORIDADE_LABEL_IA = { alta: "Alta", media: "Média", baixa: "Baixa" };
async function renderQualificacaoIA(leadId) {
  const box = document.querySelector("#lead-qualificacao-ia");
  const badge = document.querySelector("#lead-qualificacao-badge");
  if (!box) return;
  let qualificacao;
  try {
    const r = await fetch(`/v1/admin/leads/${leadId}/qualificacao-ia`);
    if (!r.ok) { box.hidden = true; if (badge) badge.textContent = "—"; return; }
    qualificacao = await r.json();
  } catch { box.hidden = true; if (badge) badge.textContent = "—"; return; }
  if (badge) badge.textContent = PRIORIDADE_LABEL_IA[qualificacao.prioridade] || "—";
  if (qualificacao.erro || qualificacao.status !== "pendente") { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<header><p class="eyebrow">Qualificação da IA na captação · revisão necessária</p><h3>Prioridade sugerida: ${escapeHtml(PRIORIDADE_LABEL_IA[qualificacao.prioridade] || qualificacao.prioridade)}</h3></header>
    <p class="lead-sugestao-ia-resumo">${escapeHtml(qualificacao.observacao)}</p>
    <p class="lead-sugestao-ia-aviso">Sugerida a partir só dos dados de captação (ainda sem histórico de contato) -- nunca decide sozinha quem é prioridade.</p>
    <div class="lead-sugestao-ia-acoes">
      <button type="button" class="secondary-button" data-revisar-qualificacao="descartada">Descartar</button>
      <button type="button" class="primary-button" data-revisar-qualificacao="aprovada">Marcar como revisada</button>
    </div>`;
  box.querySelectorAll("[data-revisar-qualificacao]").forEach(botao => botao.addEventListener("click", async () => {
    box.querySelectorAll("button").forEach(b => { b.disabled = true; });
    try {
      await fetch(`/v1/admin/leads/${leadId}/qualificacao-ia/${qualificacao.id}/revisar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: botao.dataset.revisarQualificacao }),
      });
      box.hidden = true;
    } catch {
      box.querySelectorAll("button").forEach(b => { b.disabled = false; });
    }
  }));
}

// Item "IA em sombra" (fora do roteiro da auditoria completa do CRM):
// resumo + sugestão de próxima ação gerados em segundo plano (worker),
// nunca em tempo real -- se ainda não existe sugestão para o lead, a
// seção some, sem tentar gerar uma na hora.
async function renderSugestaoIA(leadId) {
  const box = document.querySelector("#lead-sugestao-ia");
  if (!box) return;
  let sugestao;
  try {
    const r = await fetch(`/v1/admin/leads/${leadId}/sugestao-ia`);
    if (!r.ok) { box.hidden = true; return; }
    sugestao = await r.json();
  } catch { box.hidden = true; return; }
  if (sugestao.erro || sugestao.status !== "pendente") { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<header><p class="eyebrow">Sugestão da IA · revisão necessária</p><h3>Gerada em ${formatDate(sugestao.gerado_em, false)}</h3></header>
    <p class="lead-sugestao-ia-resumo">${escapeHtml(sugestao.resumo)}</p>
    ${sugestao.sugestao_proxima_acao ? `<p class="lead-sugestao-ia-acao"><strong>Sugestão de próxima ação:</strong> ${escapeHtml(sugestao.sugestao_proxima_acao)}</p>` : ""}
    <p class="lead-sugestao-ia-aviso">Conteúdo gerado por IA, sem revisão humana ainda -- nunca foi enviado a ninguém.</p>
    <div class="lead-sugestao-ia-acoes">
      <button type="button" class="secondary-button" data-revisar="descartada">Descartar</button>
      <button type="button" class="primary-button" data-revisar="aprovada">Marcar como revisada</button>
    </div>`;
  box.querySelectorAll("[data-revisar]").forEach(botao => botao.addEventListener("click", async () => {
    botao.closest(".lead-sugestao-ia-acoes").querySelectorAll("button").forEach(b => { b.disabled = true; });
    try {
      await fetch(`/v1/admin/leads/${leadId}/sugestao-ia/${sugestao.id}/revisar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: botao.dataset.revisar }),
      });
      box.hidden = true;
    } catch {
      botao.closest(".lead-sugestao-ia-acoes").querySelectorAll("button").forEach(b => { b.disabled = false; });
    }
  }));
}

async function renderRelacionados(leadId) {
  const box = document.querySelector("#lead-relacionados");
  if (!box) return;
  let data;
  try { data = await (await fetch(`/v1/admin/leads/${leadId}/relacionados`)).json(); }
  catch { box.hidden = true; return; }
  if (!data.itens || !data.itens.length) { box.hidden = true; return; }
  box.hidden = false;
  const linhas = data.itens.map(item => `<li><a href="/admin/pesquisas?lead_id=${item.id}">${escapeHtml(item.marca || "Sem marca")}</a><span class="lead-relacionado-status">${escapeHtml(item.status)}</span><time>${formatDate(item.criado_em, false)}</time></li>`).join("");
  box.innerHTML = `<header><p class="eyebrow">Mesmo contato</p><h3>${data.total} outra${data.total === 1 ? "" : "s"} oportunidade${data.total === 1 ? "" : "s"}</h3></header><ul class="lead-relacionados-lista">${linhas}</ul>`;
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

function openResearchMove(researchId, marcaAtual) {
  state.moveResearchId = researchId;
  researchMoveForm.reset();
  document.querySelector("#research-move-description").textContent = `Pesquisa de "${marcaAtual}" — escolha para qual cliente ela pertence de verdade.`;
  document.querySelector("#research-move-lead-id-field").hidden = false;
  document.querySelector("#research-move-novo-fields").hidden = true;
  document.querySelector("#research-move-message").hidden = true;
  researchMoveDialog.showModal();
}

researchMoveForm.querySelectorAll('input[name="destino"]').forEach(radio => {
  radio.addEventListener("change", () => {
    const novo = researchMoveForm.destino.value === "novo";
    document.querySelector("#research-move-lead-id-field").hidden = novo;
    document.querySelector("#research-move-novo-fields").hidden = !novo;
  });
});

document.querySelector("#cancel-research-move").addEventListener("click", () => {
  researchMoveDialog.close();
});

researchMoveForm.addEventListener("submit", async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(researchMoveForm));
  const statusBox = document.querySelector("#research-move-message");
  statusBox.hidden = false;
  statusBox.className = "status-message loading";
  statusBox.textContent = "Movendo…";
  try {
    const body = data.destino === "novo"
      ? { novo_cliente: { nome: data.novo_nome, email: data.novo_email || null, telefone: data.novo_telefone || "", empresa: data.novo_empresa || null } }
      : { lead_id_destino: Number(data.lead_id_destino) };
    const resultado = await responsePayload(await fetch(`/v1/admin/pesquisas/${encodeURIComponent(state.moveResearchId)}/mover-lead`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }));
    researchMoveDialog.close();
    showMessage(`Pesquisa movida para ${resultado.lead_destino_nome}.`, "success");
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
  const moveResearch = event.target.closest(".move-research");
  if (moveResearch) {
    openResearchMove(moveResearch.dataset.researchId, moveResearch.dataset.researchMarca);
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
  if (event.target.matches('#lead-crm-form [name="status"]')) {
    const bloco = document.querySelector("#lead-motivo-perda");
    if (bloco) bloco.hidden = event.target.value !== "descartado";
    return;
  }
  if (!event.target.matches("#lead-contact-filter")) return;
  loadLeadContacts(state.openLeadId, event.target.value);
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
    const proximaAcao = data.get("proxima_acao_em");
    if (proximaAcao) {
      const leadUpdate = await fetch(`/v1/admin/leads/${form.dataset.leadId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ proxima_acao_em: new Date(proximaAcao).toISOString() }) });
      if (!leadUpdate.ok) { saveMessage.textContent = "Contato registrado, mas não foi possível salvar o próximo contato."; return; }
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
  const saveMessage = document.querySelector("#lead-save-message");
  if (data.get("status") === "descartado" && !data.get("motivo_perda")) {
    saveMessage.textContent = "Informe o motivo da perda.";
    return;
  }
  const payload = {
    status: data.get("status"),
    responsavel_id: data.get("responsavel_id") ? Number(data.get("responsavel_id")) : null,
    proxima_acao_em: data.get("proxima_acao_em") ? new Date(data.get("proxima_acao_em")).toISOString() : null,
    notas: data.get("notas") || null,
    tags: String(data.get("tags") || "").split(",").map(item => item.trim()).filter(Boolean),
    registrar_contato: true,
    motivo_perda: data.get("motivo_perda") || null,
    motivo_perda_detalhe: data.get("motivo_perda_detalhe") || null,
  };
  if (state.canPii) payload.documento = data.get("documento") || null;
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
async function renderPropostas(lead) {
  const box = document.querySelector("#lead-propostas");
  if (!box) return;
  try {
    const data = await (await fetch(`/v1/admin/leads/${lead.id}/propostas`)).json();
    const rows = (data.propostas || []).map(p => `<tr><td data-label="Número">${escapeHtml(p.numero)}</td><td data-label="Versão">v${p.versao}</td><td data-label="Marca">${escapeHtml(p.marca || "A definir")}</td><td data-label="Status"><span class="role-badge">${escapeHtml(p.status)}</span></td><td data-label="Total">${formatCurrency(p.total)}</td><td data-label="Ações"><div class="proposal-actions"><button class="secondary-button proposal-pdf" data-id="${p.id}" type="button">PDF</button><button class="secondary-button proposal-link" data-id="${p.id}" type="button">Gerar link</button><button class="secondary-button proposal-preview" data-id="${p.id}" type="button">Visualizar</button>${state.canManage && p.status === "rascunho" ? `<button class="secondary-button proposal-send" data-id="${p.id}" type="button">Enviar por e-mail</button>` : ""}${state.canManage && p.status === "enviada" ? `<button class="secondary-button proposal-accept" data-id="${p.id}" type="button">Registrar aceite</button>` : ""}${state.canManage ? `<button class="secondary-button proposal-version" data-id="${p.id}" type="button">Nova versão</button>` : ""}</div></td></tr>`).join("");
    box.innerHTML = `<header><div><p class="eyebrow">Comercial</p><h3>Propostas de registro</h3></div>${state.canManage ? `<button id="new-proposal" class="secondary-button" type="button">Criar proposta</button>` : ""}</header>${rows ? `<div class="doc-table-scroll"><table class="lead-docs lead-proposals-table"><thead><tr><th>Número</th><th>Versão</th><th>Marca</th><th>Status</th><th>Total</th><th>Ações</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p>Nenhuma proposta criada.</p>`}`;
    box.querySelector("#new-proposal")?.addEventListener("click", () => criarProposta(lead, box));
    box.querySelectorAll(".proposal-preview").forEach(button => button.addEventListener("click", () => visualizarProposta(button.dataset.id)));
    box.querySelectorAll(".proposal-pdf").forEach(button => button.addEventListener("click", () => window.open(`/v1/admin/propostas/${button.dataset.id}/pdf`, "_blank")));
    box.querySelectorAll(".proposal-link").forEach(button => button.addEventListener("click", async () => {
      const response = await fetch(`/v1/admin/propostas/${button.dataset.id}/link`, { method: "POST" });
      if (response.ok) { const data = await response.json(); await navigator.clipboard?.writeText(data.link); alert(`Link gerado e copiado:\n${data.link}`); }
      else alert("NÃ£o foi possÃ­vel gerar o link.");
    }));
    box.querySelectorAll(".proposal-version").forEach(button => button.addEventListener("click", async () => {
      const response = await fetch(`/v1/admin/propostas/${button.dataset.id}/nova-versao`, { method: "POST" });
      if (response.ok) await renderPropostas(lead); else alert("Não foi possível criar a nova versão.");
    }));
    box.querySelectorAll(".proposal-send, .proposal-accept").forEach(button => button.addEventListener("click", async () => {
      const response = button.classList.contains("proposal-send")
        ? await fetch(`/v1/admin/propostas/${button.dataset.id}/enviar`, { method: "POST" })
        : await fetch(`/v1/admin/propostas/${button.dataset.id}/status`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "aceita" }) });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        alert(data.detail || "Não foi possível atualizar a proposta.");
        return;
      }
      if (response.ok) await renderPropostas(lead);
      else alert("Não foi possível atualizar a proposta.");
    }));
  } catch { box.innerHTML = "<p class=\"status-message error\">Não foi possível carregar as propostas.</p>"; }
}

function formatCurrency(value) {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(value || 0));
}

function criarProposta(lead, box) {
  // Abre o diálogo de revisão em vez de gerar direto com valores fixos --
  // honorários e taxa GRU não podem mais ser alterados depois de criada a
  // proposta (só numa nova versão), então precisam de conferência antes.
  proposalContext = { lead, box };
  const statusBox = document.querySelector("#proposal-message");
  statusBox.hidden = true;
  proposalForm.elements.honorarios.value = "1500";
  proposalForm.elements.taxa_gru.value = "415";
  proposalForm.elements.condicoes_pagamento.value = "50% na contratação e 50% no protocolo";
  proposalForm.elements.validade_em.value = "";
  proposalForm.elements.escopo.value = "Pesquisa, preparação e protocolo de registro de marca no INPI";
  const opcoes = document.querySelector("#proposal-research-options");
  opcoes.innerHTML = (lead.pesquisas || []).map(item => `
    <label>
      <input type="checkbox" name="pesquisa_ids" value="${escapeHtml(item.id)}" checked>
      <span><strong>${escapeHtml(item.marca)}</strong><small>${item.classe_nice ? `NCL ${escapeHtml(item.classe_nice)}` : "Todas as classes"}</small></span>
    </label>`).join("") || "<p>Nenhuma pesquisa vinculada. A proposta será criada sem marca definida.</p>";
  proposalDialog.showModal();
}

document.querySelector("#cancel-proposal").addEventListener("click", () => {
  proposalDialog.close();
  proposalContext = null;
});

proposalForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!proposalContext) return;
  const { lead, box } = proposalContext;
  const formData = new FormData(proposalForm);
  const dados = Object.fromEntries(formData);
  const pesquisaIds = formData.getAll("pesquisa_ids");
  const statusBox = document.querySelector("#proposal-message");
  statusBox.hidden = false;
  if ((lead.pesquisas || []).length && !pesquisaIds.length) {
    statusBox.className = "status-message error";
    statusBox.textContent = "Selecione ao menos uma marca/classe para a proposta.";
    return;
  }
  statusBox.className = "status-message loading";
  statusBox.textContent = "Gerando proposta…";
  const payload = {
    pesquisa_id: pesquisaIds[0] || null,
    pesquisa_ids: pesquisaIds,
    validade_em: dados.validade_em || null,
    escopo: dados.escopo,
    honorarios: Number(dados.honorarios),
    taxa_gru: Number(dados.taxa_gru),
    condicoes_pagamento: dados.condicoes_pagamento || null,
  };
  try {
    await responsePayload(await fetch(`/v1/admin/leads/${lead.id}/propostas`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
    proposalDialog.close();
    proposalContext = null;
    if (box) await renderPropostas(lead, box);
    else { alert("Proposta criada com sucesso."); openLead(lead.id); }
  } catch (error) {
    statusBox.className = "status-message error";
    statusBox.textContent = error.message;
  }
});

async function visualizarProposta(id) {
  const response = await fetch(`/v1/admin/propostas/${id}/documento`);
  if (!response.ok) return alert("Não foi possível abrir a proposta.");
  const data = await response.json();
  const win = window.open("", "_blank", "noopener,noreferrer");
  // Janela em branco herda o CSP style-src estrito da página que a abriu:
  // o CSS vem de um link 'self', nunca de um atributo style="" inline.
  if (win) win.document.write(`<link rel="stylesheet" href="/static/print-proposta.css"><pre class="proposta-texto">${escapeHtml(data.texto)}</pre>`);
}

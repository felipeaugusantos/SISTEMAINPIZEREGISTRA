const legalState = { references: null, canManage: false, checklists: {} };
const legalMessage = document.querySelector("#legal-message");
const deadlineDialog = document.querySelector("#deadline-dialog");
const deadlineForm = document.querySelector("#deadline-form");
const deliveryDialog = document.querySelector("#delivery-dialog");
const deliveryForm = document.querySelector("#delivery-form");
const dateOnly = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short" });
const dateTime = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value ?? "";
  return node.innerHTML;
}
function showMessage(text, kind = "error") {
  legalMessage.hidden = !text;
  legalMessage.textContent = text;
  legalMessage.className = `status-message ${kind}`;
}
async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join(" · ")
      : data.detail;
    throw new Error(detail || `Não foi possível concluir (${response.status})`);
  }
  return data;
}
function optionList(items, selected = "") {
  return items.map((item) => `<option value="${escapeHtml(item.id)}"${String(item.id) === String(selected) ? " selected" : ""}>${escapeHtml(item.nome)}</option>`).join("");
}
function queryParams() {
  const query = new URLSearchParams(new FormData(document.querySelector("#legal-filter")));
  [...query].forEach(([key, value]) => { if (!value) query.delete(key); });
  return query;
}
function renderMetrics(metrics) {
  const values = [
    ["Vencidos", metrics.vencidos, "danger"],
    ["Vencem hoje", metrics.vence_hoje, "warning"],
    ["Próximos 7 dias", metrics.proximos_7_dias, "warning"],
    ["A confirmar", metrics.aguardando_confirmacao, ""],
    ["Agenda filtrada", metrics.total, ""],
  ];
  document.querySelector("#legal-metrics").innerHTML = values.map(([label, value, kind]) => `<article class="${kind}"><span>${label}</span><strong>${value}</strong></article>`).join("");
}
function renderNotifications(items) {
  document.querySelector("#legal-notification-count").textContent = `${items.filter((item) => item.status === "nova").length} não lida(s)`;
  document.querySelector("#legal-notifications").innerHTML = items.length
    ? items.map((item) => `<article class="legal-notification ${item.status === "nova" ? "new" : ""}"><i aria-hidden="true"></i><div><h3>${escapeHtml(item.titulo)} · ${escapeHtml(item.numero)}</h3><p>${escapeHtml(item.mensagem)} · ${dateTime.format(new Date(item.criado_em))}</p></div>${item.status === "nova" ? `<button class="secondary-button read-notification" data-id="${item.id}" type="button">Marcar como lida</button>` : `<span class="legal-badge">Lida</span>`}</article>`).join("")
    : '<div class="legal-empty">Nenhuma notificação jurídica.</div>';
}
function deadlineClass(item) {
  if (!item.confirmado) return "unconfirmed";
  if (item.vencido) return "overdue";
  if (item.dias_restantes <= 7 && ["pendente", "em_andamento"].includes(item.status)) return "due-soon";
  return "";
}
function deadlineActions(item) {
  if (!legalState.canManage || ["concluido", "cancelado"].includes(item.status)) return "";
  if (!item.confirmado) return `<button class="primary-button confirm-deadline" data-id="${item.id}" type="button">Confirmar prazo</button><button class="secondary-button cancel-deadline" data-id="${item.id}" type="button">Descartar sugestão</button>`;
  return `<button class="primary-button complete-deadline" data-id="${item.id}" type="button">Concluir</button><button class="secondary-button progress-deadline" data-id="${item.id}" type="button">Em andamento</button><button class="secondary-button delivery-deadline" data-id="${item.id}" type="button">Registrar entrega</button>`;
}
function renderDeadlines(items) {
  document.querySelector("#legal-deadlines").innerHTML = items.length
    ? items.map((item) => `<article class="legal-deadline ${deadlineClass(item)}"><div><div class="legal-badges"><span class="legal-badge">${escapeHtml(item.tipo_nome)}</span><span class="legal-badge ${item.prioridade === "critica" ? "critical" : ""}">${escapeHtml(item.prioridade)}</span>${!item.confirmado ? '<span class="legal-badge critical">Conferência obrigatória</span>' : ""}</div><p class="legal-deadline-identity"><a class="legal-process-number" href="/processos/${encodeURIComponent(item.numero)}" target="_blank" rel="noopener">${escapeHtml(item.numero)}</a><span class="legal-identity-sep">–</span><strong class="legal-client-name">${escapeHtml(item.empresa || item.marca || "Cliente não identificado")}</strong></p><h3>${escapeHtml(item.titulo)}</h3>${item.empresa ? "" : '<span class="legal-badge">Sem empresa vinculada</span>'}<small>${escapeHtml(item.descricao || "Sem orientações adicionais")}</small></div><div class="legal-deadline-date"><span>Vencimento</span><strong>${dateOnly.format(new Date(item.vencimento_em))}</strong><span>${item.vencido ? `Vencido há ${Math.abs(item.dias_restantes)} dia(s)` : `${item.dias_restantes} dia(s) restante(s)`} · ${escapeHtml(item.contagem)}</span></div><div class="legal-deadline-owner"><span>Responsável</span><strong>${escapeHtml(item.responsavel || "Não atribuído")}</strong><span>Escalonamento: ${escapeHtml(item.escalonar_para || "não definido")}</span><span>Status: ${escapeHtml(item.status.replaceAll("_", " "))}</span></div><div class="legal-actions">${deadlineActions(item)}${item.empresa ? "" : `<button class="secondary-button vincular-cliente" data-id="${item.id}" data-sugestao="${escapeHtml(item.marca || "")}" type="button">Vincular cliente no CRM</button>`}<button class="secondary-button open-checklist" data-id="${item.id}" data-tipo="${escapeHtml(item.tipo_nome)}" type="button">Checklist${checklistBadge(item.id)}</button></div></article>`).join("")
    : '<div class="legal-empty">Nenhum prazo encontrado. Cadastre um prazo ou execute o motor para procurar sugestões nas publicações da RPI.</div>';
}
function renderHistory(items) {
  document.querySelector("#legal-history").innerHTML = items.length
    ? items.map((item) => `<article><time datetime="${escapeHtml(item.criado_em)}">${dateTime.format(new Date(item.criado_em))}</time><strong>${escapeHtml(item.numero)}</strong><span>${escapeHtml(item.descricao)}</span><span>${escapeHtml(item.ator)}</span></article>`).join("")
    : '<div class="legal-empty">O histórico será formado por criações, alterações, entregas, escalonamentos e leituras.</div>';
}
async function loadDashboard() {
  const [data, checklists] = await Promise.all([
    api(`/v1/admin/juridico/painel?${queryParams()}`),
    api("/v1/admin/juridico/checklists/resumo").catch(() => ({})),
  ]);
  legalState.canManage = data.acoes.gerenciar;
  legalState.checklists = checklists || {};
  document.querySelector("#new-legal-deadline").hidden = !legalState.canManage;
  document.querySelector("#run-legal-engine").hidden = !legalState.canManage;
  renderMetrics(data.metricas);
  renderNotifications(data.notificacoes);
  renderDeadlines(data.prazos);
  renderKanban(data.prazos);
  renderHistory(data.historico);
}
async function loadReferences() {
  const data = await api("/v1/admin/juridico/referencias");
  legalState.references = data;
  const users = optionList(data.usuarios);
  document.querySelector("#legal-filter").elements.responsavel_id.innerHTML = '<option value="">Todos</option>' + users;
  deadlineForm.elements.processo_monitorado_id.innerHTML = '<option value="">Selecione</option>' + optionList(data.processos);
  deadlineForm.elements.tipo.innerHTML = optionList(data.tipos);
  deadlineForm.elements.responsavel_id.innerHTML = '<option value="">Não atribuído</option>' + users;
  deadlineForm.elements.escalonar_para_id.innerHTML = '<option value="">Sem escalonamento</option>' + users;
}
function openDeadline() {
  deadlineForm.reset();
  deadlineForm.elements.prioridade.value = "media";
  deadlineForm.elements.antecedencia_dias.value = 7;
  deadlineForm.elements.escalonar_dias_antes.value = 2;
  deadlineForm.elements.data_base.value = new Date().toISOString().slice(0, 10);
  deadlineDialog.showModal();
}
async function updateDeadline(id, payload) {
  await api(`/v1/admin/juridico/prazos/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  await loadDashboard();
}

document.querySelector("#legal-filter").addEventListener("submit", (event) => { event.preventDefault(); loadDashboard().catch((error) => showMessage(error.message)); });
document.querySelector("#clear-legal-filter").addEventListener("click", () => { document.querySelector("#legal-filter").reset(); loadDashboard().catch((error) => showMessage(error.message)); });
document.querySelector("#new-legal-deadline").addEventListener("click", openDeadline);
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => deadlineDialog.close()));
document.querySelectorAll("[data-close-delivery]").forEach((button) => button.addEventListener("click", () => deliveryDialog.close()));
document.querySelector("#run-legal-engine").addEventListener("click", async () => {
  showMessage("Analisando prazos e escalonamentos…", "loading");
  try {
    const result = await api("/v1/admin/juridico/motor/executar", { method: "POST" });
    showMessage(`Motor concluído: ${result.prazos_sugeridos} sugestão(ões), ${result.notificacoes_criadas} notificação(ões) e ${result.escalados} escalonamento(s).`, "success");
    await loadDashboard();
  } catch (error) { showMessage(error.message); }
});
deadlineForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(deadlineForm));
  for (const field of ["processo_monitorado_id", "dias_prazo", "antecedencia_dias", "escalonar_dias_antes"]) data[field] = Number(data[field]);
  for (const field of ["responsavel_id", "escalonar_para_id"]) data[field] = Number(data[field]) || null;
  data.descricao = data.descricao || null;
  try {
    const result = await api("/v1/admin/juridico/prazos", { method: "POST", body: JSON.stringify(data) });
    deadlineDialog.close();
    showMessage(`Prazo salvo com vencimento em ${dateOnly.format(new Date(result.vencimento_em))}.`, "success");
    await loadDashboard();
  } catch (error) { showMessage(error.message); }
});
document.querySelector("#legal-deadlines").addEventListener("click", (event) => {
  const confirm = event.target.closest(".confirm-deadline");
  const cancel = event.target.closest(".cancel-deadline");
  const complete = event.target.closest(".complete-deadline");
  const progress = event.target.closest(".progress-deadline");
  const delivery = event.target.closest(".delivery-deadline");
  if (confirm) updateDeadline(confirm.dataset.id, { confirmar: true, descricao_evento: "Prazo sugerido pela RPI conferido e confirmado pelo operador" }).catch((error) => showMessage(error.message));
  if (cancel) updateDeadline(cancel.dataset.id, { status: "cancelado", descricao_evento: "Sugestão automática descartada após conferência" }).catch((error) => showMessage(error.message));
  if (complete) updateDeadline(complete.dataset.id, { status: "concluido" }).catch((error) => showMessage(error.message));
  if (progress) updateDeadline(progress.dataset.id, { status: "em_andamento" }).catch((error) => showMessage(error.message));
  if (delivery) { deliveryForm.reset(); deliveryForm.elements.prazo_id.value = delivery.dataset.id; deliveryDialog.showModal(); }
  const vincular = event.target.closest(".vincular-cliente");
  if (vincular) {
    const nome = window.prompt("Nome do cliente para vincular no CRM:", vincular.dataset.sugestao || "");
    if (nome !== null) api(`/v1/admin/juridico/prazos/${vincular.dataset.id}/vincular-cliente`, { method: "POST", body: JSON.stringify({ empresa_nome: nome.trim() || null }) }).then((resposta) => { showMessage(`Cliente vinculado: ${resposta.empresa}`, "success"); loadDashboard().catch((error) => showMessage(error.message)); }).catch((error) => showMessage(error.message));
  }
});
document.querySelector("#legal-notifications").addEventListener("click", async (event) => {
  const button = event.target.closest(".read-notification");
  if (!button) return;
  try { await api(`/v1/admin/juridico/notificacoes/${button.dataset.id}`, { method: "PATCH" }); await loadDashboard(); }
  catch (error) { showMessage(error.message); }
});
deliveryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(deliveryForm));
  const prazoId = data.prazo_id; delete data.prazo_id;
  data.protocolo = data.protocolo || null; data.documento = data.documento || null;
  try {
    await api(`/v1/admin/juridico/prazos/${prazoId}/entregas`, { method: "POST", body: JSON.stringify(data) });
    deliveryDialog.close(); showMessage("Entrega registrada na trilha auditável.", "success"); await loadDashboard();
  } catch (error) { showMessage(error.message); }
});

Promise.all([loadReferences(), loadDashboard()]).catch((error) => showMessage(error.message));

/* ===== Checklist + Kanban ===== */
const KANBAN_COLUNAS = [
  ["aguardando_confirmacao", "Aguardando"],
  ["pendente", "Pendente"],
  ["em_andamento", "Em andamento"],
  ["concluido", "Concluído"],
  ["cancelado", "Cancelado"],
];

function checklistBadge(id) {
  const resumo = legalState.checklists[id];
  if (!resumo || !resumo.total) return "";
  const done = resumo.concluidos === resumo.total;
  return ` <span class="checklist-chip ${done ? "done" : ""}">&#9745; ${resumo.concluidos}/${resumo.total}</span>`;
}

function kanbanCard(item) {
  const draggable = legalState.canManage && !["concluido", "cancelado"].includes(item.status);
  return `<article class="kanban-card ${deadlineClass(item)}" draggable="${draggable}" data-id="${item.id}" data-status="${item.status}">
    <div class="kanban-card-badges"><span class="legal-badge">${escapeHtml(item.tipo_nome)}</span>${!item.confirmado ? '<span class="legal-badge critical">Sugestão</span>' : ""}${checklistBadge(item.id)}</div>
    <h4>${escapeHtml(item.titulo)}</h4>
    <p>${escapeHtml(item.numero)} · ${escapeHtml(item.marca || "—")}</p>
    <small>Vence ${dateOnly.format(new Date(item.vencimento_em))} · ${escapeHtml(item.responsavel || "sem responsável")}</small>
    <button class="secondary-button open-checklist" data-id="${item.id}" data-tipo="${escapeHtml(item.tipo_nome)}" type="button">Checklist</button>
  </article>`;
}

// innerHTML abaixo: conteúdo montado com literais + valores via escapeHtml/numéricos.
function renderKanban(items) {
  document.querySelector("#legal-kanban").innerHTML = KANBAN_COLUNAS.map(([status, label]) => {
    const cards = items.filter((item) => item.status === status);
    return `<div class="kanban-column" data-status="${status}"><header><span>${label}</span><b>${cards.length}</b></header><div class="kanban-dropzone">${cards.map(kanbanCard).join("") || '<p class="kanban-empty">—</p>'}</div></div>`;
  }).join("");
}

function transicaoKanban(from, to) {
  if (from === "aguardando_confirmacao") {
    if (to === "pendente") return { confirmar: true };
    if (to === "cancelado") return { status: "cancelado", descricao_evento: "Sugestão descartada no Kanban" };
    return null;
  }
  if (["pendente", "em_andamento", "concluido", "cancelado"].includes(to)) return { status: to };
  return null;
}

function setLegalView(view) {
  const list = view === "list";
  document.querySelector("#legal-deadlines").hidden = !list;
  document.querySelector("#legal-kanban").hidden = list;
  document.querySelector("#view-list").classList.toggle("active", list);
  document.querySelector("#view-kanban").classList.toggle("active", !list);
  document.querySelector("#view-list").setAttribute("aria-pressed", String(list));
  document.querySelector("#view-kanban").setAttribute("aria-pressed", String(!list));
}
document.querySelector("#view-list").addEventListener("click", () => setLegalView("list"));
document.querySelector("#view-kanban").addEventListener("click", () => setLegalView("kanban"));

const kanbanBoard = document.querySelector("#legal-kanban");
kanbanBoard.addEventListener("dragstart", (event) => {
  const card = event.target.closest(".kanban-card[draggable='true']");
  if (!card) return;
  event.dataTransfer.setData("text/plain", JSON.stringify({ id: card.dataset.id, from: card.dataset.status }));
  card.classList.add("dragging");
});
kanbanBoard.addEventListener("dragend", (event) => event.target.closest(".kanban-card")?.classList.remove("dragging"));
kanbanBoard.addEventListener("dragover", (event) => {
  const column = event.target.closest(".kanban-column");
  if (column) { event.preventDefault(); column.classList.add("drag-over"); }
});
kanbanBoard.addEventListener("dragleave", (event) => event.target.closest(".kanban-column")?.classList.remove("drag-over"));
kanbanBoard.addEventListener("drop", async (event) => {
  const column = event.target.closest(".kanban-column");
  if (!column) return;
  event.preventDefault();
  column.classList.remove("drag-over");
  let payload;
  try { payload = JSON.parse(event.dataTransfer.getData("text/plain")); } catch { return; }
  if (!payload || payload.from === column.dataset.status) return;
  const patch = transicaoKanban(payload.from, column.dataset.status);
  if (!patch) { showMessage("Transição não permitida. Confirme ou descarte a sugestão primeiro.", "error"); return; }
  try { await updateDeadline(payload.id, patch); showMessage("Prazo movido para " + column.dataset.status.replaceAll("_", " ") + "."); }
  catch (error) { showMessage(error.message, "error"); }
});

const checklistDialog = document.querySelector("#checklist-dialog");
let checklistPrazoId = null;
// innerHTML abaixo: descrições via escapeHtml, ids numéricos.
function renderChecklist(data) {
  document.querySelector("#checklist-empty").hidden = data.total > 0;
  const pct = data.total ? Math.round((data.concluidos / data.total) * 100) : 0;
  document.querySelector("#checklist-bar").style.width = `${pct}%`;
  document.querySelector("#checklist-progress-label").textContent = `${data.concluidos}/${data.total} concluídas`;
  document.querySelector("#checklist-items").innerHTML = data.itens.map((item) =>
    `<li class="${item.concluido ? "done" : ""}"><label><input type="checkbox" data-item="${item.id}" ${item.concluido ? "checked" : ""} ${legalState.canManage ? "" : "disabled"}><span>${escapeHtml(item.descricao)}</span></label>${legalState.canManage ? `<button class="checklist-remove" data-item="${item.id}" type="button" aria-label="Remover etapa">×</button>` : ""}</li>`
  ).join("");
}
async function loadChecklist() {
  renderChecklist(await api(`/v1/admin/juridico/prazos/${checklistPrazoId}/checklist`));
}
async function openChecklist(id, tipo) {
  checklistPrazoId = id;
  document.querySelector("#checklist-title").textContent = `Etapas · ${tipo || "prazo"}`;
  document.querySelector("#checklist-add").hidden = !legalState.canManage;
  document.querySelector("#checklist-default").hidden = !legalState.canManage;
  checklistDialog.showModal();
  await loadChecklist();
}
document.addEventListener("click", (event) => {
  const button = event.target.closest(".open-checklist");
  if (!button) return;
  openChecklist(button.dataset.id, button.dataset.tipo).catch((error) => showMessage(error.message, "error"));
});
document.querySelectorAll("[data-close-checklist]").forEach((button) => button.addEventListener("click", () => checklistDialog.close()));
document.querySelector("#checklist-items").addEventListener("change", async (event) => {
  const checkbox = event.target.closest("input[data-item]");
  if (!checkbox) return;
  try {
    await api(`/v1/admin/juridico/checklist/${checkbox.dataset.item}`, { method: "PATCH", body: JSON.stringify({ concluido: checkbox.checked }) });
    await loadChecklist(); await loadDashboard();
  } catch (error) { showMessage(error.message, "error"); await loadChecklist(); }
});
document.querySelector("#checklist-items").addEventListener("click", async (event) => {
  const button = event.target.closest(".checklist-remove");
  if (!button) return;
  try { await api(`/v1/admin/juridico/checklist/${button.dataset.item}`, { method: "DELETE" }); await loadChecklist(); await loadDashboard(); }
  catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#checklist-add").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = event.currentTarget.elements.descricao;
  try {
    await api(`/v1/admin/juridico/prazos/${checklistPrazoId}/checklist`, { method: "POST", body: JSON.stringify({ descricao: input.value }) });
    input.value = ""; await loadChecklist(); await loadDashboard();
  } catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#checklist-default").addEventListener("click", async () => {
  try { await api(`/v1/admin/juridico/prazos/${checklistPrazoId}/checklist/padrao`, { method: "POST" }); await loadChecklist(); await loadDashboard(); }
  catch (error) { showMessage(error.message, "error"); }
});

const legalState = { references: null, canManage: false };
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
    ? items.map((item) => `<article class="legal-deadline ${deadlineClass(item)}"><div><div class="legal-badges"><span class="legal-badge">${escapeHtml(item.tipo_nome)}</span><span class="legal-badge ${item.prioridade === "critica" ? "critical" : ""}">${escapeHtml(item.prioridade)}</span>${!item.confirmado ? '<span class="legal-badge critical">Conferência obrigatória</span>' : ""}</div><h3>${escapeHtml(item.titulo)}</h3><p><strong>${escapeHtml(item.numero)}</strong> · ${escapeHtml(item.marca || "Sem título")} · ${escapeHtml(item.empresa || "Sem empresa vinculada")}</p><small>${escapeHtml(item.descricao || "Sem orientações adicionais")}</small></div><div class="legal-deadline-date"><span>Vencimento</span><strong>${dateOnly.format(new Date(item.vencimento_em))}</strong><span>${item.vencido ? `Vencido há ${Math.abs(item.dias_restantes)} dia(s)` : `${item.dias_restantes} dia(s) restante(s)`} · ${escapeHtml(item.contagem)}</span></div><div class="legal-deadline-owner"><span>Responsável</span><strong>${escapeHtml(item.responsavel || "Não atribuído")}</strong><span>Escalonamento: ${escapeHtml(item.escalonar_para || "não definido")}</span><span>Status: ${escapeHtml(item.status.replaceAll("_", " "))}</span></div><div class="legal-actions">${deadlineActions(item)}</div></article>`).join("")
    : '<div class="legal-empty">Nenhum prazo encontrado. Cadastre um prazo ou execute o motor para procurar sugestões nas publicações da RPI.</div>';
}
function renderHistory(items) {
  document.querySelector("#legal-history").innerHTML = items.length
    ? items.map((item) => `<article><time datetime="${escapeHtml(item.criado_em)}">${dateTime.format(new Date(item.criado_em))}</time><strong>${escapeHtml(item.numero)}</strong><span>${escapeHtml(item.descricao)}</span><span>${escapeHtml(item.ator)}</span></article>`).join("")
    : '<div class="legal-empty">O histórico será formado por criações, alterações, entregas, escalonamentos e leituras.</div>';
}
async function loadDashboard() {
  const data = await api(`/v1/admin/juridico/painel?${queryParams()}`);
  legalState.canManage = data.acoes.gerenciar;
  document.querySelector("#new-legal-deadline").hidden = !legalState.canManage;
  document.querySelector("#run-legal-engine").hidden = !legalState.canManage;
  renderMetrics(data.metricas);
  renderNotifications(data.notificacoes);
  renderDeadlines(data.prazos);
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

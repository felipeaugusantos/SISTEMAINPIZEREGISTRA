// Evita style.width inline (bloqueado pelo CSP style-src estrito): arredonda
// para o multiplo de 5 mais proximo e aplica a classe utilitaria .w-pct-N.
function setBarWidth(el, percentual) {
  const passo = Math.min(100, Math.max(0, Math.round((Number(percentual) || 0) / 5) * 5));
  el.className = el.className.replace(/\bw-pct-\d+\b/g, "").trim();
  el.classList.add(`w-pct-${passo}`);
}

const legalState = { references: null, rules: [], canManage: false, canGovernRules: false, canConfirmCritical: false, checklists: {}, offset: 0, pageSize: 10 };
const legalMessage = document.querySelector("#legal-message");
const deadlineDialog = document.querySelector("#deadline-dialog");
const deadlineForm = document.querySelector("#deadline-form");
const deliveryDialog = document.querySelector("#delivery-dialog");
const deliveryForm = document.querySelector("#delivery-form");
const confirmDeadlineDialog = document.querySelector("#confirm-deadline-dialog");
const confirmDeadlineForm = document.querySelector("#confirm-deadline-form");
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
function civilDate(value) {
  return value ? dateOnly.format(new Date(`${value}T12:00:00`)) : "sem término";
}
function splitLines(value) {
  return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("Não foi possível ler o anexo selecionado."));
    reader.readAsDataURL(file);
  });
}
function queryParams() {
  const query = new URLSearchParams(new FormData(document.querySelector("#legal-filter")));
  [...query].forEach(([key, value]) => { if (!value) query.delete(key); });
  query.set("limite", legalState.pageSize);
  query.set("deslocamento", legalState.offset);
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
function intakeStatus(item) {
  const labels = {
    pronto: ["Pronto para iniciar", "ready"],
    documentos_pendentes: ["Aguardando documentos", "warning"],
    pagamento_pendente: ["Pagamento pendente", "danger"],
    em_atendimento: ["Em atendimento", "active"],
  };
  return labels[item.status] || [item.status, ""];
}
function renderIntakes(data) {
  const items = data.itens || [];
  document.querySelector("#legal-intake-count").textContent = `${data.metricas?.novos || 0} novo(s)`;
  document.querySelector("#legal-intakes").innerHTML = items.length ? items.map((item) => {
    const [statusLabel, statusClass] = intakeStatus(item);
    const pendencias = item.documentos_pendentes?.length
      ? `<p class="legal-intake-pending"><strong>Pendências:</strong> ${item.documentos_pendentes.map(escapeHtml).join(", ")}</p>`
      : '<p class="legal-intake-ready">Documentação mínima conferida pelo sistema.</p>';
    const action = item.recebido_em
      ? `<div class="legal-intake-owner"><span>Responsável</span><strong>${escapeHtml(item.recebido_por || "Equipe jurídica")}</strong><small>Recebido em ${dateTime.format(new Date(item.recebido_em))}</small></div>`
      : legalState.canManage
        ? `<button class="primary-button receive-intake" data-id="${item.proposta_id}" type="button">Assumir atendimento</button>`
        : "";
    return `<article class="legal-intake ${statusClass}">
      <header><div><span class="legal-badge ${statusClass}">${escapeHtml(statusLabel)}</span><h3>${escapeHtml(item.cliente)}</h3><p>${escapeHtml(item.empresa || "Pessoa física")} · Proposta ${escapeHtml(item.proposta_numero)}</p></div><strong class="legal-intake-value">${Number(item.valor_total || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })}</strong></header>
      <div class="legal-intake-scope"><strong>${escapeHtml(item.marca || "Marca a definir")}</strong><span>${escapeHtml(item.classes || "Classes a definir")}</span></div>
      ${pendencias}
      <footer><div class="legal-intake-links"><a href="/admin/leads">Abrir oportunidade</a><a href="/admin/financeiro">Ver financeiro</a></div>${action}</footer>
    </article>`;
  }).join("") : '<div class="legal-empty">Nenhuma proposta paga aguardando início jurídico.</div>';
}
function renderNotifications(items) {
  document.querySelector("#legal-notification-count").textContent = `${items.filter((item) => item.status === "nova").length} não lida(s)`;
  document.querySelector("#legal-notifications").innerHTML = items.length
    ? items.map((item) => `<article class="legal-notification ${item.status === "nova" ? "new" : ""}"><i aria-hidden="true"></i><div><h3>${escapeHtml(item.titulo)} · ${escapeHtml(item.numero)}</h3><p>${escapeHtml(item.mensagem)} · ${dateTime.format(new Date(item.criado_em))}</p></div>${item.status === "nova" ? `<button class="secondary-button read-notification" data-id="${item.id}" type="button">Marcar como lida</button>` : `<span class="legal-badge">Lida</span>`}</article>`).join("")
    : '<div class="legal-empty">Nenhuma notificação jurídica.</div>';
}
function deadlineClass(item) {
  if (item.historico) return "historical";
  if (!item.confirmado) return "unconfirmed";
  if (item.vencido) return "overdue";
  if (item.dias_restantes <= 7 && ["pendente", "em_andamento"].includes(item.status)) return "due-soon";
  return "";
}
function deadlineTimeLabel(item) {
  if (item.historico) return "Referência histórica · não é pendência atual";
  if (item.vencido) return `Vencido há ${Math.abs(item.dias_restantes)} dia(s) · ${escapeHtml(item.contagem)}`;
  return `${item.dias_restantes} dia(s) restante(s) · ${escapeHtml(item.contagem)}`;
}
function deadlineActions(item) {
  const evidencias = `<button class="secondary-button delivery-deadline" data-id="${item.id}" type="button">Evidências</button>`;
  if (!legalState.canManage || ["concluido", "cancelado", "dispensado", "historico", "duplicado"].includes(item.status)) return evidencias;
  if (!item.confirmado) {
    const podeConfirmar = item.prioridade !== "critica" || legalState.canConfirmCritical;
    const botaoConfirmar = podeConfirmar
      ? `<button class="primary-button confirm-deadline" data-id="${item.id}" type="button">Confirmar prazo</button>`
      : `<span class="legal-badge">Confirmação restrita a administrador/tech</span>`;
    return `${botaoConfirmar}<button class="secondary-button cancel-deadline" data-id="${item.id}" type="button">Descartar sugestão</button>${evidencias}`;
  }
  return `<button class="primary-button complete-deadline" data-id="${item.id}" type="button">Concluir</button><button class="secondary-button progress-deadline" data-id="${item.id}" type="button">Em andamento</button>${evidencias}`;
}
function renderDeadlines(items) {
  const container = document.querySelector("#legal-deadlines");
  if (!items.length) {
    container.innerHTML = '<div class="legal-empty">Nenhum prazo encontrado. Cadastre um prazo ou execute o motor para procurar sugestões nas publicações da RPI.</div>';
    return;
  }
  const grupos = new Map();
  for (const item of items) {
    const chave = item.processo_monitorado_id ?? item.numero;
    if (!grupos.has(chave)) grupos.set(chave, []);
    grupos.get(chave).push(item);
  }
  container.innerHTML = [...grupos.values()].map((prazos) => {
    const ref = prazos[0];
    const cabecalho = `<header class="legal-process-header"><div class="legal-process-head-left"><button class="legal-toggle" type="button" aria-expanded="true" aria-label="Expandir ou recolher atualizações">▾</button><div class="legal-deadline-identity"><a class="legal-process-number" href="/processos/${encodeURIComponent(ref.numero)}" target="_blank" rel="noopener">${escapeHtml(ref.numero)}</a><span class="legal-identity-sep">–</span><strong class="legal-client-name">${escapeHtml(ref.empresa || ref.marca || "Cliente não identificado")}</strong>${ref.empresa ? "" : '<span class="legal-badge">Sem empresa vinculada</span>'}<span class="legal-process-count">${prazos.length} atualizaç${prazos.length === 1 ? "ão" : "ões"}</span></div></div><div class="legal-process-actions">${ref.empresa ? "" : `<button class="secondary-button vincular-cliente" data-id="${ref.id}" data-sugestao="${escapeHtml(ref.marca || "")}" type="button">Vincular cliente no CRM</button>`}<button class="secondary-button open-checklist" data-id="${ref.id}" data-tipo="${escapeHtml(ref.tipo_nome)}" type="button">Checklist${checklistBadge(ref.id)}</button></div></header>`;
    const linhas = prazos.map((item) => {
      const marcoOperacional = item.vencimento_operacional_em && item.vencimento_operacional_em !== item.vencimento_em
        ? `<span>Marco interno: ${dateOnly.format(new Date(item.vencimento_operacional_em))}</span>`
        : "";
      return `<div class="legal-deadline ${deadlineClass(item)}"><div><div class="legal-badges"><span class="legal-badge">${escapeHtml(item.tipo_nome)}</span><span class="legal-badge ${item.prioridade === "critica" ? "critical" : ""}">${escapeHtml(item.prioridade)}</span>${!item.confirmado ? '<span class="legal-badge critical">Conferência obrigatória</span>' : ""}${item.status === "dispensado" ? '<span class="legal-badge legal-badge-ok">Sem ação · taxa única INPI</span>' : ""}${item.historico ? '<span class="legal-badge legal-badge-history">Referência histórica</span>' : ""}</div><h3>${escapeHtml(item.titulo)}</h3><small>${escapeHtml(item.descricao || "Sem orientações adicionais")}</small></div><div class="legal-deadline-date"><span>${item.historico ? "Prazo original" : "Vencimento legal"}</span><strong>${dateOnly.format(new Date(item.vencimento_em))}</strong>${marcoOperacional}<span>${deadlineTimeLabel(item)}</span></div><div class="legal-deadline-owner"><span>Responsável</span><strong>${escapeHtml(item.responsavel || "Não atribuído")}</strong><span>Escalonamento: ${escapeHtml(item.escalonar_para || "não definido")}</span><span>Status: ${escapeHtml(item.status.replaceAll("_", " "))}</span></div><div class="legal-actions">${deadlineActions(item)}</div></div>`;
    }).join("");
    return `<article class="legal-process-group">${cabecalho}<div class="legal-process-deadlines">${linhas}</div></article>`;
  }).join("");
}
function renderHistory(items) {
  document.querySelector("#legal-history").innerHTML = items.length
    ? items.map((item) => `<article><time datetime="${escapeHtml(item.criado_em)}">${dateTime.format(new Date(item.criado_em))}</time><strong>${escapeHtml(item.numero)}</strong><span>${escapeHtml(item.descricao)}</span><span>${escapeHtml(item.ator)}</span></article>`).join("")
    : '<div class="legal-empty">O histórico será formado por criações, alterações, entregas, escalonamentos e leituras.</div>';
}
function renderPagination(pagination) {
  const nav = document.querySelector("#legal-pagination");
  nav.hidden = false;
  document.querySelector("#legal-page-summary").textContent = `Página ${pagination.pagina} de ${pagination.total_paginas} · ${pagination.total_clientes} cliente(s)`;
  document.querySelector("#legal-prev").disabled = !pagination.tem_anterior;
  document.querySelector("#legal-next").disabled = !pagination.tem_proxima;
}
async function loadDashboard() {
  const [data, checklists, intakes] = await Promise.all([
    api(`/v1/admin/juridico/painel?${queryParams()}`),
    api("/v1/admin/juridico/checklists/resumo").catch(() => ({})),
    api("/v1/admin/juridico/encaminhamentos"),
  ]);
  legalState.canManage = data.acoes.gerenciar;
  legalState.canConfirmCritical = data.acoes.confirmar_critico;
  legalState.checklists = checklists || {};
  document.querySelector("#new-legal-deadline").hidden = !legalState.canManage;
  document.querySelector("#run-legal-engine").hidden = !legalState.canManage;
  renderMetrics(data.metricas);
  renderIntakes(intakes);
  renderNotifications(data.notificacoes);
  renderDeadlines(data.prazos);
  renderKanban(data.prazos);
  renderCalendar(data.prazos);
  renderPagination(data.paginacao);
  renderHistory(data.historico);
}
async function loadReferences() {
  const data = await api("/v1/admin/juridico/referencias");
  legalState.references = data;
  legalState.canGovernRules = !!data.acoes.governar_regras;
  const users = optionList(data.usuarios);
  document.querySelector("#legal-filter").elements.responsavel_id.innerHTML = '<option value="">Todos</option>' + users;
  document.querySelector("#legal-filter").elements.tipo.innerHTML = '<option value="">Todos</option>' + optionList(data.tipos);
  deadlineForm.elements.processo_monitorado_id.innerHTML = '<option value="">Selecione</option>' + optionList(data.processos);
  deadlineForm.elements.tipo.innerHTML = optionList(data.tipos);
  deadlineForm.elements.responsavel_id.innerHTML = '<option value="">Não atribuído</option>' + users;
  deadlineForm.elements.escalonar_para_id.innerHTML = '<option value="">Sem escalonamento</option>' + users;
  document.querySelector("#legal-rule-form").elements.tipo_prazo.innerHTML = optionList(data.tipos);
  document.querySelector("#legal-rule-admin").hidden = !legalState.canGovernRules;
  document.querySelector("#legal-calendar-admin").hidden = !legalState.canGovernRules;
  document.querySelector("#legal-reprocess").hidden = !data.acoes.gerenciar;
}
async function loadLegalPolicy() {
  const [policy, engine] = await Promise.all([
    api("/v1/admin/juridico/politica"),
    api("/v1/admin/juridico/motor/execucoes?limite=1").catch(() => ({ execucoes: [] })),
  ]);
  const form = document.querySelector("#legal-policy-form");
  for (const field of ["exigir_responsavel_confirmacao", "exigir_checklist_conclusao", "exigir_evidencia_conclusao", "exigir_segunda_pessoa_critico"]) {
    form.elements[field].checked = !!policy[field];
  }
  form.elements.margem_operacional_dias.value = Number(policy.margem_operacional_dias || 0);
  [...form.elements].forEach((element) => { element.disabled = !legalState.canManage; });
  const latest = engine.execucoes?.[0];
  document.querySelector("#legal-engine-status").textContent = latest
    ? `Última execução: ${latest.status} · ${dateTime.format(new Date(latest.iniciado_em))}`
    : "Motor ainda não executado";
}
function renderRules(rules) {
  legalState.rules = rules;
  document.querySelector("#legal-rules-count").textContent = `${rules.length} regra(s) · catálogo versionado`;
  document.querySelector("#legal-rules").innerHTML = rules.length ? rules.map((rule) => `
    <article>
      <header><strong>Despacho ${escapeHtml(rule.codigo_despacho)}</strong><span class="legal-badge ${rule.confianca === "homologada" ? "ready" : "warning"}">${escapeHtml(rule.confianca)}</span></header>
      <h4>${escapeHtml(rule.descricao_oficial)}</h4>
      <p>${escapeHtml(rule.acao)} · ${rule.dias_prazo} dia(s) ${escapeHtml(rule.contagem)}</p>
      <small>Vigência: ${civilDate(rule.vigencia_inicio)} até ${civilDate(rule.vigencia_fim)} · ${rule.ativo ? "ativa" : "inativa"}</small>
      <details><summary>Fonte e controles</summary><p>${escapeHtml(rule.fonte_legal)}</p><p><strong>Aprovada por:</strong> ${escapeHtml(rule.aprovado_por)}</p><p><strong>Checklist:</strong> ${(rule.checklist || []).map(escapeHtml).join(" · ") || "não definido"}</p><p><strong>Evidências:</strong> ${(rule.evidencias_exigidas || []).map(escapeHtml).join(" · ") || "não definidas"}</p></details>
    </article>`).join("") : '<div class="legal-empty">Nenhuma regra de prazo cadastrada.</div>';
  const homologadas = rules.filter((rule) => rule.ativo && rule.confianca === "homologada");
  document.querySelector("#legal-reprocess-preview").elements.regra_id.innerHTML = homologadas.length
    ? homologadas.map((rule) => `<option value="${rule.id}">Despacho ${escapeHtml(rule.codigo_despacho)} · ${escapeHtml(rule.acao)}</option>`).join("")
    : '<option value="">Nenhuma regra homologada</option>';
}

function renderCalendarExceptions(items) {
  document.querySelector("#legal-calendar-exceptions").innerHTML = items.length ? items.map((item) => `
    <article>
      <header><strong>${escapeHtml(item.descricao)}</strong><span class="legal-badge">${escapeHtml(item.tipo.replaceAll("_", " "))}</span></header>
      <p>${civilDate(item.data_inicio)} a ${civilDate(item.data_fim)} · ${item.ativo ? "ativa" : "inativa"}</p>
      <details><summary>Fonte oficial</summary><p>${escapeHtml(item.fonte_oficial)}</p><p><strong>Aprovada por:</strong> ${escapeHtml(item.aprovado_por)}</p></details>
    </article>`).join("") : '<div class="legal-empty">Nenhuma exceção oficial cadastrada.</div>';
}

async function loadGovernance() {
  const [rules, exceptions] = await Promise.all([
    api("/v1/admin/juridico/regras-prazo"),
    api("/v1/admin/juridico/calendario/excecoes"),
  ]);
  renderRules(rules);
  renderCalendarExceptions(exceptions);
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
function renderDeliveryDocuments(items) {
  document.querySelector("#delivery-documents").innerHTML = items.length ? items.map((item) => `
    <article>
      <div><strong>${escapeHtml(item.nome)}</strong><small>${Number(item.tamanho_bytes || 0).toLocaleString("pt-BR")} bytes · ${escapeHtml(item.criado_por || "autor não informado")} · ${item.criado_em ? dateTime.format(new Date(item.criado_em)) : "data não informada"}</small><code>SHA-256 ${escapeHtml(item.hash)}</code></div>
      <a class="secondary-button" href="/v1/admin/juridico/prazos/${deliveryForm.elements.prazo_id.value}/documentos/${item.id}/download">Baixar</a>
    </article>`).join("") : '<div class="legal-empty">Nenhum anexo comprobatório registrado neste prazo.</div>';
}
async function loadDeliveryDocuments() {
  const prazoId = deliveryForm.elements.prazo_id.value;
  const result = await api(`/v1/admin/juridico/prazos/${prazoId}/documentos`);
  renderDeliveryDocuments(result.documentos || []);
}
async function openDelivery(id) {
  deliveryForm.reset();
  deliveryForm.elements.prazo_id.value = id;
  document.querySelector("#delivery-entry").hidden = !legalState.canManage;
  document.querySelector("#delivery-submit").hidden = !legalState.canManage;
  document.querySelector("#delivery-documents").innerHTML = '<div class="legal-empty">Carregando evidências…</div>';
  deliveryDialog.showModal();
  await loadDeliveryDocuments();
}

document.querySelector("#legal-filter").addEventListener("submit", (event) => { event.preventDefault(); legalState.offset = 0; loadDashboard().catch((error) => showMessage(error.message)); });
document.querySelector("#clear-legal-filter").addEventListener("click", () => { document.querySelector("#legal-filter").reset(); legalState.offset = 0; loadDashboard().catch((error) => showMessage(error.message)); });
document.querySelector("#legal-prev").addEventListener("click", () => { legalState.offset = Math.max(0, legalState.offset - legalState.pageSize); loadDashboard().catch((error) => showMessage(error.message)); });
document.querySelector("#legal-next").addEventListener("click", () => { legalState.offset += legalState.pageSize; loadDashboard().catch((error) => showMessage(error.message)); });
document.querySelector("#new-legal-deadline").addEventListener("click", openDeadline);
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => deadlineDialog.close()));
document.querySelectorAll("[data-close-delivery]").forEach((button) => button.addEventListener("click", () => deliveryDialog.close()));
document.querySelectorAll("[data-close-confirm-deadline]").forEach((button) => button.addEventListener("click", () => confirmDeadlineDialog.close()));
document.querySelector("#run-legal-engine").addEventListener("click", async () => {
  showMessage("Analisando prazos e escalonamentos…", "loading");
  try {
    const result = await api("/v1/admin/juridico/motor/executar", { method: "POST" });
    showMessage(`Motor concluído: ${result.prazos_sugeridos} sugestão(ões), ${result.prazos_historicos || 0} referência(s) histórica(s), ${result.prazos_duplicados || 0} duplicidade(s) arquivada(s), ${result.prazos_reconciliados || 0} pendência(s) encerrada(s) por despacho posterior, ${result.notificacoes_criadas} notificação(ões) e ${result.escalados} escalonamento(s).`, "success");
    await Promise.all([loadDashboard(), loadLegalPolicy()]);
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
  const toggle = event.target.closest(".legal-toggle");
  if (toggle) {
    const group = toggle.closest(".legal-process-group");
    const collapsed = group.classList.toggle("collapsed");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    return;
  }
  const confirm = event.target.closest(".confirm-deadline");
  const cancel = event.target.closest(".cancel-deadline");
  const complete = event.target.closest(".complete-deadline");
  const progress = event.target.closest(".progress-deadline");
  const delivery = event.target.closest(".delivery-deadline");
  if (confirm) {
    confirmDeadlineForm.reset();
    confirmDeadlineForm.elements.prazo_id.value = confirm.dataset.id;
    confirmDeadlineForm.elements.confirmacao_observacoes.value = "Prazo sugerido pela RPI conferido e confirmado pelo operador";
    confirmDeadlineDialog.showModal();
  }
  if (cancel) updateDeadline(cancel.dataset.id, { status: "cancelado", descricao_evento: "Sugestão automática descartada após conferência" }).catch((error) => showMessage(error.message));
  if (complete) updateDeadline(complete.dataset.id, { status: "concluido" }).catch((error) => showMessage(error.message));
  if (progress) updateDeadline(progress.dataset.id, { status: "em_andamento" }).catch((error) => showMessage(error.message));
  if (delivery) openDelivery(delivery.dataset.id).catch((error) => showMessage(error.message));
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
document.querySelector("#legal-intakes").addEventListener("click", async (event) => {
  const button = event.target.closest(".receive-intake");
  if (!button) return;
  button.disabled = true;
  try {
    const result = await api(`/v1/admin/juridico/encaminhamentos/${button.dataset.id}/receber`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    showMessage(`Atendimento recebido por ${result.responsavel}.`, "success");
    await loadDashboard();
  } catch (error) {
    button.disabled = false;
    showMessage(error.message);
  }
});
deliveryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(deliveryForm));
  const prazoId = data.prazo_id; delete data.prazo_id;
  const arquivo = data.arquivo; delete data.arquivo;
  data.protocolo = data.protocolo || null; data.documento = data.documento || null;
  try {
    if (arquivo?.size) {
      if (arquivo.size > 15 * 1024 * 1024) throw new Error("O anexo excede o limite de 15 MB.");
      if (arquivo.type && !["application/pdf", "image/png", "image/jpeg"].includes(arquivo.type)) throw new Error("Envie um arquivo PDF, PNG ou JPEG.");
      data.documento_nome = arquivo.name;
      data.documento_content_type = arquivo.type;
      data.documento_base64 = await fileToBase64(arquivo);
    }
    await api(`/v1/admin/juridico/prazos/${prazoId}/entregas`, { method: "POST", body: JSON.stringify(data) });
    deliveryForm.reset();
    deliveryForm.elements.prazo_id.value = prazoId;
    showMessage("Entrega registrada com evidência auditável.", "success");
    await Promise.all([loadDeliveryDocuments(), loadDashboard()]);
  } catch (error) { showMessage(error.message); }
});
confirmDeadlineForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(confirmDeadlineForm));
  const prazoId = data.prazo_id;
  try {
    await updateDeadline(prazoId, { confirmar: true, confirmacao_observacoes: data.confirmacao_observacoes.trim(), descricao_evento: "Prazo sugerido pela RPI conferido e confirmado pelo operador" });
    confirmDeadlineDialog.close();
    showMessage("Prazo confirmado.", "success");
  } catch (error) { showMessage(error.message); }
});

Promise.all([loadReferences(), loadDashboard()])
  .then(() => Promise.all([loadLegalPolicy(), loadGovernance()]))
  .catch((error) => showMessage(error.message));

document.querySelector("#legal-policy-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    exigir_responsavel_confirmacao: form.elements.exigir_responsavel_confirmacao.checked,
    exigir_checklist_conclusao: form.elements.exigir_checklist_conclusao.checked,
    exigir_evidencia_conclusao: form.elements.exigir_evidencia_conclusao.checked,
    exigir_segunda_pessoa_critico: form.elements.exigir_segunda_pessoa_critico.checked,
    margem_operacional_dias: Number(form.elements.margem_operacional_dias.value || 0),
  };
  try {
    await api("/v1/admin/juridico/politica", { method: "PUT", body: JSON.stringify(payload) });
    showMessage("Política jurídica salva e marcos operacionais recalculados.", "success");
    await Promise.all([loadLegalPolicy(), loadDashboard()]);
  } catch (error) { showMessage(error.message, "error"); }
});

document.querySelector("#legal-rule-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  payload.dias_prazo = Number(payload.dias_prazo);
  payload.vigencia_fim = payload.vigencia_fim || null;
  payload.checklist = splitLines(payload.checklist);
  payload.evidencias_exigidas = splitLines(payload.evidencias_exigidas);
  try {
    await api("/v1/admin/juridico/regras-prazo", { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    showMessage("Nova versão da regra cadastrada com trilha de aprovação.", "success");
    await loadGovernance();
  } catch (error) { showMessage(error.message, "error"); }
});

document.querySelector("#legal-calendar-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api("/v1/admin/juridico/calendario/excecoes", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    form.reset();
    showMessage("Exceção oficial adicionada ao calendário jurídico.", "success");
    await loadGovernance();
  } catch (error) { showMessage(error.message, "error"); }
});

document.querySelector("#legal-reprocess-preview").addEventListener("submit", async (event) => {
  event.preventDefault();
  const regraId = Number(event.currentTarget.elements.regra_id.value);
  const resultBox = document.querySelector("#legal-reprocess-result");
  const confirmForm = document.querySelector("#legal-reprocess-confirm");
  if (!regraId) return;
  try {
    const result = await api(`/v1/admin/juridico/motor/reprocessamento?regra_id=${regraId}&limite=500`);
    legalState.reprocessPreview = result;
    resultBox.hidden = false;
    resultBox.innerHTML = `<strong>${result.quantidade} publicação(ões) elegível(is)</strong><p>Despacho ${escapeHtml(result.regra.codigo_despacho)} · ${escapeHtml(result.regra.acao)}.${result.ha_mais ? " Há mais registros; esta execução ficará limitada a 500." : ""}</p>`;
    confirmForm.hidden = result.quantidade === 0;
    confirmForm.reset();
  } catch (error) { showMessage(error.message, "error"); }
});

document.querySelector("#legal-reprocess-preview").elements.regra_id.addEventListener("change", () => {
  document.querySelector("#legal-reprocess-result").hidden = true;
  document.querySelector("#legal-reprocess-confirm").hidden = true;
  legalState.reprocessPreview = null;
});

document.querySelector("#legal-reprocess-confirm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const preview = legalState.reprocessPreview;
  if (!preview?.quantidade) return;
  const data = Object.fromEntries(new FormData(event.currentTarget));
  const payload = {
    regra_id: preview.regra.id,
    limite: preview.limite,
    justificativa: data.justificativa,
    confirmacao: data.confirmacao,
  };
  try {
    const result = await api("/v1/admin/juridico/motor/reprocessamento", { method: "POST", body: JSON.stringify(payload) });
    showMessage(`Reprocessamento concluído: ${result.avaliacoes_liberadas} avaliação(ões) liberada(s) e ${result.resultado_motor.prazos_sugeridos || 0} novo(s) prazo(s) sugerido(s).`, "success");
    event.currentTarget.hidden = true;
    document.querySelector("#legal-reprocess-result").hidden = true;
    legalState.reprocessPreview = null;
    await Promise.all([loadDashboard(), loadLegalPolicy(), loadGovernance()]);
  } catch (error) { showMessage(error.message, "error"); }
});

/* ===== Checklist + Kanban ===== */
const KANBAN_COLUNAS = [
  ["aguardando_confirmacao", "Aguardando"],
  ["pendente", "Pendente"],
  ["em_andamento", "Em andamento"],
  ["historico", "Histórico"],
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
  const draggable = legalState.canManage && !["concluido", "cancelado", "dispensado", "historico", "duplicado"].includes(item.status);
  return `<article class="kanban-card ${deadlineClass(item)}" draggable="${draggable}" data-id="${item.id}" data-status="${item.status}">
    <div class="kanban-card-badges"><span class="legal-badge">${escapeHtml(item.tipo_nome)}</span>${!item.confirmado ? '<span class="legal-badge critical">Sugestão</span>' : ""}${checklistBadge(item.id)}</div>
    <h4>${escapeHtml(item.titulo)}</h4>
    <p>${escapeHtml(item.numero)} · ${escapeHtml(item.marca || "—")}</p>
    <small>${item.historico ? "Prazo original" : "Vence"} ${dateOnly.format(new Date(item.vencimento_em))} · ${escapeHtml(item.responsavel || "sem responsável")}</small>
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

function renderCalendar(items) {
  const grupos = new Map();
  for (const item of items) {
    const chave = new Date(item.vencimento_em).toISOString().slice(0, 10);
    if (!grupos.has(chave)) grupos.set(chave, []);
    grupos.get(chave).push(item);
  }
  document.querySelector("#legal-calendar").innerHTML = [...grupos.entries()].map(([dia, prazos]) => `
    <article class="legal-calendar-day">
      <header><strong>${dateOnly.format(new Date(`${dia}T12:00:00`))}</strong><span>${prazos.length} prazo(s)</span></header>
      ${prazos.map((item) => `<div class="legal-calendar-item ${deadlineClass(item)}"><span class="legal-badge">${escapeHtml(item.tipo_nome)}</span><strong>${escapeHtml(item.titulo)}</strong><small>${escapeHtml(item.numero)} · ${escapeHtml(item.responsavel || "Não atribuído")} · ${escapeHtml(item.prioridade)}</small></div>`).join("")}
    </article>`).join("") || '<div class="legal-empty">Nenhum prazo no período selecionado.</div>';
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
  const calendar = view === "calendar";
  document.querySelector("#legal-deadlines").hidden = !list;
  document.querySelector("#legal-kanban").hidden = list || calendar;
  document.querySelector("#legal-calendar").hidden = !calendar;
  document.querySelector("#view-list").classList.toggle("active", list);
  document.querySelector("#view-kanban").classList.toggle("active", view === "kanban");
  document.querySelector("#view-calendar").classList.toggle("active", calendar);
  document.querySelector("#view-list").setAttribute("aria-pressed", String(list));
  document.querySelector("#view-kanban").setAttribute("aria-pressed", String(view === "kanban"));
  document.querySelector("#view-calendar").setAttribute("aria-pressed", String(calendar));
}
document.querySelector("#view-list").addEventListener("click", () => setLegalView("list"));
document.querySelector("#view-kanban").addEventListener("click", () => setLegalView("kanban"));
document.querySelector("#view-calendar").addEventListener("click", () => setLegalView("calendar"));

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
  setBarWidth(document.querySelector("#checklist-bar"), pct);
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

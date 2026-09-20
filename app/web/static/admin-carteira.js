const state = { attorney: null, selected: new Set(), selectedPortfolio: new Set(), companies: [], owners: [], view: "list", offset: 0, pageSize: 20, draggedId: null };
const KANBAN_STAGES = [
  ["triagem", "Novo / Triagem"], ["aguardando_documentos", "Aguardando documentos"],
  ["documentacao_gru", "Documentação e GRU"], ["protocolado", "Protocolado"],
  ["aguardando_inpi", "Aguardando INPI"], ["exigencia_recurso", "Exigência / Recurso"],
  ["deferido_concessao", "Deferido / Concessão"], ["encerrado", "Encerrado"],
];
const message = document.querySelector("#portfolio-message");

function escapeHtml(value) {
  const el = document.createElement("span"); el.textContent = value ?? ""; return el.innerHTML;
}
function formatDate(value) { return value ? new Intl.DateTimeFormat("pt-BR").format(new Date(`${value}T12:00:00`)) : "—"; }
function readableError(detail, fallback = "Não foi possível concluir a operação.") {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map(item => readableError(item, "")).filter(Boolean);
    return messages.join(" · ") || fallback;
  }
  if (detail && typeof detail === "object") {
    return readableError(detail.message || detail.msg || detail.detail, fallback);
  }
  return fallback;
}
function showMessage(text, kind = "success") {
  message.hidden = false;
  message.textContent = readableError(text, kind === "error" ? "Não foi possível carregar os processos monitorados." : "Operação concluída.");
  message.className = `status-message ${kind}`;
}
async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options); const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(readableError(data.detail, `Operação não concluída (${response.status})`)); return data;
}
function payloadContext(companyId, ownerId) {
  return { empresa_nome: document.querySelector(companyId).value.trim() || null, responsavel_id: Number(document.querySelector(ownerId).value) || null };
}

async function loadReferences() {
  [state.companies, state.owners] = await Promise.all([api("/v1/admin/carteira/empresas"), api("/v1/admin/carteira/responsaveis")]);
  document.querySelector("#company-suggestions").replaceChildren(...state.companies.map(item => { const option = document.createElement("option"); option.value = item.nome; return option; }));
  const options = state.owners.map(item => `<option value="${item.id}">${escapeHtml(item.nome)}</option>`).join("");
  document.querySelector("#attorney-owner").insertAdjacentHTML("beforeend", options);
  document.querySelector("#manual-owner").insertAdjacentHTML("beforeend", options);
  document.querySelector("#import-owner").insertAdjacentHTML("beforeend", options);
  document.querySelector("#bulk-owner").insertAdjacentHTML("beforeend", options);
}

async function loadRpiStatus() {
  // Achado do usuário (20/09/2026): "Atualizar situação de todos" e
  // "Atualizar status" só reprocessam despachos já importados -- não
  // buscam nada novo no INPI. Sem isso, o atendimento não tinha como saber
  // se a base está desatualizada antes de clicar em "Atualizar".
  const el = document.querySelector("#rpi-status");
  let status;
  try { status = await api("/v1/admin/carteira/rpi-status"); }
  catch { return; }
  if (status.ultima_rpi_local == null) return;
  el.hidden = false;
  if (status.em_dia) {
    el.textContent = `Base sincronizada até a RPI ${status.ultima_rpi_local}.`;
    el.className = "rpi-status-indicator is-em-dia";
  } else {
    el.textContent = `Base desatualizada: última RPI importada é a ${status.ultima_rpi_local}, já saiu a ${status.ultima_rpi_oficial} (${status.edicoes_atraso} edição(ões) de atraso). "Atualizar" reprocessa só o que já foi importado -- não busca isso sozinho.`;
    el.className = "rpi-status-indicator is-atrasado";
  }
}

function renderMetrics(summary) {
  const form = document.querySelector("#portfolio-filter");
  const currentStatus = form.elements.status.value;
  const currentSituation = form.elements.situacao_inpi.value;
  const entries = [
    [summary.total, "Total monitorado", "all", ""],
    [summary.deferidos, "Processos deferidos", "situacao_inpi", "deferido"],
    [summary.pausados, "Processos pausados", "status", "pausado"],
    [summary.arquivados_extintos, "Processos arquivados/extintos", "situacao_inpi", "encerrado"],
    [summary.indeferidos, "Processos indeferidos", "situacao_inpi", "indeferido"],
    [summary.registros_concedidos, "Registros concedidos", "situacao_inpi", "registrado"],
    [summary.em_tramitacao, "Em tramitação", "situacao_inpi", "em_tramitacao"],
  ];
  document.querySelector("#portfolio-metrics").innerHTML = entries.map(([value, label, filter, filterValue]) => {
    const active = filter === "all"
      ? !currentStatus && !currentSituation
      : (filter === "status" ? currentStatus : currentSituation) === filterValue;
    return `<button class="portfolio-metric ${active ? "is-active" : ""}" type="button" data-metric-filter="${filter}" data-metric-value="${filterValue}" aria-pressed="${active}"><strong>${value}</strong><span>${label}</span></button>`;
  }).join("");
}

function applyMetricFilter(filter, value) {
  const form = document.querySelector("#portfolio-filter");
  form.elements.busca.value = "";
  form.elements.status.value = "";
  form.elements.situacao_inpi.value = "";
  if (filter !== "all") form.elements[filter].value = value;
  state.offset = 0;
  selectView("list");
}
function statusOptions(current) {
  return [["ativo","Ativo"],["pausado","Pausado"],["encerrado","Encerrado"],["arquivado","Arquivado"]].map(([value,label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("");
}
function inpiBadge(item) {
  const key = item.grupo_situacao_inpi || "revisar";
  const label = item.grupo_situacao_inpi_nome || "Revisar classificação";
  return `<span class="inpi-status-badge is-${escapeHtml(key)}">${escapeHtml(label)}</span>`;
}
function updateBulkBar() {
  const count = state.selectedPortfolio.size;
  const total = document.querySelectorAll("#portfolio-list [data-select-id]").length;
  document.querySelector("#portfolio-bulk-count").textContent = count ? `${count} selecionado(s)` : "Nenhum selecionado";
  document.querySelector("#bulk-assign-apply").disabled = count === 0;
  document.querySelector("#portfolio-select-page").checked = total > 0 && count === total;
}
function renderPortfolio(data) {
  renderMetrics(data.resumo);
  // Achado do usuário (20/09/2026): atribuir empresa/responsável a
  // processos já monitorados só existia um por um (PATCH /{id}) -- muitos
  // ficavam "Não atribuído"/"Sem empresa vinculada" numa carteira grande.
  // Seleção reseta a cada carregamento da lista (filtro, página ou troca de
  // view), então sempre corresponde só ao que está visível na tela.
  state.selectedPortfolio = new Set();
  const target = document.querySelector("#portfolio-list");
  if (!data.itens.length) { target.innerHTML = `<div class="portfolio-empty">Nenhum processo encontrado nesta carteira.</div>`; renderPagination(data); updateBulkBar(); return; }
  target.innerHTML = data.itens.map(item => {
    const movement = item.ultima_movimentacao;
    return `<article class="portfolio-item" data-id="${item.id}">
      <div class="portfolio-process"><label class="portfolio-select-cell"><input type="checkbox" data-select-id="${item.id}" aria-label="Selecionar processo ${escapeHtml(item.numero)}"></label><a class="process-number" href="/processos/${encodeURIComponent(item.numero)}" target="_blank" rel="noopener">${escapeHtml(item.numero)}</a><h3>${escapeHtml(item.titulo_exibicao || item.titulo || "Título não informado pelo INPI")}</h3><p>${escapeHtml(item.empresa || "Sem empresa vinculada")} · ${escapeHtml(item.procurador || "Procurador não informado")}</p><small>Depósito: ${formatDate(item.data_deposito)} · origem: ${escapeHtml(item.origem)}</small>${item.lead_id ? `<small class="portfolio-lead-link">Lead de origem: ${escapeHtml(item.lead_marca || "#" + item.lead_id)}</small>` : ""}</div>
      <div class="portfolio-inpi"><span class="portfolio-item-label">Situação no INPI</span>${inpiBadge(item)}<strong>${escapeHtml(item.situacao || "Não informada")}</strong><p><span>Responsável</span>${escapeHtml(item.responsavel || "Não atribuído")}</p></div>
      <div class="latest">${movement ? `<small>Última movimentação · RPI ${movement.numero_rpi}</small><strong>${formatDate(movement.data)}</strong><p>${escapeHtml(movement.descricao || "")}</p>` : `<small>Movimentações</small><strong>Nenhuma localizada</strong>`}</div>
      <div class="portfolio-status"><label><span class="portfolio-item-label">Status interno</span><select data-status>${statusOptions(item.status)}</select></label><label><span class="portfolio-item-label">Procurador</span><input data-procurador type="text" value="${escapeHtml(item.procurador || "")}" placeholder="Não informado" maxlength="500"></label><button class="primary-button" data-save-status type="button">Salvar</button><button class="secondary-button" data-atualizar type="button">Atualizar status</button><button class="secondary-button" data-relatorio type="button">Gerar relatório</button></div>
    </article>`;
  }).join("");
  renderPagination(data);
  updateBulkBar();
}
function renderPagination(data) {
  const nav = document.querySelector("#portfolio-pagination");
  const totalPages = Math.max(1, Math.ceil(data.total / state.pageSize));
  const currentPage = Math.floor(state.offset / state.pageSize) + 1;
  nav.hidden = false;
  document.querySelector("#portfolio-page-summary").textContent = `Página ${currentPage} de ${totalPages} · ${data.total} processo(s)`;
  document.querySelector("#portfolio-prev").disabled = state.offset === 0;
  document.querySelector("#portfolio-next").disabled = !data.tem_mais;
}
function stageOptions(current) {
  return KANBAN_STAGES.map(([value, label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("");
}
function renderKanban(data) {
  const target = document.querySelector("#portfolio-kanban");
  const official = data.modo === "inpi";
  target.classList.toggle("is-inpi", official);
  target.innerHTML = `${official ? `<p class="kanban-source">${escapeHtml(data.fonte)}. A organização é automática e não altera o fluxo interno do escritório.</p>` : ""}` + data.colunas.map(column => `<section class="kanban-column ${official ? `is-${column.chave}` : ""}" data-stage="${column.chave}">
    <header><h3>${escapeHtml(column.titulo)}</h3><span>${column.total}</span></header>
    <div class="kanban-dropzone">
      ${column.itens.map(item => `<article class="kanban-card" draggable="${official ? "false" : "true"}" data-id="${item.id}">
        <a href="/processos/${encodeURIComponent(item.numero)}" target="_blank" rel="noopener">${escapeHtml(item.numero)}</a>
        <h4>${escapeHtml(item.titulo_exibicao || item.titulo || "Título não informado pelo INPI")}</h4>
        <p>${escapeHtml(item.empresa || "Sem empresa vinculada")}</p>
        <dl><div><dt>INPI</dt><dd>${escapeHtml(item.situacao || "Não informada")}</dd></div><div><dt>Responsável</dt><dd>${escapeHtml(item.responsavel || "Não atribuído")}</dd></div></dl>
        ${item.ultima_movimentacao ? `<small>RPI ${item.ultima_movimentacao.numero_rpi} · ${formatDate(item.ultima_movimentacao.data)}</small>` : ""}
        ${official ? '<span class="official-source">Classificação automática pela RPI</span>' : `<label><span>Mover para</span><select data-move-stage>${stageOptions(item.etapa_kanban)}</select></label>`}
      </article>`).join("") || '<p class="kanban-empty">Arraste um processo para esta etapa.</p>'}
      ${column.tem_mais ? `<p class="kanban-more">Mostrando 20 de ${column.total}. Use a Lista para ver todos.</p>` : ""}
    </div>
  </section>`).join("");
}
async function loadPortfolio() {
  const params = new URLSearchParams(new FormData(document.querySelector("#portfolio-filter")));
  [...params.entries()].forEach(([key, value]) => {
    if (!String(value).trim()) params.delete(key);
  });
  // Achado do usuário (20/09/2026): só existia relatório em PDF processo por
  // processo, nada pra exportar a carteira inteira ou um filtro de uma vez
  // -- o link de exportação sempre reflete o filtro atualmente aplicado.
  const exportParams = new URLSearchParams(params);
  exportParams.delete("limite");
  exportParams.delete("deslocamento");
  document.querySelector("#export-csv").href = `/v1/admin/carteira/exportar.csv?${exportParams}`;
  if (state.view === "kanban") {
    const data = await api(`/v1/admin/carteira/kanban?${params}`); renderKanban(data); return;
  }
  if (state.view === "inpi") {
    const data = await api(`/v1/admin/carteira/kanban-inpi?${params}`); renderKanban(data); return;
  }
  params.set("limite", state.pageSize); params.set("deslocamento", state.offset);
  const data = await api(`/v1/admin/carteira?${params}`); renderPortfolio(data);
}

async function moveKanbanCard(id, etapa) {
  await api(`/v1/admin/carteira/${id}`, { method: "PATCH", body: JSON.stringify({ etapa_kanban: etapa }) });
  showMessage("Etapa do processo atualizada."); await loadPortfolio();
}

function renderAttorney(data) {
  state.attorney = data; state.selected.clear();
  document.querySelector("#attorney-result").hidden = false;
  document.querySelector("#attorney-total").innerHTML = `<strong>${data.total_processos} processo${data.total_processos === 1 ? "" : "s"}</strong><span>${data.total_titulares} titular${data.total_titulares === 1 ? "" : "es"}/cliente${data.total_titulares === 1 ? "" : "s"} identificado${data.total_titulares === 1 ? "" : "s"}</span>`;
  document.querySelector("#attorney-variants").innerHTML = data.variacoes.length ? data.variacoes.map(item => `<span>${escapeHtml(item.nome)} <b>${item.processos_na_pagina}</b></span>`).join("") : "<small>Nenhuma variação disponível.</small>";
  const coverage = data.cobertura || {};
  document.querySelector("#attorney-coverage").textContent = `${coverage.primeira_rpi && coverage.ultima_rpi ? `Cobertura sincronizada: RPI ${coverage.primeira_rpi} a ${coverage.ultima_rpi}. ` : ""}${coverage.aviso || ""}`;
  document.querySelector("#attorney-rows").innerHTML = data.itens.map(item => `<tr>
    <td><input type="checkbox" data-process-id="${item.processo_id}" ${item.monitorado_id ? "disabled" : ""}></td>
    <td><a href="/processos/${encodeURIComponent(item.numero)}" target="_blank" rel="noopener">${escapeHtml(item.numero)}</a><br><small>${escapeHtml(item.fonte)}</small></td>
    <td><strong>${escapeHtml(item.titulo_exibicao || item.titulo || "Título não informado pelo INPI")}</strong><br><small>${escapeHtml(item.procurador || "")}</small></td>
    <td>${escapeHtml(item.situacao || "Não informada")}</td><td>${formatDate(item.data_deposito)}</td>
    <td>${item.monitorado_id ? '<span class="role-badge">Já monitorado</span>' : "Disponível"}</td></tr>`).join("");
}

document.querySelector("#attorney-form").addEventListener("submit", async event => {
  event.preventDefault(); const values = new FormData(event.currentTarget);
  try { const data = await api(`/v1/admin/carteira/buscar-procurador?procurador=${encodeURIComponent(values.get("procurador"))}&modo=${values.get("modo")}&limite=100`); data.procurador = values.get("procurador"); data.modo = values.get("modo"); renderAttorney(data); }
  catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#attorney-rows").addEventListener("change", event => {
  const checkbox = event.target.closest("[data-process-id]"); if (!checkbox) return;
  const id = Number(checkbox.dataset.processId); checkbox.checked ? state.selected.add(id) : state.selected.delete(id);
});
document.querySelector("#select-page").addEventListener("change", event => {
  document.querySelectorAll("#attorney-rows [data-process-id]:not(:disabled)").forEach(box => { box.checked = event.target.checked; const id = Number(box.dataset.processId); box.checked ? state.selected.add(id) : state.selected.delete(id); });
});
document.querySelector("#link-selected").addEventListener("click", async () => {
  if (!state.selected.size) { showMessage("Selecione ao menos um processo disponível.", "error"); return; }
  try { const result = await api("/v1/admin/carteira/vincular-lote", { method: "POST", body: JSON.stringify({ processo_ids: [...state.selected], ...payloadContext("#attorney-company", "#attorney-owner") }) }); showMessage(`${result.vinculados} processo(s) vinculado(s); ${result.ja_vinculados} já estavam na carteira.`); document.querySelector("#attorney-form").requestSubmit(); await loadPortfolio(); }
  catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#link-all").addEventListener("click", async () => {
  if (!state.attorney || !confirm(`Vincular até ${state.attorney.total_processos} processo(s) encontrados?`)) return;
  try { const result = await api("/v1/admin/carteira/vincular-procurador", { method: "POST", body: JSON.stringify({ procurador: state.attorney.procurador, modo: state.attorney.modo, ...payloadContext("#attorney-company", "#attorney-owner") }) }); showMessage(`${result.vinculados} processo(s) incluído(s); ${result.ja_vinculados} já estavam monitorados.`); document.querySelector("#attorney-form").requestSubmit(); await loadPortfolio(); }
  catch (error) { showMessage(error.message, "error"); }
});

const dialog = document.querySelector("#manual-dialog");
document.querySelector("#open-manual").addEventListener("click", () => dialog.showModal());
document.querySelector("#close-manual").addEventListener("click", () => dialog.close());
document.querySelector("#cancel-manual").addEventListener("click", () => dialog.close());
document.querySelector("#manual-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form));
  try {
    const result = await api("/v1/admin/carteira/manual", { method: "POST", body: JSON.stringify({ ...values, responsavel_id: Number(values.responsavel_id) || null, empresa_nome: values.empresa_nome || null, observacoes: values.observacoes || null }) });
    if (result.status === "pendente") showMessage(result.mensagem || `Processo ${result.numero} aguardando publicação na RPI.`);
    else if (result.alertas_conflito_interesse && result.alertas_conflito_interesse.length) {
      // Achado FASE-A da auditoria do CRM (05/09/2026): alerta nao bloqueante --
      // o vinculo ja foi criado, isto so avisa o operador para ele conferir.
      const itens = result.alertas_conflito_interesse.map(a => {
        const rotulo = a.tipo === "titular_outro_cliente" ? `"${a.nome_encontrado}" é titular de um processo já monitorado para ${a.empresa_nome || "outro cliente"}` : `"${a.nome_encontrado}" já é cliente cadastrado (${a.empresa_nome})`;
        return `<li>${escapeHtml(rotulo)}</li>`;
      }).join("");
      message.hidden = false; message.className = "status-message warning";
      message.innerHTML = `<strong>Processo ${escapeHtml(result.numero)} adicionado à carteira.</strong> Possível conflito de interesse encontrado — confira antes de prosseguir:<ul>${itens}</ul>`;
    }
    else showMessage(result.vinculados ? `Processo ${result.numero} adicionado à carteira.` : `Processo ${result.numero} já estava na carteira.`);
    dialog.close(); form.reset(); await Promise.all([loadPortfolio(), loadPreCadastros()]);
  }
  catch (error) { showMessage(error.message, "error"); }
});

async function loadPreCadastros() {
  const section = document.querySelector("#pre-cadastros-section");
  const tbody = document.querySelector("#pre-cadastros-rows");
  let itens;
  try { itens = await api("/v1/admin/carteira/pre-cadastros"); }
  catch { section.hidden = true; return; }
  section.hidden = itens.length === 0;
  tbody.innerHTML = itens.map(item => `
    <tr data-id="${item.id}">
      <td>${escapeHtml(item.numero)}</td>
      <td>${escapeHtml(item.titular)}</td>
      <td>${escapeHtml(item.empresa || "—")}</td>
      <td>${escapeHtml(item.responsavel_nome || "Não atribuído")}</td>
      <td>${escapeHtml(item.criado_por)}</td>
      <td>${new Date(item.criado_em).toLocaleDateString("pt-BR")}</td>
      <td><button class="secondary-button cancel-pre-cadastro" type="button">Cancelar</button></td>
    </tr>`).join("");
}
document.querySelector("#pre-cadastros-rows").addEventListener("click", async event => {
  const button = event.target.closest(".cancel-pre-cadastro"); if (!button) return;
  const id = button.closest("tr").dataset.id;
  if (!confirm("Cancelar este pré-cadastro? Ele não será mais vinculado automaticamente quando a RPI publicar.")) return;
  try { await api(`/v1/admin/carteira/pre-cadastros/${id}/cancelar`, { method: "POST" }); await loadPreCadastros(); }
  catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#portfolio-filter").addEventListener("submit", event => { event.preventDefault(); state.offset = 0; loadPortfolio().catch(error => showMessage(error.message, "error")); });
document.querySelector("#update-all").addEventListener("click", async event => {
  if (!confirm("Reconsolidar a situação no INPI de toda a carteira ativa a partir dos despachos já sincronizados? Isso pode levar alguns instantes para carteiras grandes.")) return;
  const button = event.currentTarget; button.disabled = true; button.textContent = "Atualizando…";
  try {
    const result = await api("/v1/admin/carteira/atualizar-lote", { method: "POST", body: JSON.stringify({}) });
    showMessage(`${result.verificados} processo(s) verificado(s); ${result.alterados} com situação atualizada.`);
    await loadPortfolio();
  } catch (error) { showMessage(error.message, "error"); }
  finally { button.disabled = false; button.textContent = "Atualizar situação de todos"; }
});
document.querySelector("#portfolio-metrics").addEventListener("click", event => {
  const card = event.target.closest("[data-metric-filter]"); if (!card) return;
  applyMetricFilter(card.dataset.metricFilter, card.dataset.metricValue);
});
function selectView(view) {
  state.view = view; state.offset = 0;
  const list = view === "list";
  document.querySelector("#portfolio-list").hidden = !list;
  document.querySelector("#portfolio-bulk-assign").hidden = !list;
  document.querySelector("#portfolio-pagination").hidden = !list;
  document.querySelector("#portfolio-kanban").hidden = list;
  ["list", "kanban", "inpi"].forEach(name => {
    const button = document.querySelector(`#portfolio-view-${name}`);
    const active = name === view; button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", active);
  });
  loadPortfolio().catch(error => showMessage(error.message, "error"));
}
document.querySelector("#portfolio-view-list").addEventListener("click", () => selectView("list"));
document.querySelector("#portfolio-view-kanban").addEventListener("click", () => selectView("kanban"));
document.querySelector("#portfolio-view-inpi").addEventListener("click", () => selectView("inpi"));
document.querySelector("#portfolio-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.pageSize); loadPortfolio().catch(error => showMessage(error.message, "error")); });
document.querySelector("#portfolio-next").addEventListener("click", () => { state.offset += state.pageSize; loadPortfolio().catch(error => showMessage(error.message, "error")); });
document.querySelector("#portfolio-kanban").addEventListener("change", event => {
  const select = event.target.closest("[data-move-stage]"); if (!select) return;
  const card = select.closest("[data-id]"); moveKanbanCard(card.dataset.id, select.value).catch(error => showMessage(error.message, "error"));
});
document.querySelector("#portfolio-kanban").addEventListener("dragstart", event => {
  const card = event.target.closest("[data-id]"); if (!card) return;
  state.draggedId = card.dataset.id; card.classList.add("is-dragging"); event.dataTransfer.effectAllowed = "move";
});
document.querySelector("#portfolio-kanban").addEventListener("dragend", event => {
  event.target.closest("[data-id]")?.classList.remove("is-dragging"); state.draggedId = null;
  document.querySelectorAll(".kanban-column.is-over").forEach(column => column.classList.remove("is-over"));
});
document.querySelector("#portfolio-kanban").addEventListener("dragover", event => {
  const column = event.target.closest("[data-stage]"); if (!column) return;
  event.preventDefault(); event.dataTransfer.dropEffect = "move"; column.classList.add("is-over");
});
document.querySelector("#portfolio-kanban").addEventListener("dragleave", event => {
  const column = event.target.closest("[data-stage]"); if (column && !column.contains(event.relatedTarget)) column.classList.remove("is-over");
});
document.querySelector("#portfolio-kanban").addEventListener("drop", event => {
  const column = event.target.closest("[data-stage]"); if (!column || !state.draggedId) return;
  event.preventDefault(); column.classList.remove("is-over");
  moveKanbanCard(state.draggedId, column.dataset.stage).catch(error => showMessage(error.message, "error"));
});
document.querySelector("#portfolio-list").addEventListener("change", event => {
  const checkbox = event.target.closest("[data-select-id]"); if (!checkbox) return;
  const id = Number(checkbox.dataset.selectId);
  checkbox.checked ? state.selectedPortfolio.add(id) : state.selectedPortfolio.delete(id);
  updateBulkBar();
});
document.querySelector("#portfolio-select-page").addEventListener("change", event => {
  document.querySelectorAll("#portfolio-list [data-select-id]").forEach(box => {
    box.checked = event.target.checked;
    const id = Number(box.dataset.selectId);
    box.checked ? state.selectedPortfolio.add(id) : state.selectedPortfolio.delete(id);
  });
  updateBulkBar();
});
document.querySelector("#bulk-assign-apply").addEventListener("click", async () => {
  const empresaNome = document.querySelector("#bulk-company").value.trim() || null;
  const responsavelValor = document.querySelector("#bulk-owner").value;
  const responsavelId = responsavelValor ? Number(responsavelValor) : null;
  if (!empresaNome && !responsavelId) { showMessage("Informe uma empresa e/ou um responsável para atribuir.", "error"); return; }
  const button = document.querySelector("#bulk-assign-apply"); button.disabled = true; button.textContent = "Atribuindo…";
  try {
    const result = await api("/v1/admin/carteira/atribuir-lote", { method: "POST", body: JSON.stringify({ monitorado_ids: [...state.selectedPortfolio], empresa_nome: empresaNome, responsavel_id: responsavelId }) });
    showMessage(`${result.atribuidos} processo(s) atualizado(s).`);
    document.querySelector("#bulk-company").value = "";
    document.querySelector("#bulk-owner").value = "";
    await loadPortfolio();
  } catch (error) { showMessage(error.message, "error"); }
  finally { button.disabled = false; button.textContent = "Atribuir aos selecionados"; }
});
document.querySelector("#portfolio-list").addEventListener("click", async event => {
  const button = event.target.closest("[data-save-status]"); if (!button) return; const card = button.closest("[data-id]"); button.disabled = true;
  try { await api(`/v1/admin/carteira/${card.dataset.id}`, { method: "PATCH", body: JSON.stringify({ status: card.querySelector("[data-status]").value, procurador: card.querySelector("[data-procurador]").value }) }); showMessage("Carteira atualizada."); await loadPortfolio(); }
  catch (error) { showMessage(error.message, "error"); button.disabled = false; }
});
document.querySelector("#portfolio-list").addEventListener("click", async event => {
  const button = event.target.closest("[data-atualizar]"); if (!button) return; const card = button.closest("[data-id]"); button.disabled = true; button.textContent = "Atualizando…";
  try {
    const result = await api(`/v1/admin/carteira/${card.dataset.id}/atualizar`, { method: "POST" });
    const partes = [`Situação: ${result.situacao || "não informada"}`];
    if (result.cliente_cadastrado) partes.push(`Cliente cadastrado: ${result.cliente_cadastrado}`);
    showMessage(partes.join(" · "));
    await loadPortfolio();
  }
  catch (error) { showMessage(error.message, "error"); button.disabled = false; button.textContent = "Atualizar status"; }
});

// Achado do usuário (08/09/2026): relatório de acompanhamento em PDF pro
// cliente -- mostra situação no INPI, fase no escritório e histórico de
// movimentações da RPI, com um campo de observações digitado na hora
// (não fica salvo no cadastro interno do processo).
const reportDialog = document.querySelector("#report-dialog");
const reportForm = document.querySelector("#report-form");
document.querySelector("#portfolio-list").addEventListener("click", event => {
  const button = event.target.closest("[data-relatorio]"); if (!button) return;
  const card = button.closest("[data-id]");
  reportForm.reset();
  reportForm.dataset.monitoradoId = card.dataset.id;
  reportDialog.showModal();
});
document.querySelector("#close-report").addEventListener("click", () => reportDialog.close());
document.querySelector("#cancel-report").addEventListener("click", () => reportDialog.close());
reportForm.addEventListener("submit", async event => {
  event.preventDefault();
  const submitButton = document.querySelector("#report-submit");
  submitButton.disabled = true;
  submitButton.textContent = "Gerando…";
  try {
    const dados = Object.fromEntries(new FormData(reportForm));
    const response = await fetch(`/v1/admin/carteira/${reportForm.dataset.monitoradoId}/relatorio-pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ observacoes_relatorio: dados.observacoes_relatorio || null }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Não foi possível gerar o relatório.");
    }
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || "acompanhamento.pdf";
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    reportDialog.close();
    showMessage("Relatório gerado.", "success");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Gerar e baixar PDF";
  }
});

const importDialog = document.querySelector("#import-dialog");
const importResult = document.querySelector("#import-result");
document.querySelector("#open-import").addEventListener("click", () => {
  importResult.hidden = true;
  document.querySelector("#import-form").reset();
  importDialog.showModal();
});
document.querySelector("#close-import").addEventListener("click", () => importDialog.close());
document.querySelector("#cancel-import").addEventListener("click", () => importDialog.close());
document.querySelector("#import-form").addEventListener("submit", async event => {
  event.preventDefault();
  const file = document.querySelector("#import-file").files[0];
  if (!file) return;
  const submit = document.querySelector("#import-submit");
  submit.disabled = true;
  importResult.hidden = true;
  const body = new FormData();
  body.append("arquivo", file);
  const owner = document.querySelector("#import-owner").value;
  if (owner) body.append("responsavel_id", owner);
  try {
    const response = await fetch("/v1/admin/carteira/importar", { method: "POST", body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha na importação (${response.status})`);
    importResult.hidden = false;
    importResult.className = "import-result success";
    const naoEncontrados = data.exemplos_nao_encontrados || [];
    // Números inteiros do backend + números de processo passados por escapeHtml.
    importResult.innerHTML =
      `<strong>${data.vinculados} processo(s) importado(s).</strong>` +
      `<ul><li>${data.ja_vinculados} já estavam na carteira</li>` +
      `<li>${data.nao_encontrados} não localizado(s) na base RPI</li>` +
      `<li>${data.sem_numero} linha(s) sem número</li></ul>` +
      (naoEncontrados.length
        ? `<small>Não encontrados: ${naoEncontrados.map(escapeHtml).join(", ")}${data.nao_encontrados > naoEncontrados.length ? "…" : ""}</small>`
        : "");
    await loadPortfolio();
  } catch (error) {
    importResult.hidden = false;
    importResult.className = "import-result error";
    importResult.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

let suggestionTimer;
document.querySelector("#attorney-name").addEventListener("input", event => {
  clearTimeout(suggestionTimer); const value = event.target.value.trim(); if (value.length < 2) return;
  suggestionTimer = setTimeout(async () => { try { const names = await api(`/v1/admin/carteira/procuradores?busca=${encodeURIComponent(value)}`); document.querySelector("#attorney-suggestions").replaceChildren(...names.map(name => { const option = document.createElement("option"); option.value = name; return option; })); } catch {} }, 300);
});

Promise.all([loadReferences(), loadPortfolio(), loadPreCadastros(), loadRpiStatus()]).catch(error => showMessage(error.message, "error"));

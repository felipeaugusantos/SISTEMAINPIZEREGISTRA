const state = { attorney: null, selected: new Set(), companies: [], owners: [] };
const message = document.querySelector("#portfolio-message");

function escapeHtml(value) {
  const el = document.createElement("span"); el.textContent = value ?? ""; return el.innerHTML;
}
function formatDate(value) { return value ? new Intl.DateTimeFormat("pt-BR").format(new Date(`${value}T12:00:00`)) : "—"; }
function showMessage(text, kind = "success") { message.hidden = false; message.textContent = text; message.className = `status-message ${kind}`; }
async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options); const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Operação não concluída (${response.status})`); return data;
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
}

function renderMetrics(summary) {
  const entries = [[summary.total, "Total monitorado"], [summary.ativos, "Ativos"], [summary.pausados, "Pausados"], [summary.encerrados, "Encerrados"]];
  document.querySelector("#portfolio-metrics").innerHTML = entries.map(([value, label]) => `<article><strong>${value}</strong><span>${label}</span></article>`).join("");
}
function statusOptions(current) {
  return [["ativo","Ativo"],["pausado","Pausado"],["encerrado","Encerrado"],["arquivado","Arquivado"]].map(([value,label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("");
}
function renderPortfolio(data) {
  renderMetrics(data.resumo);
  const target = document.querySelector("#portfolio-list");
  if (!data.itens.length) { target.innerHTML = `<div class="portfolio-empty">Nenhum processo encontrado nesta carteira.</div>`; return; }
  target.innerHTML = data.itens.map(item => {
    const movement = item.ultima_movimentacao;
    return `<article class="portfolio-item" data-id="${item.id}">
      <div class="portfolio-process"><a class="process-number" href="/processos/${encodeURIComponent(item.numero)}" target="_blank" rel="noopener">${escapeHtml(item.numero)}</a><h3>${escapeHtml(item.titulo || "Marca sem título")}</h3><p>${escapeHtml(item.empresa || "Sem empresa vinculada")} · ${escapeHtml(item.procurador || "Procurador não informado")}</p><small>Depósito: ${formatDate(item.data_deposito)} · origem: ${escapeHtml(item.origem)}</small></div>
      <div class="portfolio-inpi"><span class="portfolio-item-label">Situação no INPI</span><strong>${escapeHtml(item.situacao || "Não informada")}</strong><p><span>Responsável</span>${escapeHtml(item.responsavel || "Não atribuído")}</p></div>
      <div class="latest">${movement ? `<small>Última movimentação · RPI ${movement.numero_rpi}</small><strong>${formatDate(movement.data)}</strong><p>${escapeHtml(movement.descricao || "")}</p>` : `<small>Movimentações</small><strong>Nenhuma localizada</strong>`}</div>
      <div class="portfolio-status"><label><span class="portfolio-item-label">Status interno</span><select data-status>${statusOptions(item.status)}</select></label><button class="secondary-button" data-save-status type="button">Salvar status</button></div>
    </article>`;
  }).join("");
}
async function loadPortfolio() {
  const params = new URLSearchParams(new FormData(document.querySelector("#portfolio-filter")));
  const data = await api(`/v1/admin/carteira?${params}`); renderPortfolio(data);
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
    <td><strong>${escapeHtml(item.titulo || "Sem título")}</strong><br><small>${escapeHtml(item.procurador || "")}</small></td>
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
  try { const result = await api("/v1/admin/carteira/manual", { method: "POST", body: JSON.stringify({ ...values, responsavel_id: Number(values.responsavel_id) || null, empresa_nome: values.empresa_nome || null, observacoes: values.observacoes || null }) }); showMessage(result.vinculados ? `Processo ${result.numero} adicionado à carteira.` : `Processo ${result.numero} já estava na carteira.`); dialog.close(); form.reset(); await loadPortfolio(); }
  catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#portfolio-filter").addEventListener("submit", event => { event.preventDefault(); loadPortfolio().catch(error => showMessage(error.message, "error")); });
document.querySelector("#portfolio-list").addEventListener("click", async event => {
  const button = event.target.closest("[data-save-status]"); if (!button) return; const card = button.closest("[data-id]"); button.disabled = true;
  try { await api(`/v1/admin/carteira/${card.dataset.id}`, { method: "PATCH", body: JSON.stringify({ status: card.querySelector("[data-status]").value }) }); showMessage("Status de acompanhamento atualizado."); await loadPortfolio(); }
  catch (error) { showMessage(error.message, "error"); button.disabled = false; }
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

Promise.all([loadReferences(), loadPortfolio()]).catch(error => showMessage(error.message, "error"));

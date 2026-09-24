const crmState = { offset: 0, limit: 10, total: 0, references: null, canManage: false, ultimoHistorico: null, atendimento: null, remindersOffset: 0, remindersLimit: 4, remindersTotal: 0 };
const kanbanState = { etapas: [], cards: [] };
const form = document.querySelector("#crm-filter");
const reminderFilter = document.querySelector("#crm-reminder-filter");
const reminderDialog = document.querySelector("#reminder-dialog");
const reminderForm = document.querySelector("#reminder-form");
const postponeDialog = document.querySelector("#postpone-dialog");
const postponeForm = document.querySelector("#postpone-form");
let postponeReminderId = null;
const crmMessage = document.querySelector("#crm-message");
const channels = { telefone: "Telefone", whatsapp: "WhatsApp", email: "E-mail", reuniao: "Reunião", outro: "Atendimento" };
const dateTime = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

function esc(value) { const node = document.createElement("span"); node.textContent = value ?? ""; return node.innerHTML; }
function show(text, kind = "error") { crmMessage.hidden = !text; crmMessage.textContent = text; crmMessage.className = `status-message ${kind}`; }
async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(" · ") : data.detail;
    throw new Error(detail || `Não foi possível concluir (${response.status})`);
  }
  return data;
}
function options(items, selected = "") { return items.map(item => `<option value="${esc(item.id)}"${String(item.id) === String(selected) ? " selected" : ""}>${esc(item.nome)}</option>`).join(""); }
function params() {
  const values = new FormData(form), query = new URLSearchParams();
  for (const [key, value] of values) {
    if (!value) continue;
    if (key === "inicio") query.set(key, new Date(`${value}T00:00:00`).toISOString());
    else if (key === "fim") query.set(key, new Date(`${value}T23:59:59.999`).toISOString());
    else query.set(key, value);
  }
  query.set("limite", crmState.limit); query.set("deslocamento", crmState.offset); return query;
}
function formatarHoras(horas) {
  if (!horas) return "—";
  if (horas < 1) return `${Math.round(horas * 60)}min`;
  if (horas < 24) return `${horas.toFixed(1)}h`;
  return `${(horas / 24).toFixed(1)}d`;
}
function renderMetrics(data) {
  const rows = [["Total filtrado", data.total], ["Ligações", data.por_canal.telefone || 0], ["WhatsApp", data.por_canal.whatsapp || 0], ["E-mails", data.por_canal.email || 0], ["Reuniões", data.por_canal.reuniao || 0], ["Atendimentos", data.por_canal.outro || 0]];
  if (crmState.atendimento) {
    rows.push(["Tempo médio até 1º contato", formatarHoras(crmState.atendimento.tempo_medio_primeiro_atendimento_horas)]);
    rows.push(["Leads sem atendimento", crmState.atendimento.leads_sem_atendimento]);
  }
  document.querySelector("#crm-metrics").innerHTML = rows.map(([label, value]) => `<article><span>${label}</span><strong>${value}</strong></article>`).join("");
}
function tempoDecorrido(iso) {
  const minutos = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutos < 60) return `há ${minutos}min`;
  const horas = Math.round(minutos / 60);
  if (horas < 24) return `há ${horas}h`;
  return `há ${Math.round(horas / 24)}d`;
}
function processoNaoVinculadoBadge() {
  return `<span class="crm-kanban-sla-badge" title="A fase avançou no CRM, mas nenhum processo do INPI está vinculado a este lead ainda — o portal do cliente não reflete esse avanço até vincular em Processos monitorados.">⚠ Processo não vinculado</span>`;
}
function renderKanban(data) {
  kanbanState.etapas = data.etapas || [];
  kanbanState.cards = data.cards || [];
  crmState.canManage = data.acoes?.gerenciar ?? crmState.canManage;
  const target = document.querySelector("#crm-kanban");
  if (!target) return;
  target.innerHTML = kanbanState.etapas.map(etapa => {
    const cards = kanbanState.cards
      .filter(card => card.etapa === etapa.id)
      .sort((a, b) => new Date(a.entrou_etapa_em) - new Date(b.entrou_etapa_em));
    const atrasados = cards.filter(card => card.atrasado).length;
    return `<section class="crm-kanban-column" data-etapa="${esc(etapa.id)}"><header><h3>${esc(etapa.label)}</h3><span class="crm-kanban-column-badges">${atrasados ? `<strong class="crm-kanban-atrasados" title="${atrasados} card(s) fora do SLA">${atrasados}</strong>` : ""}<strong>${cards.length}</strong></span></header><div class="crm-kanban-dropzone" data-etapa="${esc(etapa.id)}">${cards.length ? cards.map(card => `<article class="crm-kanban-card-item${card.atrasado ? " atrasado" : ""}" draggable="${crmState.canManage}" data-lead-id="${card.id}">${card.atrasado ? `<span class="crm-kanban-sla-badge">⚠ Fora do SLA · ${tempoDecorrido(card.entrou_etapa_em)}</span>` : ""}${card.processo_vinculado_pendente ? processoNaoVinculadoBadge() : ""}<div><strong>${esc(card.nome)}</strong>${card.empresa ? `<small>${esc(card.empresa)}</small>` : ""}</div><span>${esc(card.marca || "Interesse geral")}</span><small>${esc(card.responsavel || "Não atribuído")}</small>${card.proxima_acao_em ? `<time>Próxima ação: ${dateTime.format(new Date(card.proxima_acao_em))}</time>` : `<time class="kanban-no-action">Sem próxima ação</time>`}<a href="/admin/pesquisas?lead_id=${card.id}" data-open-contact="${card.id}">Abrir contato</a></article>`).join("") : `<p class="crm-kanban-empty">Nenhuma oportunidade</p>`}</div></section>`;
  }).join("");
  const semResponsavel = kanbanState.cards.filter(card => !card.responsavel).length;
  const botaoDistribuir = document.querySelector("#crm-kanban-distribuir");
  if (botaoDistribuir) {
    botaoDistribuir.hidden = !crmState.canManage || semResponsavel === 0;
    botaoDistribuir.textContent = `Distribuir sem responsável (${semResponsavel})`;
  }
  target.querySelectorAll(".crm-kanban-card-item[draggable='true']").forEach(card => card.addEventListener("dragstart", event => { event.dataTransfer.setData("text/plain", card.dataset.leadId); card.classList.add("dragging"); }));
  target.querySelectorAll(".crm-kanban-card-item").forEach(card => card.addEventListener("dragend", () => card.classList.remove("dragging")));
  target.querySelectorAll(".crm-kanban-dropzone").forEach(zone => {
    zone.addEventListener("dragover", event => { event.preventDefault(); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", async event => {
      event.preventDefault(); zone.classList.remove("drag-over");
      const leadId = event.dataTransfer.getData("text/plain");
      if (!leadId || !crmState.canManage) return;
      try { await api(`/v1/admin/leads/${leadId}/kanban`, { method: "POST", body: JSON.stringify({ etapa: zone.dataset.etapa }) }); await loadKanban(); await loadHistory(); }
      catch (error) { show(error.message); }
    });
  });
}
async function loadKanban() { renderKanban(await api("/v1/admin/leads-kanban")); }

const crmLeadDialog = window.createLeadDialog(document.querySelector("#lead-workspace"), {
  onChange: async () => { await Promise.all([loadKanban(), loadHistory()]); },
  onSummary: loadAtendimentoStats,
  onClose: () => { Promise.all([loadKanban(), loadHistory()]).catch(error => show(error.message)); },
});
document.addEventListener("click", evento => {
  const gatilho = evento.target.closest("[data-open-contact]");
  if (!gatilho || evento.button !== 0 || evento.ctrlKey || evento.metaKey || evento.shiftKey) return;
  evento.preventDefault();
  crmLeadDialog.openLead(Number(gatilho.dataset.openContact)).catch(error => show(error.message));
});
function agruparHistoricoPorCliente(itens) {
  const grupos = [];
  const indicePorLead = new Map();
  for (const item of itens) {
    if (!indicePorLead.has(item.lead_id)) {
      indicePorLead.set(item.lead_id, grupos.length);
      grupos.push({ lead_id: item.lead_id, cliente: item.cliente, empresa: item.empresa || item.marca, itens: [] });
    }
    grupos[indicePorLead.get(item.lead_id)].itens.push(item);
  }
  return grupos;
}
function renderHistory(data) {
  crmState.total = data.total; crmState.ultimoHistorico = data; renderMetrics(data);
  document.querySelector("#crm-total").textContent = `${data.total} registro${data.total === 1 ? "" : "s"}`;
  const target = document.querySelector("#crm-history");
  target.innerHTML = data.itens.length
    ? agruparHistoricoPorCliente(data.itens).map(grupo => `<li class="crm-client-group"><header class="crm-client-group-header"><span><strong>${esc(grupo.cliente)}</strong><small>${esc(grupo.empresa || "Cliente sem empresa")}</small></span><a href="/admin/pesquisas?lead_id=${grupo.lead_id}" data-open-contact="${grupo.lead_id}">Abrir</a></header><ol class="crm-client-group-items">${grupo.itens.map(item => `<li class="crm-entry"><time datetime="${esc(item.criado_em)}">${dateTime.format(new Date(item.criado_em))}</time><span class="crm-history-observation">${esc(item.observacao || item.resultado || "Sem observação")}</span><span class="crm-history-next">${item.proximo_contato ? dateTime.format(new Date(item.proximo_contato)) : "Sem próximo contato"}</span></li>`).join("")}</ol></li>`).join("")
    : `<li class="crm-empty"><strong>Nenhum atendimento foi encontrado.</strong><p>Revise nome, documento, telefone, status ou período informado.</p><a class="primary-button" href="/admin/pesquisas">Ir para Leads</a></li>`;
  const page = Math.floor(crmState.offset / crmState.limit) + 1;
  document.querySelector("#crm-page").textContent = `Página ${page}`;
  document.querySelector("#crm-prev").disabled = crmState.offset === 0;
  document.querySelector("#crm-next").disabled = !data.tem_mais;
}
async function loadHistory() { show(""); renderHistory(await api(`/v1/admin/crm/historico?${params()}`)); }
async function loadAtendimentoStats() {
  crmState.atendimento = await api("/v1/admin/leads-dashboard");
  if (crmState.ultimoHistorico) renderMetrics(crmState.ultimoHistorico);
}

function reminderParams() {
  const query = new URLSearchParams(new FormData(reminderFilter));
  [...query].forEach(([key, value]) => { if (!value) query.delete(key); });
  query.set("limite", crmState.remindersLimit);
  query.set("deslocamento", crmState.remindersOffset);
  return query;
}
function renderReminderMetrics(metrics) {
  document.querySelector("#crm-reminder-metrics").innerHTML = [["Vencidos", metrics.vencidos, "danger"], ["Próximos 7 dias", metrics.proximos_7_dias, "warning"], ["Pendentes", metrics.pendentes, "normal"], ["Cadastros a revisar", metrics.cadastros_para_atualizar, "info"]].map(([label, value, kind]) => `<span class="${kind}"><strong>${value}</strong>${label}</span>`).join("");
}
function renderCustomerAlerts(items) {
  const target = document.querySelector("#crm-customer-alerts");
  target.innerHTML = items.length ? `<details class="crm-customer-alert"><summary>${items.length} cadastro${items.length === 1 ? "" : "s"} sem atualização há mais de 90 dias</summary><div>${items.map(item => `<article><span><strong>${esc(item.cliente)}</strong><small>${esc(item.empresa || "Empresa não informada")} · última atualização ${dateTime.format(new Date(item.atualizado_em))}</small></span><button class="secondary-button create-update-reminder" type="button" data-lead-id="${item.lead_id}">Criar alerta</button></article>`).join("")}</div></details>` : "";
}
function renderReminders(data) {
  crmState.canManage = data.acoes.gerenciar; crmState.remindersTotal = data.total; renderReminderMetrics(data.metricas); renderCustomerAlerts(data.cadastros_para_atualizar);
  document.querySelector("#new-reminder").hidden = !crmState.canManage;
  document.querySelector("#crm-reminders").innerHTML = data.itens.length ? data.itens.map(item => `<article class="crm-reminder ${item.vencido ? "overdue" : ""} priority-${esc(item.prioridade)}"><div><span class="crm-reminder-type">${esc(item.tipo_nome)}</span><h3>${esc(item.titulo)}</h3><p>${esc(item.cliente)}${item.empresa ? ` · ${esc(item.empresa)}` : ""}</p>${item.descricao ? `<small>${esc(item.descricao)}</small>` : ""}${item.motivo_adiamento ? `<small class="crm-reminder-motivo">Adiado: ${esc(item.motivo_adiamento)}</small>` : ""}</div><div class="crm-reminder-due"><span>${item.vencido ? "Vencido" : "Alerta"}</span><strong>${dateTime.format(new Date(item.lembrar_em))}</strong><small>${esc(item.responsavel || "Não atribuído")} · prioridade ${esc(item.prioridade)}</small></div>${crmState.canManage && item.status === "pendente" ? `<div class="crm-reminder-actions"><button class="primary-button complete-reminder" data-id="${item.id}" type="button">Concluir</button><button class="secondary-button postpone-reminder" data-id="${item.id}" type="button">Adiar 1 dia</button><button class="text-button cancel-reminder-item" data-id="${item.id}" type="button">Cancelar</button></div>` : `<span class="crm-reminder-state">${esc(item.status)}</span>`}</article>`).join("") : `<div class="crm-empty"><strong>Nenhum lembrete neste filtro.</strong><p>Crie alertas para que retornos e tarefas não dependam da memória da equipe.</p></div>`;
  const page = Math.floor(crmState.remindersOffset / crmState.remindersLimit) + 1;
  document.querySelector("#crm-reminders-page").textContent = `Página ${page}`;
  document.querySelector("#crm-reminders-prev").disabled = crmState.remindersOffset === 0;
  document.querySelector("#crm-reminders-next").disabled = !data.tem_mais;
}
async function loadReminders() {
  let data = await api(`/v1/admin/crm/lembretes?${reminderParams()}`);
  // Concluir/cancelar o único item de uma página deixaria a página vazia --
  // volta uma página automaticamente em vez de mostrar "nenhum lembrete".
  if (!data.itens.length && crmState.remindersOffset > 0 && data.total > 0) {
    crmState.remindersOffset = Math.max(0, crmState.remindersOffset - crmState.remindersLimit);
    data = await api(`/v1/admin/crm/lembretes?${reminderParams()}`);
  }
  renderReminders(data);
}

async function references() {
  const data = await api("/v1/admin/crm/referencias"); crmState.references = data;
  form.elements.canal.innerHTML = '<option value="">Todos</option>' + options(data.canais);
  form.elements.operador_id.innerHTML = '<option value="">Todos</option>' + options(data.operadores);
  form.elements.status_cliente.innerHTML = '<option value="">Todos</option>' + options(data.status_clientes);
  reminderFilter.elements.tipo.innerHTML = '<option value="">Todos</option>' + options(data.tipos_lembrete);
  reminderFilter.elements.responsavel_id.innerHTML = '<option value="">Todos</option>' + options(data.operadores);
  reminderForm.elements.lead_id.innerHTML = '<option value="">Selecione</option>' + data.clientes.map(item => `<option value="${item.id}">${esc(item.nome)}${item.empresa ? ` · ${esc(item.empresa)}` : ""}</option>`).join("");
  reminderForm.elements.tipo.innerHTML = options(data.tipos_lembrete);
  reminderForm.elements.prioridade.innerHTML = options(data.prioridades, "media");
  reminderForm.elements.responsavel_id.innerHTML = '<option value="">Não atribuído</option>' + options(data.operadores);
}
function openPostpone(id) {
  postponeReminderId = id; postponeForm.reset();
  document.querySelector("#postpone-message").hidden = true; postponeDialog.showModal();
}
function openReminder(leadId = "", type = "retorno") {
  reminderForm.reset(); reminderForm.elements.lead_id.value = leadId; reminderForm.elements.tipo.value = type; reminderForm.elements.prioridade.value = "media";
  const due = new Date(Date.now() + 24 * 60 * 60 * 1000); due.setMinutes(due.getMinutes() - due.getTimezoneOffset()); reminderForm.elements.lembrar_em.value = due.toISOString().slice(0, 16);
  if (type === "atualizar_cadastro") reminderForm.elements.titulo.value = "Atualizar dados cadastrais do cliente";
  document.querySelector("#reminder-message").hidden = true; reminderDialog.showModal();
}
async function updateReminder(id, payload) { await api(`/v1/admin/crm/lembretes/${id}`, { method: "PATCH", body: JSON.stringify(payload) }); await loadReminders(); }

form.addEventListener("submit", event => { event.preventDefault(); crmState.offset = 0; loadHistory().catch(error => show(error.message)); });
reminderFilter.addEventListener("submit", event => { event.preventDefault(); crmState.remindersOffset = 0; loadReminders().catch(error => show(error.message)); });
document.querySelector("#crm-reminders-prev").addEventListener("click", () => { crmState.remindersOffset = Math.max(0, crmState.remindersOffset - crmState.remindersLimit); loadReminders().catch(error => show(error.message)); });
document.querySelector("#crm-reminders-next").addEventListener("click", () => { if (crmState.remindersOffset + crmState.remindersLimit < crmState.remindersTotal) { crmState.remindersOffset += crmState.remindersLimit; loadReminders().catch(error => show(error.message)); } });
document.querySelector("#crm-clear").addEventListener("click", () => { form.reset(); crmState.offset = 0; loadHistory().catch(error => show(error.message)); });
document.querySelector("#crm-prev").addEventListener("click", () => { crmState.offset = Math.max(0, crmState.offset - crmState.limit); loadHistory().catch(error => show(error.message)); });
document.querySelector("#crm-next").addEventListener("click", () => { if (crmState.offset + crmState.limit < crmState.total) { crmState.offset += crmState.limit; loadHistory().catch(error => show(error.message)); } });
document.querySelector("#new-reminder").addEventListener("click", () => openReminder());
document.querySelector("#crm-kanban-refresh")?.addEventListener("click", () => loadKanban().catch(error => show(error.message)));
document.querySelector("#crm-kanban-distribuir")?.addEventListener("click", async event => {
  const botao = event.currentTarget;
  botao.disabled = true;
  try {
    const resultado = await api("/v1/admin/leads/distribuir", { method: "POST", body: JSON.stringify({}) });
    const resumo = Object.entries(resultado.por_responsavel).map(([nome, quantidade]) => `${nome}: ${quantidade}`).join(" · ");
    show(resultado.distribuidos ? `${resultado.distribuidos} lead(s) distribuído(s) — ${resumo}` : "Nenhum lead sem responsável para distribuir.", "success");
    await loadKanban();
  } catch (error) { show(error.message); }
  finally { botao.disabled = false; }
});
document.querySelector("#close-reminder").addEventListener("click", () => reminderDialog.close());
document.querySelector("#cancel-reminder").addEventListener("click", () => reminderDialog.close());
document.querySelector("#crm-customer-alerts").addEventListener("click", event => { const button = event.target.closest(".create-update-reminder"); if (button) openReminder(button.dataset.leadId, "atualizar_cadastro"); });
document.querySelector("#crm-reminders").addEventListener("click", event => {
  const complete = event.target.closest(".complete-reminder"), postpone = event.target.closest(".postpone-reminder"), cancel = event.target.closest(".cancel-reminder-item");
  if (complete) updateReminder(complete.dataset.id, { status: "concluido" }).catch(error => show(error.message));
  if (postpone) openPostpone(postpone.dataset.id);
  if (cancel) updateReminder(cancel.dataset.id, { status: "cancelado" }).catch(error => show(error.message));
});
document.querySelector("#close-postpone").addEventListener("click", () => postponeDialog.close());
document.querySelector("#cancel-postpone").addEventListener("click", () => postponeDialog.close());
postponeForm.addEventListener("submit", async event => {
  event.preventDefault(); const motivo = new FormData(postponeForm).get("motivo").trim();
  if (!motivo) return;
  const statusBox = document.querySelector("#postpone-message"); statusBox.hidden = false; statusBox.className = "status-message loading"; statusBox.textContent = "Salvando…";
  try {
    await updateReminder(postponeReminderId, { lembrar_em: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(), status: "pendente", motivo_adiamento: motivo });
    postponeDialog.close();
  } catch (error) { statusBox.className = "status-message error"; statusBox.textContent = error.message; }
});
reminderForm.addEventListener("submit", async event => {
  event.preventDefault(); const data = Object.fromEntries(new FormData(reminderForm));
  data.lead_id = Number(data.lead_id); data.responsavel_id = Number(data.responsavel_id) || null; data.lembrar_em = new Date(data.lembrar_em).toISOString(); data.descricao = data.descricao || null;
  const statusBox = document.querySelector("#reminder-message"); statusBox.hidden = false; statusBox.className = "status-message loading"; statusBox.textContent = "Salvando…";
  try { await api("/v1/admin/crm/lembretes", { method: "POST", body: JSON.stringify(data) }); reminderDialog.close(); show("Lembrete criado e incluído na agenda da equipe.", "success"); await loadReminders(); }
  catch (error) { statusBox.className = "status-message error"; statusBox.textContent = error.message; }
});

references().then(() => Promise.all([loadHistory(), loadReminders(), loadKanban(), loadAtendimentoStats()])).then(() => {
  const leadId = new URLSearchParams(location.search).get("lead_id"); if (/^\d+$/.test(leadId || "") && crmState.canManage) openReminder(leadId);
}).catch(error => show(error.message));

const pesquisaId = location.pathname.split("/").filter(Boolean).pop();
const sections = document.querySelector("#analysis-sections");
const progress = document.querySelector("#analysis-progress");
const message = document.querySelector("#analysis-message");
const helpDrawer = document.querySelector("#analysis-help");
let analysis = null;

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}
function formatDate(value) {
  if (!value) return "Não informado";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" }).format(new Date(value));
}
function percent(value) { return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`; }
function label(value) { return String(value || "não informado").replaceAll("_", " "); }
function showMessage(text, kind = "") { message.className = `status-message ${kind}`.trim(); message.textContent = text; }
function statusBadge(status, text) { return `<span class="step-status ${status}">${escapeHtml(text)}</span>`; }
function helpButton(topic, title) { return `<button class="context-help" type="button" data-help="${topic}" aria-label="Ajuda: ${escapeHtml(title)}">?</button>`; }
function step({ id, number, title, subtitle, state, stateLabel, body, open = false }) {
  return `<details id="step-${id}" class="analysis-step" ${open ? "open" : ""}>
    <summary><div class="analysis-step-title"><span class="analysis-step-number">${number}</span><div><h2>${escapeHtml(title)} ${helpButton(id, title)}</h2><small>${escapeHtml(subtitle)}</small></div></div>${statusBadge(state, stateLabel)}</summary>
    <div class="analysis-step-body">${body}</div>
  </details>`;
}
function stateFrom(available, attention = false) { return !available ? "pending" : attention ? "attention" : "completed"; }
function permissionState(permission) { return permission ? null : { state: "restricted", stateLabel: "Sem permissão", body: `<p class="analysis-empty">Seu perfil não possui acesso a esta etapa.</p>` }; }

function renderSummary(data) {
  const lead = data.lead;
  document.querySelector("#analysis-brand").textContent = data.pesquisa.marca;
  document.querySelector("#analysis-subtitle").textContent = data.pesquisa.atividade || "Atividade não informada";
  document.querySelector("#analysis-public-report").href = `/relatorios/${encodeURIComponent(data.pesquisa.id)}`;
  document.querySelector("#analysis-summary").innerHTML = `
    <div><span>Cliente</span><strong title="${escapeHtml(lead?.nome || "Sem vínculo")}">${escapeHtml(lead?.nome || "Sem vínculo")}</strong></div>
    <div><span>Empresa</span><strong title="${escapeHtml(lead?.empresa || "Não informada")}">${escapeHtml(lead?.empresa || "Não informada")}</strong></div>
    <div><span>Contato</span><strong title="${escapeHtml(lead?.email || "Não informado")}">${escapeHtml(lead?.email || "Não informado")}</strong></div>
    <div><span>Pesquisa criada</span><strong>${formatDate(data.pesquisa.criado_em)}</strong></div>
    <div><span>Relatório completo</span><strong>${data.relatorio_completo.gerado ? `Gerado em ${formatDate(data.relatorio_completo.gerado_em)}` : "Não gerado"}</strong></div>`;
}

function renderValidation(data) {
  const denied = permissionState(data.permissoes.validacao_visualizar);
  if (denied) return step({ id: "validation", number: 1, title: "Validação técnica", subtitle: "Situações, classes, afinidade e alto renome", ...denied });
  const item = data.validacao;
  if (!item?.disponivel) return step({ id: "validation", number: 1, title: "Validação técnica", subtitle: "Situações, classes, afinidade e alto renome", state: "pending", stateLabel: "Aguardando dados", body: `<p class="analysis-empty">Abra o resultado automático para preparar o snapshot técnico desta pesquisa.</p>`, open: true });
  const alerts = item.qualidade?.avisos || [];
  const conflicts = (item.conflitos || []).map(conflict => `<tr><td>${escapeHtml(conflict.numero)}</td><td><strong>${escapeHtml(conflict.titulo || "Sem título")}</strong><br><small>${escapeHtml(conflict.relevancia_rotulo || label(conflict.relevancia))}</small></td><td>${escapeHtml(conflict.situacao || "Não informada")}</td><td>${escapeHtml((conflict.classes || []).join(", ") || "—")}</td><td>${conflict.alto_renome ? "Sim" : escapeHtml(conflict.afinidade?.rotulo || "Não")}</td></tr>`).join("");
  const attention = alerts.length > 0 || item.matriz_afinidade_status !== "validada";
  return step({ id: "validation", number: 1, title: "Validação técnica", subtitle: "Situações, classes, afinidade e alto renome", state: stateFrom(true, attention), stateLabel: attention ? "Requer atenção" : "Concluída", open: true, body: `
    <div class="analysis-grid"><div class="analysis-stat"><span>Base</span><strong>RPI ${escapeHtml(item.ultima_rpi || "—")}</strong></div><div class="analysis-stat"><span>Ocorrências</span><strong>${item.total_ocorrencias}</strong></div><div class="analysis-stat"><span>Matriz de afinidade</span><strong>${escapeHtml(label(item.matriz_afinidade_status))}</strong></div></div>
    ${alerts.length ? `<ul class="analysis-alerts">${alerts.map(alert => `<li>${escapeHtml(alert)}</li>`).join("")}</ul>` : ""}
    <table class="analysis-conflicts"><thead><tr><th>Processo</th><th>Marca e relevância</th><th>Situação</th><th>Classes</th><th>Afinidade / alto renome</th></tr></thead><tbody>${conflicts || `<tr><td colspan="5">Nenhum conflito exibido.</td></tr>`}</tbody></table>` });
}

function renderRisk(data) {
  const denied = permissionState(data.permissoes.risco_visualizar);
  if (denied) return step({ id: "risk", number: 2, title: "Motor determinístico de risco", subtitle: "Pontuação auditável baseada em regras", ...denied });
  const item = data.risco;
  if (!item) return step({ id: "risk", number: 2, title: "Motor determinístico de risco", subtitle: "Pontuação auditável baseada em regras", state: "pending", stateLabel: "Não calculado", body: `<p class="analysis-empty">O risco será calculado quando o snapshot técnico for atualizado.</p>` });
  const conflicts = (item.principais_conflitos || []).slice(0, 6).map(conflict => `<li><strong>${escapeHtml(conflict.numero || "Processo")}</strong> — ${escapeHtml(conflict.titulo || "Sem título")}</li>`).join("");
  const review = data.permissoes.risco_revisar ? `<form id="risk-review-form" class="analysis-action-form" data-id="${item.id}"><label>Nível do parecer<select name="nivel_humano" required>${["baixo","moderado","alto","critico"].map(value => `<option value="${value}" ${item.nivel_humano === value ? "selected" : ""}>${label(value)}</option>`).join("")}</select></label><label>Avaliador<input name="avaliador" minlength="2" value="${escapeHtml(item.avaliador || data.usuario.nome)}" required></label><label class="wide">Justificativa<textarea name="observacoes_humanas" minlength="3" rows="4" required>${escapeHtml(item.observacoes_humanas || "")}</textarea></label><button class="primary-button" type="submit">Salvar parecer de risco</button></form>` : "";
  return step({ id: "risk", number: 2, title: "Motor determinístico de risco", subtitle: `Versão ${item.versao_motor} · ${item.modo}`, state: item.avaliado_em ? "completed" : "attention", stateLabel: item.avaliado_em ? "Parecer registrado" : "Aguardando parecer", body: `<div class="analysis-grid"><div class="analysis-stat"><span>Pontuação</span><strong>${item.pontuacao} pontos</strong></div><div class="analysis-stat"><span>Nível calculado</span><strong>${escapeHtml(label(item.nivel))}</strong></div><div class="analysis-stat"><span>Avaliação humana</span><strong>${escapeHtml(label(item.nivel_humano || "pendente"))}</strong></div></div>${conflicts ? `<h3>Principais conflitos</h3><ul class="analysis-alerts">${conflicts}</ul>` : ""}${review}` });
}

function renderLearning(data) {
  const denied = permissionState(data.permissoes.aprendizado_visualizar);
  if (denied) return step({ id: "learning", number: 3, title: "Aprendizado supervisionado", subtitle: "Probabilidade histórica e incerteza", ...denied });
  const item = data.aprendizado;
  if (!item) return step({ id: "learning", number: 3, title: "Aprendizado supervisionado", subtitle: "Probabilidade histórica e incerteza", state: "pending", stateLabel: "Sem previsão", body: `<p class="analysis-empty">Ainda não existe uma previsão supervisionada para esta pesquisa.</p>` });
  const alerts = item.alertas_qualidade || [];
  const review = data.permissoes.aprendizado_revisar ? `<form id="learning-review-form" class="analysis-action-form" data-id="${item.id}"><label>Leitura humana<select name="nivel_humano" required>${["favoravel","atencao","alto_risco","critico"].map(value => `<option value="${value}" ${item.nivel_humano === value ? "selected" : ""}>${label(value)}</option>`).join("")}</select></label><label>Avaliador<input name="avaliador" minlength="2" value="${escapeHtml(item.avaliador || data.usuario.nome)}" required></label><label class="wide">Observações<textarea name="observacoes_humanas" minlength="3" rows="4" required>${escapeHtml(item.observacoes_humanas || "")}</textarea></label><button class="primary-button" type="submit">Salvar leitura supervisionada</button></form>` : "";
  return step({ id: "learning", number: 3, title: "Aprendizado supervisionado", subtitle: `Modelo ${item.modelo}`, state: alerts.length ? "attention" : "completed", stateLabel: alerts.length ? "Estimativa com alertas" : "Disponível", body: `<div class="analysis-probability"><strong>${percent(item.probabilidade)}</strong><span>de deferimento estimado<br>faixa ${percent(item.probabilidade_inferior)} a ${percent(item.probabilidade_superior)}</span></div><div class="analysis-grid"><div class="analysis-stat"><span>Confiança</span><strong>${escapeHtml(item.confianca_rotulo)} · ${percent(item.confianca)}</strong></div><div class="analysis-stat"><span>Cobertura</span><strong>${percent(item.cobertura)}</strong></div><div class="analysis-stat"><span>Leitura humana</span><strong>${escapeHtml(label(item.nivel_humano || "pendente"))}</strong></div></div>${alerts.length ? `<ul class="analysis-alerts">${alerts.map(alert => `<li>${escapeHtml(alert)}</li>`).join("")}</ul>` : ""}<p class="analysis-empty">Estimativa preliminar baseada em decisões históricas. Não constitui garantia de registro.</p>${review}` });
}

function aiOutput(value) {
  if (!value) return "";
  if (typeof value === "string") return escapeHtml(value);
  return escapeHtml(JSON.stringify(value, null, 2));
}
function renderAi(data) {
  const denied = permissionState(data.permissoes.ia_visualizar);
  if (denied) return step({ id: "ai", number: 4, title: "IA explicativa", subtitle: "Explicação estruturada dos indicadores", ...denied });
  const item = data.ia;
  if (!item?.avaliacao_risco_id) return step({ id: "ai", number: 4, title: "IA explicativa", subtitle: "Explicação estruturada dos indicadores", state: "pending", stateLabel: "Aguardando risco", body: `<p class="analysis-empty">A IA somente pode explicar uma avaliação de risco já calculada.</p>` });
  const explanation = item.explicacao;
  const generate = data.permissoes.ia_gerar ? `<button id="generate-ai" class="primary-button" type="button" data-id="${item.avaliacao_risco_id}">${explanation ? "Gerar nova explicação" : "Gerar explicação com IA"}</button>` : "";
  const review = explanation && data.permissoes.ia_revisar && explanation.saida ? `<form id="ai-review-form" class="analysis-action-form" data-id="${explanation.id}"><label>Decisão<select name="decisao"><option value="aprovada">Aprovar</option><option value="rejeitada">Rejeitar</option></select></label><label>Revisor<input name="revisor" minlength="2" value="${escapeHtml(explanation.revisor || data.usuario.nome)}" required></label><label class="wide">Observações<textarea name="observacoes" minlength="3" rows="4" required>${escapeHtml(explanation.observacoes_revisao || "")}</textarea></label><button class="primary-button" type="submit">Registrar revisão da IA</button></form>` : "";
  const state = !item.habilitada ? "attention" : explanation?.status === "aprovada" ? "completed" : explanation ? "attention" : "pending";
  const stateLabel = !item.habilitada ? "IA desativada" : explanation ? label(explanation.status) : "Não gerada";
  return step({ id: "ai", number: 4, title: "IA explicativa", subtitle: "A IA explica; não altera pontuação ou probabilidade", state, stateLabel, body: `${!item.habilitada ? `<p class="analysis-empty">A IA está desativada no controle de produção. As demais etapas continuam funcionando normalmente.</p>` : ""}${explanation?.erro ? `<ul class="analysis-alerts"><li>${escapeHtml(explanation.erro)}</li></ul>` : ""}${explanation?.saida ? `<div class="ai-output">${aiOutput(explanation.saida)}</div>` : ""}<div class="analysis-action-row">${generate}</div>${review}` });
}

function renderOpinion(data) {
  const reviewed = Boolean(data.risco?.avaliado_em);
  return step({ id: "opinion", number: 5, title: "Parecer humano", subtitle: "Síntese profissional e justificativa", state: reviewed ? "completed" : "pending", stateLabel: reviewed ? "Registrado" : "Pendente", body: reviewed ? `<div class="analysis-grid"><div class="analysis-stat"><span>Classificação</span><strong>${escapeHtml(label(data.risco.nivel_humano))}</strong></div><div class="analysis-stat"><span>Avaliador</span><strong>${escapeHtml(data.risco.avaliador)}</strong></div><div class="analysis-stat"><span>Data</span><strong>${formatDate(data.risco.avaliado_em)}</strong></div></div><div class="ai-output">${escapeHtml(data.risco.observacoes_humanas)}</div>` : `<p class="analysis-empty">Registre o parecer na etapa “Motor determinístico de risco”. Ele será consolidado aqui e ficará disponível para o relatório completo.</p>` });
}

function renderReport(data) {
  const report = data.relatorio_completo;
  const action = data.permissoes.relatorio_gerar && report.base_disponivel ? `<button id="generate-full-report" class="primary-button" type="button">${report.gerado ? "Baixar completo novamente" : "Gerar relatório completo"}</button>` : "";
  return step({ id: "report", number: 6, title: "Relatório completo", subtitle: "Documento interno para revisão e contato com o cliente", state: report.gerado ? "completed" : "pending", stateLabel: report.gerado ? "Já gerado" : "Não gerado", body: `<div class="analysis-grid"><div class="analysis-stat"><span>Status</span><strong>${report.gerado ? "Completo gerado" : "Completo não gerado"}</strong></div><div class="analysis-stat"><span>Primeira geração</span><strong>${formatDate(report.gerado_em)}</strong></div><div class="analysis-stat"><span>Responsável</span><strong>${escapeHtml(report.gerado_por || "Não informado")}</strong></div></div><p class="analysis-empty">Gere o documento após conferir as evidências. A primeira geração é registrada de forma permanente.</p><div class="analysis-action-row">${action}</div>` });
}

function renderProgress(data) {
  const steps = [
    ["validation", "Validação técnica", data.permissoes.validacao_visualizar ? (data.validacao?.disponivel ? ((data.validacao.qualidade?.avisos || []).length ? "attention" : "completed") : "pending") : "restricted"],
    ["risk", "Motor de risco", data.permissoes.risco_visualizar ? (data.risco ? (data.risco.avaliado_em ? "completed" : "attention") : "pending") : "restricted"],
    ["learning", "Aprendizado", data.permissoes.aprendizado_visualizar ? (data.aprendizado ? ((data.aprendizado.alertas_qualidade || []).length ? "attention" : "completed") : "pending") : "restricted"],
    ["ai", "IA explicativa", data.permissoes.ia_visualizar ? (data.ia?.explicacao ? (data.ia.explicacao.status === "aprovada" ? "completed" : "attention") : "pending") : "restricted"],
    ["opinion", "Parecer humano", data.risco?.avaliado_em ? "completed" : "pending"],
    ["report", "Relatório completo", data.relatorio_completo.gerado ? "completed" : "pending"],
  ];
  progress.innerHTML = steps.map(([id, title, state], index) => `<li class="${state}"><a href="#step-${id}"><i>${state === "completed" ? "✓" : index + 1}</i><span>${escapeHtml(title)}</span></a></li>`).join("");
}

function render(data) {
  analysis = data;
  renderSummary(data);
  renderProgress(data);
  sections.innerHTML = [renderValidation(data), renderRisk(data), renderLearning(data), renderAi(data), renderOpinion(data), renderReport(data)].join("");
}

async function load() {
  showMessage("Carregando análise consolidada…");
  const response = await fetch(`/v1/admin/analises/${encodeURIComponent(pesquisaId)}`);
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || "Não foi possível carregar a análise."); }
  render(await response.json());
  showMessage("");
}
async function sendJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Operação não concluída.");
  return data;
}
async function downloadFullReport() {
  const response = await fetch(`/v1/admin/pesquisas/${encodeURIComponent(pesquisaId)}/relatorio-completo.pdf`, { method: "POST" });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || "Não foi possível gerar o relatório."); }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `relatorio-completo-${pesquisaId}.pdf`;
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
}

sections.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target; const values = new FormData(form); const button = form.querySelector("button[type=submit]"); button.disabled = true;
  try {
    if (form.id === "risk-review-form") await sendJson(`/v1/admin/fase3/avaliacoes/${form.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nivel_humano: values.get("nivel_humano"), avaliador: values.get("avaliador"), observacoes_humanas: values.get("observacoes_humanas") }) });
    if (form.id === "learning-review-form") await sendJson(`/v1/admin/aprendizado/previsoes/${form.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nivel_humano: values.get("nivel_humano"), avaliador: values.get("avaliador"), observacoes_humanas: values.get("observacoes_humanas") }) });
    if (form.id === "ai-review-form") await sendJson(`/v1/admin/fase4/explicacoes/${form.dataset.id}/revisao`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decisao: values.get("decisao"), revisor: values.get("revisor"), observacoes: values.get("observacoes") }) });
    showMessage("Etapa atualizada com sucesso.", "success"); await load();
  } catch (error) { showMessage(error.message, "error"); button.disabled = false; }
});
sections.addEventListener("click", async event => {
  const help = event.target.closest(".context-help");
  if (help) { helpDrawer.showModal(); const target = document.querySelector(`#help-${help.dataset.help}`); if (target) { target.open = true; target.scrollIntoView({ block: "start" }); } return; }
  const ai = event.target.closest("#generate-ai");
  const report = event.target.closest("#generate-full-report");
  if (!ai && !report) return;
  const button = ai || report; button.disabled = true;
  try {
    if (ai) await sendJson(`/v1/admin/fase4/avaliacoes/${ai.dataset.id}/gerar`, { method: "POST" });
    if (report) await downloadFullReport();
    showMessage(ai ? "Explicação gerada." : "Relatório completo gerado e registrado.", "success"); await load();
  } catch (error) { showMessage(error.message, "error"); button.disabled = false; }
});

document.querySelector("#analysis-help-open").addEventListener("click", () => helpDrawer.showModal());
document.querySelector("#analysis-help-close").addEventListener("click", () => helpDrawer.close());
helpDrawer.addEventListener("click", event => { if (event.target === helpDrawer) helpDrawer.close(); });
load().catch(error => { showMessage(error.message, "error"); sections.innerHTML = `<p class="analysis-empty">${escapeHtml(error.message)}</p>`; });

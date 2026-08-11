const pesquisaId = location.pathname.split("/").filter(Boolean).pop();
const sections = document.querySelector("#analysis-sections");
const progress = document.querySelector("#analysis-progress");
const message = document.querySelector("#analysis-message");
const helpDrawer = document.querySelector("#analysis-help");
const registrabilityDialog = document.querySelector("#registrability-dialog");
const registrabilityForm = document.querySelector("#registrability-form");
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

const riskRanges = [
  { level: "baixo", range: "0–24", title: "Baixo risco", meaning: "Cenário mais favorável", detail: "Poucos indícios de conflito relevante nos dados analisados." },
  { level: "moderado", range: "25–49", title: "Risco moderado", meaning: "Registro exige atenção", detail: "Existem conflitos que precisam de conferência antes do protocolo." },
  { level: "alto", range: "50–74", title: "Alto risco", meaning: "Chance relevante de impedimento", detail: "Há sinais fortes de conflito e a viabilidade tende a ser desfavorável." },
  { level: "critico", range: "75–100", title: "Risco crítico", meaning: "Grande chance de não registrar", detail: "Os conflitos encontrados indicam forte possibilidade de indeferimento." },
];

function riskLegend(item) {
  const current = riskRanges.find(range => range.level === item.nivel) || riskRanges[0];
  return `<section class="risk-legend risk-legend-${escapeHtml(current.level)}" aria-label="Interpretação da pontuação de risco">
    <header><div><span>Como interpretar</span><strong>${item.pontuacao} pontos: ${escapeHtml(current.meaning)}</strong></div><p>${escapeHtml(current.detail)}</p></header>
    <div class="risk-scale">${riskRanges.map(range => `<div class="risk-range ${range.level} ${range.level === item.nivel ? "active" : ""}" ${range.level === item.nivel ? 'aria-current="true"' : ""}><span>${range.range} pontos</span><strong>${escapeHtml(range.title)}</strong><small>${escapeHtml(range.meaning)}</small></div>`).join("")}</div>
    <p class="risk-legend-note"><strong>Importante:</strong> esta pontuação mede risco de conflito. Ela não é um percentual de chance e não substitui a análise profissional nem a decisão do INPI.</p>
  </section>`;
}

const officialStatusLabels = {
  atendido: "Atendido na triagem",
  alerta: "Ponto de atenção",
  possivel_impedimento: "Possível impedimento",
  nao_analisado: "Não analisado",
  nao_aplicavel: "Não aplicável",
};

function officialMatrix(matrix, canComplete = false) {
  if (!matrix) return "";
  const rules = (matrix.regras || []).map(rule => `<article class="official-rule ${escapeHtml(rule.status)}">
    <header><div><strong>${escapeHtml(rule.criterio)}</strong><small>${escapeHtml(rule.referencia)}</small></div><span>${escapeHtml(officialStatusLabels[rule.status] || label(rule.status))}</span></header>
    <p>${escapeHtml(rule.conclusao)}</p><small>${escapeHtml(rule.evidencia)}</small>
  </article>`).join("");
  return `<section class="official-matrix" aria-labelledby="official-matrix-title">
    <header><div><p class="eyebrow">Critérios oficiais</p><h3 id="official-matrix-title">Matriz de Registrabilidade INPI</h3><p>${escapeHtml(matrix.conclusao)}</p></div><div class="official-coverage"><strong>${matrix.cobertura_percentual}%</strong><span>dos critérios verificáveis<br>com os dados atuais</span></div></header>
    <div class="official-summary"><span class="atendido">${matrix.contagens.atendido || 0} atendidos</span><span class="alerta">${matrix.contagens.alerta || 0} alertas</span><span class="possivel_impedimento">${matrix.contagens.possivel_impedimento || 0} possíveis impedimentos</span><span class="nao_analisado">${matrix.contagens.nao_analisado || 0} não analisados</span><span class="nao_aplicavel">${matrix.contagens.nao_aplicavel || 0} não aplicáveis</span></div>
    <div class="official-rules">${rules}</div>
    <footer><p>${escapeHtml(matrix.aviso)}</p><div class="official-footer-actions">${canComplete ? `<button id="complete-official-analysis" class="primary-button" type="button">Completar análise oficial</button>` : ""}<a href="${escapeHtml(matrix.fonte)}" target="_blank" rel="noopener">Consultar Manual de Marcas do INPI</a></div><small>Versão da matriz: ${escapeHtml(matrix.versao)} · Manual de ${escapeHtml(matrix.manual_atualizado_em)}</small></footer>
  </section>`;
}

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
    ${officialMatrix(item.matriz_registrabilidade, data.permissoes.validacao_revisar)}
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
  return step({ id: "risk", number: 2, title: "Motor determinístico de risco", subtitle: `Versão ${item.versao_motor} · ${item.modo}`, state: item.avaliado_em ? "completed" : "attention", stateLabel: item.avaliado_em ? "Parecer registrado" : "Aguardando parecer", body: `<div class="analysis-grid"><div class="analysis-stat"><span>Pontuação de risco</span><strong>${item.pontuacao} pontos</strong></div><div class="analysis-stat"><span>Nível calculado</span><strong>${escapeHtml(label(item.nivel))}</strong></div><div class="analysis-stat"><span>Avaliação humana</span><strong>${escapeHtml(label(item.nivel_humano || "pendente"))}</strong></div></div>${riskLegend(item)}${conflicts ? `<h3>Principais conflitos</h3><ul class="analysis-alerts">${conflicts}</ul>` : ""}${review}` });
}

function renderDeterministicLearning(data) {
  const indicator = data.validacao?.indicador_deterministico;
  const risk = data.risco;
  const matrix = data.validacao?.matriz_registrabilidade;
  const candidate = data.aprendizado?.modelo_status === "candidato" ? data.aprendizado : null;
  const candidateReview = candidate && data.permissoes.aprendizado_revisar ? `
    <section class="analysis-opinion">
      <h3>Previsão candidata · somente uso interno</h3>
      <div class="analysis-probability"><strong>${percent(candidate.probabilidade)}</strong><span>estimativa do modelo candidato<br>faixa ${percent(candidate.probabilidade_inferior)} a ${percent(candidate.probabilidade_superior)}</span></div>
      <p>Esta estimativa não está liberada ao cliente. Registre a leitura humana para ampliar a validação do modelo.</p>
      <form id="learning-review-form" class="analysis-action-form" data-id="${candidate.id}"><label>Leitura humana<select name="nivel_humano" required>${["favoravel","atencao","alto_risco","critico"].map(value => `<option value="${value}" ${candidate.nivel_humano === value ? "selected" : ""}>${label(value)}</option>`).join("")}</select></label><label>Avaliador<input name="avaliador" minlength="2" value="${escapeHtml(candidate.avaliador || data.usuario.nome)}" required></label><label class="wide">Observações<textarea name="observacoes_humanas" minlength="3" rows="4" required>${escapeHtml(candidate.observacoes_humanas || "")}</textarea></label><button class="primary-button" type="submit">Salvar leitura supervisionada</button></form>
    </section>` : "";
  const body = indicator ? `
    <div class="analysis-probability deterministic"><strong>${indicator.indice}%</strong><span>índice indicativo de viabilidade<br>faixa técnica ${indicator.faixa_inferior}% a ${indicator.faixa_superior}%</span></div>
    <div class="analysis-grid">
      <div class="analysis-stat"><span>Motor de risco</span><strong>${risk ? `${risk.pontuacao} pontos · ${escapeHtml(label(risk.nivel))}` : "Aguardando cálculo"}</strong></div>
      <div class="analysis-stat"><span>Matriz INPI</span><strong>${indicator.cobertura_percentual}% de cobertura</strong></div>
      <div class="analysis-stat"><span>Leitura determinística</span><strong>${escapeHtml(indicator.titulo)}</strong></div>
      <div class="analysis-stat"><span>Critérios pendentes</span><strong>${indicator.pendencias.length}</strong></div>
    </div>
    <p>${escapeHtml(indicator.resumo)}</p>
    ${matrix ? `<div class="official-summary"><span class="atendido">${matrix.contagens.atendido || 0} atendidos</span><span class="alerta">${matrix.contagens.alerta || 0} alertas</span><span class="possivel_impedimento">${matrix.contagens.possivel_impedimento || 0} possíveis impedimentos</span><span class="nao_analisado">${matrix.contagens.nao_analisado || 0} não analisados</span></div>` : ""}
    <p class="analysis-empty"><strong>Modelo supervisionado em validação.</strong> ${escapeHtml(indicator.aviso)}</p>${candidateReview}` : `
    <p class="analysis-empty"><strong>Modelo supervisionado em validação.</strong> A análise determinística será apresentada assim que o snapshot técnico e o motor de risco forem calculados.</p>${candidateReview}`;
  return step({ id: "learning", number: 3, title: "Aprendizado supervisionado", subtitle: "Probabilidade histórica e incerteza", state: "attention", stateLabel: "Modelo em validação", body });
}

function renderLearning(data) {
  const denied = permissionState(data.permissoes.aprendizado_visualizar);
  if (denied) return step({ id: "learning", number: 3, title: "Aprendizado supervisionado", subtitle: "Probabilidade histórica e incerteza", ...denied });
  const item = data.aprendizado;
  if (!item || item.modelo_status !== "ativo") return renderDeterministicLearning(data);
  const alerts = item.alertas_qualidade || [];
  const review = data.permissoes.aprendizado_revisar ? `<form id="learning-review-form" class="analysis-action-form" data-id="${item.id}"><label>Leitura humana<select name="nivel_humano" required>${["favoravel","atencao","alto_risco","critico"].map(value => `<option value="${value}" ${item.nivel_humano === value ? "selected" : ""}>${label(value)}</option>`).join("")}</select></label><label>Avaliador<input name="avaliador" minlength="2" value="${escapeHtml(item.avaliador || data.usuario.nome)}" required></label><label class="wide">Observações<textarea name="observacoes_humanas" minlength="3" rows="4" required>${escapeHtml(item.observacoes_humanas || "")}</textarea></label><button class="primary-button" type="submit">Salvar leitura supervisionada</button></form>` : "";
  const reviewed = Boolean(item.avaliado_em);
  const state = reviewed ? "completed" : alerts.length ? "attention" : "pending";
  const stateLabel = reviewed ? "Leitura registrada" : alerts.length ? "Aguardando leitura" : "Pendente";
  return step({ id: "learning", number: 3, title: "Aprendizado supervisionado", subtitle: `Modelo ${item.modelo}`, state, stateLabel, body: `<div class="analysis-probability"><strong>${percent(item.probabilidade)}</strong><span>de deferimento estimado<br>faixa ${percent(item.probabilidade_inferior)} a ${percent(item.probabilidade_superior)}</span></div><div class="analysis-grid"><div class="analysis-stat"><span>Confiança</span><strong>${escapeHtml(item.confianca_rotulo)} · ${percent(item.confianca)}</strong></div><div class="analysis-stat"><span>Cobertura</span><strong>${percent(item.cobertura)}</strong></div><div class="analysis-stat"><span>Leitura humana</span><strong>${escapeHtml(label(item.nivel_humano || "pendente"))}</strong></div>${reviewed ? `<div class="analysis-stat"><span>Registrada em</span><strong>${formatDate(item.avaliado_em)}</strong></div>` : ""}</div>${alerts.length ? `<ul class="analysis-alerts">${alerts.map(alert => `<li>${escapeHtml(alert)}</li>`).join("")}</ul>` : ""}<p class="analysis-empty">Estimativa preliminar baseada em decisões históricas. Não constitui garantia de registro.</p>${review}` });
}

function renderAgent(data) {
  const item = data.agente_registrabilidade;
  if (!item) return step({ id: "agent", number: 4, title: "Agente de Registrabilidade", subtitle: "Consolidação auditável de regras, risco e histórico", state: "pending", stateLabel: "Aguardando execução", body: `<p class="analysis-empty">Abra ou atualize o resultado da pesquisa para executar o agente com o snapshot mais recente.</p>` });
  const state = item.abstencao ? "attention" : item.status === "concluida" ? "completed" : "attention";
  const probability = item.probabilidade_deferimento === null ? "Não calculada" : percent(item.probabilidade_deferimento);
  const interval = item.probabilidade_inferior === null ? "—" : `${percent(item.probabilidade_inferior)} a ${percent(item.probabilidade_superior)}`;
  const reasons = (item.motivos || []).map(reason => `<li>${escapeHtml(reason)}</li>`).join("");
  const outcome = item.resultado_real
    ? `<div class="analysis-stat"><span>Resultado real no INPI</span><strong>${escapeHtml(label(item.resultado_real))}</strong><small>RPI ${escapeHtml(item.resultado_numero_rpi || "—")}</small></div>`
    : `<div class="analysis-stat"><span>Aprendizado futuro</span><strong>${item.numero_pedido ? "Aguardando decisão do INPI" : "Pedido ainda não vinculado"}</strong></div>`;
  const reconcile = item.numero_pedido && !item.resultado_real && data.permissoes.validacao_revisar
    ? `<button id="reconcile-agent-outcome" class="secondary-button" type="button">Verificar decisão real na RPI</button>`
    : "";
  return step({ id: "agent", number: 4, title: "Agente de Registrabilidade", subtitle: `Versão ${item.versao_agente} · decisão baseada em evidências`, state, stateLabel: item.abstencao ? "Dados insuficientes" : label(item.decisao), body: `
    <div class="analysis-grid"><div class="analysis-stat"><span>Cenário consolidado</span><strong>${escapeHtml(label(item.decisao))}</strong></div><div class="analysis-stat"><span>Chance histórica estimada</span><strong>${escapeHtml(probability)}</strong><small>Faixa ${escapeHtml(interval)}</small></div><div class="analysis-stat"><span>Cobertura conjunta</span><strong>${percent(item.cobertura)}</strong></div>${outcome}</div>
    ${reasons ? `<h3>Fundamentos da decisão</h3><ul class="analysis-alerts">${reasons}</ul>` : ""}
    <p class="analysis-empty">${escapeHtml(item.aviso)}</p><div class="analysis-action-row">${reconcile}</div>` });
}

function renderOpinion(data) {
  const reviewed = Boolean(data.risco?.avaliado_em);
  return step({ id: "opinion", number: 4, title: "Parecer humano", subtitle: "Síntese profissional e justificativa", state: reviewed ? "completed" : "pending", stateLabel: reviewed ? "Registrado" : "Pendente", body: reviewed ? `<div class="analysis-grid"><div class="analysis-stat"><span>Classificação</span><strong>${escapeHtml(label(data.risco.nivel_humano))}</strong></div><div class="analysis-stat"><span>Avaliador</span><strong>${escapeHtml(data.risco.avaliador)}</strong></div><div class="analysis-stat"><span>Data</span><strong>${formatDate(data.risco.avaliado_em)}</strong></div></div><div class="analysis-opinion">${escapeHtml(data.risco.observacoes_humanas)}</div>` : `<p class="analysis-empty">Registre o parecer na etapa “Motor determinístico de risco”. Ele será consolidado aqui e ficará disponível para o relatório completo.</p>` });
}

function renderReport(data) {
  const report = data.relatorio_completo;
  const action = data.permissoes.relatorio_gerar && report.base_disponivel ? `<button id="generate-full-report" class="primary-button" type="button">${report.gerado ? "Baixar completo novamente" : "Gerar relatório completo"}</button>` : "";
  return step({ id: "report", number: 5, title: "Relatório completo", subtitle: "Documento interno para revisão e contato com o cliente", state: report.gerado ? "completed" : "pending", stateLabel: report.gerado ? "Já gerado" : "Não gerado", body: `<div class="analysis-grid"><div class="analysis-stat"><span>Status</span><strong>${report.gerado ? "Completo gerado" : "Completo não gerado"}</strong></div><div class="analysis-stat"><span>Primeira geração</span><strong>${formatDate(report.gerado_em)}</strong></div><div class="analysis-stat"><span>Responsável</span><strong>${escapeHtml(report.gerado_por || "Não informado")}</strong></div></div><p class="analysis-empty">Gere o documento após conferir as evidências. A primeira geração é registrada de forma permanente.</p><div class="analysis-action-row">${action}</div>` });
}

function renderProgress(data) {
  const steps = [
    ["validation", "Validação técnica", data.permissoes.validacao_visualizar ? (data.validacao?.disponivel ? ((data.validacao.qualidade?.avisos || []).length ? "attention" : "completed") : "pending") : "restricted"],
    ["risk", "Motor de risco", data.permissoes.risco_visualizar ? (data.risco ? (data.risco.avaliado_em ? "completed" : "attention") : "pending") : "restricted"],
    ["learning", "Aprendizado", data.permissoes.aprendizado_visualizar ? (data.aprendizado ? (data.aprendizado.avaliado_em ? "completed" : ((data.aprendizado.alertas_qualidade || []).length ? "attention" : "pending")) : "pending") : "restricted"],
    ["opinion", "Parecer humano", data.risco?.avaliado_em ? "completed" : "pending"],
    ["report", "Relatório completo", data.relatorio_completo.gerado ? "completed" : "pending"],
  ];
  progress.innerHTML = steps.map(([id, title, state], index) => `<li class="${state}"><a href="#step-${id}"><i>${state === "completed" ? "✓" : index + 1}</i><span>${escapeHtml(title)}</span></a></li>`).join("");
}

function render(data) {
  analysis = data;
  renderSummary(data);
  renderProgress(data);
  sections.innerHTML = [renderValidation(data), renderRisk(data), renderLearning(data), renderAgent(data), renderOpinion(data), renderReport(data)].join("");
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

function openRegistrabilityForm() {
  registrabilityForm.reset();
  const saved = analysis?.validacao?.dados_complementares || {};
  registrabilityForm.querySelectorAll("[name]").forEach(field => {
    if (!(field.name in saved) || saved[field.name] === null) return;
    field.value = field.dataset.boolean !== undefined
      ? String(Boolean(saved[field.name]))
      : saved[field.name];
  });
  document.querySelector("#registrability-save-message").textContent = "";
  registrabilityDialog.showModal();
}

function registrabilityPayload() {
  const payload = {};
  registrabilityForm.querySelectorAll("[name]").forEach(field => {
    if (field.dataset.boolean !== undefined) {
      payload[field.name] = field.value === "" ? null : field.value === "true";
    } else {
      payload[field.name] = field.value.trim() || null;
    }
  });
  payload.deposito_realizado = payload.deposito_realizado === true;
  return payload;
}

registrabilityForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = registrabilityForm.querySelector("button[type=submit]");
  const saveMessage = document.querySelector("#registrability-save-message");
  button.disabled = true;
  saveMessage.textContent = "Salvando e recalculando…";
  try {
    await sendJson(`/v1/admin/analises/${encodeURIComponent(pesquisaId)}/dados-complementares`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(registrabilityPayload()),
    });
    registrabilityDialog.close();
    showMessage("Dados complementares salvos e matriz recalculada.", "success");
    await load();
  } catch (error) {
    saveMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

sections.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target; const values = new FormData(form); const button = form.querySelector("button[type=submit]"); button.disabled = true;
  try {
    if (form.id === "risk-review-form") await sendJson(`/v1/admin/fase3/avaliacoes/${form.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nivel_humano: values.get("nivel_humano"), avaliador: values.get("avaliador"), observacoes_humanas: values.get("observacoes_humanas") }) });
    if (form.id === "learning-review-form") await sendJson(`/v1/admin/aprendizado/previsoes/${form.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nivel_humano: values.get("nivel_humano"), avaliador: values.get("avaliador"), observacoes: values.get("observacoes_humanas") }) });
    showMessage("Etapa atualizada com sucesso.", "success"); await load();
  } catch (error) { showMessage(error.message, "error"); button.disabled = false; }
});
sections.addEventListener("click", async event => {
  const help = event.target.closest(".context-help");
  if (help) { helpDrawer.showModal(); const target = document.querySelector(`#help-${help.dataset.help}`); if (target) { target.open = true; target.scrollIntoView({ block: "start" }); } return; }
  if (event.target.closest("#complete-official-analysis")) { openRegistrabilityForm(); return; }
  const reconcile = event.target.closest("#reconcile-agent-outcome");
  if (reconcile) {
    reconcile.disabled = true;
    try {
      await sendJson(`/v1/admin/analises/${encodeURIComponent(pesquisaId)}/reconciliar-resultado`, { method: "POST" });
      showMessage("Consulta ao resultado real concluída.", "success"); await load();
    } catch (error) { showMessage(error.message, "error"); reconcile.disabled = false; }
    return;
  }
  const report = event.target.closest("#generate-full-report");
  if (!report) return;
  report.disabled = true;
  try {
    await downloadFullReport();
    showMessage("Relatório completo gerado e registrado.", "success"); await load();
  } catch (error) { showMessage(error.message, "error"); report.disabled = false; }
});

document.querySelector("#analysis-help-open").addEventListener("click", () => helpDrawer.showModal());
document.querySelector("#analysis-help-close").addEventListener("click", () => helpDrawer.close());
helpDrawer.addEventListener("click", event => { if (event.target === helpDrawer) helpDrawer.close(); });
document.querySelector("#registrability-close").addEventListener("click", () => registrabilityDialog.close());
document.querySelector("#registrability-cancel").addEventListener("click", () => registrabilityDialog.close());
registrabilityDialog.addEventListener("click", event => { if (event.target === registrabilityDialog) registrabilityDialog.close(); });
load().catch(error => { showMessage(error.message, "error"); sections.innerHTML = `<p class="analysis-empty">${escapeHtml(error.message)}</p>`; });

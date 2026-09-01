const pesquisaId = location.pathname.split("/").filter(Boolean).pop();
// A pagina publica do relatorio (/relatorios/{id}) exige a chave de integracao
// do tenant padrao via query string, mesmo para quem ja esta autenticado como
// operador -- e' o mesmo gate que protege o formulario publico de pesquisa.
const chaveIntegracaoPromise = fetch("/v1/tenant/branding")
  .then((response) => (response.ok ? response.json() : {}))
  .then((tenant) => tenant.chave_integracao || null)
  .catch(() => null);
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
const nivelRiscoLabels = {
  baixo: "Baixo risco — favorável ao registro",
  moderado: "Moderado — viável com ressalvas",
  alto: "Alto risco — desfavorável ao registro",
  critico: "Crítico — fortemente desfavorável",
};
const veredictoHumanoLabels = {
  favoravel: "Favorável",
  desfavoravel: "Desfavorável",
  inconclusiva: "Inconclusiva",
};
const diretrizLabels = {
  deposito_imediato: "Depósito imediato",
  ajuste_especificacao: "Ajuste de especificação",
  adequacao_mista: "Adequação de logotipo/mista",
  inviavel_rebranding: "Inviável — sugerir rebranding",
};
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
  { level: "alto", range: "50–74", title: "Alto risco", meaning: "Impedimentos relevantes identificados", detail: "Há sinais fortes de conflito que exigem avaliação técnica." },
  { level: "critico", range: "75–100", title: "Risco crítico", meaning: "Conflitos substantivos identificados", detail: "Os conflitos encontrados exigem revisão jurídica antes de qualquer conclusão." },
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
  const linkRelatorioPublico = document.querySelector("#analysis-public-report");
  linkRelatorioPublico.href = `/relatorios/${encodeURIComponent(data.pesquisa.id)}`;
  chaveIntegracaoPromise.then((chave) => {
    if (chave) linkRelatorioPublico.href += `?chave_integracao=${encodeURIComponent(chave)}`;
  });
  document.querySelector("#analysis-summary").innerHTML = `
    <div><span>Cliente</span><strong title="${escapeHtml(lead?.nome || "Sem vínculo")}">${escapeHtml(lead?.nome || "Sem vínculo")}</strong></div>
    <div><span>Empresa</span><strong title="${escapeHtml(lead?.empresa || "Não informada")}">${escapeHtml(lead?.empresa || "Não informada")}</strong></div>
    <div><span>Contato</span><strong title="${escapeHtml(lead?.email || "Não informado")}">${escapeHtml(lead?.email || "Não informado")}</strong></div>
    <div><span>Pesquisa criada</span><strong>${formatDate(data.pesquisa.criado_em)}</strong></div>
    <div><span>Relatório completo</span><strong>${data.relatorio_completo.gerado ? `Gerado em ${formatDate(data.relatorio_completo.gerado_em)}` : "Não gerado"}</strong></div>`;
}

function renderUnified(data) {
  const item = data.analise_consolidada;
  const denied = permissionState(data.permissoes.validacao_visualizar && data.permissoes.risco_visualizar);
  if (denied) return step({ id: "unified", number: 1, title: "Análise de registrabilidade", subtitle: "Conclusão única baseada nas evidências", ...denied });
  if (!item) return step({ id: "unified", number: 1, title: "Análise de registrabilidade", subtitle: "Conclusão única baseada nas evidências", state: "pending", stateLabel: "Aguardando pesquisa", body: '<p class="analysis-empty">Abra o resultado automático para preparar as evidências desta pesquisa.</p>', open: true });
  const result = item.conclusao_preliminar;
  const presentation = item.apresentacao || {};
  const findings = (items) => (items || []).map(f => `<li><strong>${escapeHtml(f.criterio)}</strong><p>${escapeHtml(f.justificativa)}</p>${f.evidencia ? `<p><small>Evidência: ${escapeHtml(f.evidencia)}</small></p>` : ""}${f.referencia ? `<small>Referência: ${escapeHtml(f.referencia)}</small>` : ""}</li>`).join("");
  const impediments = findings(presentation.impedimentos);
  const attention = findings(presentation.pontos_atencao);
  const pending = (item.pendencias || []).map(p => `<li><strong>${escapeHtml(p.criterio)}</strong> — ${escapeHtml(p.descricao)}</li>`).join("");
  const reasons = (presentation.fundamentos_tecnicos || []).map(reason => `<li>${escapeHtml(reason)}</li>`).join("");
  const conflicts = (item.anterioridades || []).map(c => `<tr><td>${escapeHtml(c.numero)}</td><td>${escapeHtml(c.titulo || "Sem título")}</td><td>${escapeHtml(c.situacao || "Não informada")}</td><td>${escapeHtml(c.relevancia_rotulo || "Não informada")}</td></tr>`).join("");
  const estimate = item.estatistica?.estimativa;
  const stats = item.estatistica?.disponivel && estimate ? `<p>Indicador histórico: <strong>${percent(estimate.probabilidade_deferimento)}</strong> · faixa ${percent(estimate.probabilidade_inferior)} a ${percent(estimate.probabilidade_superior)}.</p>` : "";
  const refresh = data.permissoes.validacao_revisar ? '<button id="consolidate-analysis" class="secondary-button" type="button">Atualizar análise consolidada</button>' : "";
  const diretriz = item.diretriz_acao ? `<p><strong>Diretriz de ação recomendada:</strong> ${escapeHtml(diretrizLabels[item.diretriz_acao.codigo] || label(item.diretriz_acao.codigo))}${item.diretriz_acao.origem === "humana" ? ' <span class="situacao-badge situacao-favoravel">definida pelo especialista</span>' : ""}</p>` : "";
  const disclaimers = (item.disclaimers || []).map(d => `<li><strong>${escapeHtml(d.titulo)}:</strong> ${escapeHtml(d.texto)}</li>`).join("");
  const sobrescritaPeloHumano = presentation.situacao?.origem === "parecer_humano";
  const iaPreliminar = sobrescritaPeloHumano && presentation.situacao_ia_preliminar ? `<p class="analysis-empty">Pré-análise automática da IA (antes do parecer): <span class="situacao-badge situacao-${escapeHtml(presentation.situacao_ia_preliminar.codigo)}">${escapeHtml(presentation.situacao_ia_preliminar.rotulo)}</span></p>` : "";
  return step({ id: "unified", number: 1, title: "Análise de registrabilidade", subtitle: `Versão ${item.versao_relatorio} · ${item.versao_motor}`, state: item.revisao.validada ? "completed" : "attention", stateLabel: item.revisao.validada ? "Validada por especialista" : "Preliminar", open: true, body: `
    <section class="unified-conclusion"><p class="eyebrow">${sobrescritaPeloHumano ? "Veredito do especialista (sobrescreve a IA)" : "Conclusão automática preliminar"}</p><h3>Situação: <span class="situacao-badge situacao-${escapeHtml(presentation.situacao?.codigo || "inconclusiva")}">${escapeHtml(presentation.situacao?.rotulo || "Inconclusiva - requer revisão")}</span></h3><p>${escapeHtml(presentation.situacao?.explicacao)}</p>${iaPreliminar}<p>${escapeHtml(item.titulo)}</p><p>${escapeHtml(item.recomendacao)}</p>${diretriz}
    <div class="analysis-grid"><div class="analysis-stat"><span>Risco técnico</span><strong>${escapeHtml(label(result.nivel_risco || "não calculado"))}</strong></div><div class="analysis-stat"><span>Cobertura dos critérios</span><strong>${percent(result.cobertura)}</strong></div><div class="analysis-stat"><span>Pendências</span><strong>${item.pendencias.length}</strong></div></div>
    ${impediments ? `<h3>Possíveis impedimentos (${presentation.impedimentos.length})</h3><ul class="analysis-alerts">${impediments}</ul>` : ""}
    ${attention ? `<h3>Pontos de atenção (${presentation.pontos_atencao.length})</h3><ul class="analysis-alerts">${attention}</ul>` : ""}
    ${reasons ? `<h3>Outros fundamentos técnicos</h3><ul class="analysis-alerts">${reasons}</ul>` : ""}<p class="analysis-empty">${escapeHtml(result.aviso)}</p></section>
    <section class="analysis-stat"><h3>Apoio estatístico (informação do sistema)</h3>${stats}<p>${escapeHtml(presentation.mensagem_apoio)}</p></section>
    ${disclaimers ? `<section class="analysis-stat"><h3>Disclaimers estratégicos</h3><ul class="analysis-alerts">${disclaimers}</ul></section>` : ""}
    ${item.legado ? '<p class="analysis-empty">Pesquisa histórica: esta leitura é preliminar. Atualize a análise e registre um novo parecer para validar a versão consolidada.</p>' : ""}
    <h3>Marca e contexto</h3><p><strong>${escapeHtml(item.analise_conjunto.marca)}</strong> · ${escapeHtml(item.analise_conjunto.atividade || "Atividade não informada")}</p><p>${escapeHtml(item.analise_conjunto.aviso)}</p>
    ${pending ? `<h3>Informações pendentes</h3><ul class="analysis-alerts">${pending}</ul>` : ""}
    <details class="unified-technical"><summary>Evidências e critérios técnicos</summary>
    ${officialMatrix(item.matriz, data.permissoes.validacao_revisar)}
    <h3>Anterioridades encontradas</h3><div class="analysis-table-scroll"><table class="analysis-conflicts"><thead><tr><th>Processo</th><th>Marca</th><th>Situação</th><th>Relevância</th></tr></thead><tbody>${conflicts || '<tr><td colspan="4">Nenhuma anterioridade exibida.</td></tr>'}</tbody></table></div>
    <p>Base: RPI ${escapeHtml(item.fontes.ultima_rpi || "não informada")}.</p></details>
    <div class="analysis-action-row">${refresh}</div>` });
}

function renderOpinion(data) {
  const item = data.analise_consolidada;
  const opinion = item?.parecer_humano;
  const reviewed = Boolean(opinion);
  const composite = item?.score_composto;
  const compositeBlock = composite?.score_final != null ? `<div class="analysis-grid"><div class="analysis-stat"><span>Score IA</span><strong>${composite.score_ia}</strong></div><div class="analysis-stat"><span>Score humano</span><strong>${composite.score_humano}</strong></div><div class="analysis-stat"><span>Score final (50/50)</span><strong>${composite.score_final} · ${escapeHtml(label(composite.nivel_final))}</strong></div></div>` : "";
  const veredictoBlock = opinion?.veredito_humano ? `<div class="analysis-grid"><div class="analysis-stat"><span>Veredito final (sobrescreve a Situação)</span><strong>${escapeHtml(veredictoHumanoLabels[opinion.veredito_humano])}</strong></div></div>` : "";
  const saved = opinion ? `<div class="analysis-grid"><div class="analysis-stat"><span>Classificação de risco (especialista)</span><strong>${escapeHtml(nivelRiscoLabels[opinion.nivel] || label(opinion.nivel))}</strong></div><div class="analysis-stat"><span>Avaliador</span><strong>${escapeHtml(opinion.avaliador_nome || opinion.avaliador)}</strong></div><div class="analysis-stat"><span>Data</span><strong>${formatDate(opinion.avaliado_em)}</strong></div></div>${veredictoBlock}${compositeBlock}<div class="analysis-opinion">${escapeHtml(opinion.observacoes)}</div>` : '<p class="analysis-empty">Revise as evidências e registre um único parecer para esta análise. A classificação humana não altera o resultado automático da IA, mas passa a compor o score final ponderado (50% IA + 50% especialista) e pode sobrescrever a diretriz de ação sugerida.</p>';
  const form = item && data.permissoes.risco_revisar && data.permissoes.validacao_visualizar ? `<form id="consolidated-review-form" class="analysis-action-form"><label>Classificação de risco do especialista <small>(o RISCO que você identifica, não a confiança na conclusão — marque "baixo" se considera o registro viável)</small><select name="nivel_humano" required><option value="">Selecione</option>${["baixo","moderado","alto","critico"].map(value => `<option value="${value}" ${opinion?.nivel === value ? "selected" : ""}>${nivelRiscoLabels[value]}</option>`).join("")}</select></label><label>Veredito final <small>(opcional — quando preenchido, SOBRESCREVE a Situação exibida ao cliente no relatório, no lugar da pré-análise da IA)</small><select name="veredito_humano"><option value="">Manter leitura automática da IA</option>${Object.entries(veredictoHumanoLabels).map(([value, text]) => `<option value="${value}" ${opinion?.veredito_humano === value ? "selected" : ""}>${text}</option>`).join("")}</select></label><label>Diretriz de ação (opcional — sobrescreve a sugestão automática)<select name="diretriz_acao_humana"><option value="">Manter sugestão automática</option>${Object.entries(diretrizLabels).map(([value, text]) => `<option value="${value}" ${opinion?.diretriz_acao_humana === value ? "selected" : ""}>${text}</option>`).join("")}</select></label><p>Avaliador: ${escapeHtml(data.usuario.nome)}</p><label class="wide">Parecer e justificativa<textarea name="observacoes_humanas" minlength="3" maxlength="4000" rows="5" required>${escapeHtml(opinion?.observacoes || "")}</textarea></label><p class="wide">Salvar cria uma nova versão e exige validação formal novamente.</p><button class="primary-button" type="submit">Salvar parecer único</button></form>` : "";
  return step({ id: "opinion", number: 2, title: "Parecer humano", subtitle: "Síntese profissional vinculada à versão", state: reviewed ? "completed" : "pending", stateLabel: reviewed ? "Registrado" : "Pendente", body: saved + form });
}

function renderReport(data) {
  const report = data.relatorio_completo;
  const workflow = data.workflow;
  const validated = Boolean(data.analise_consolidada?.revisao.validada);
  const action = data.permissoes.relatorio_gerar && report.base_disponivel && validated ? `<button id="generate-full-report" class="primary-button" type="button">${report.gerado ? "Baixar completo novamente" : "Gerar relatório validado"}</button>` : "";
  const transitions = {
    PENDING_REVIEW: [["START_REVIEW", "Iniciar revisão"]],
    IN_REVIEW: [["VALIDATE", "Validar versão"], ["REQUEST_CHANGES", "Solicitar ajustes"]],
    CHANGES_REQUESTED: [["START_REVIEW", "Retomar revisão"]],
    VALIDATED: [["REOPEN", "Reabrir análise"]],
  }[workflow.state] || [];
  const allowedTransitions = transitions.filter(([key]) => key !== "VALIDATE" || data.permissoes.workflow_validar);
  const workflowForm = data.permissoes.workflow_revisar && allowedTransitions.length ? `<form id="workflow-form" class="analysis-action-form"><label>Ação<select name="action" required>${allowedTransitions.map(([value, text]) => `<option value="${value}">${text}</option>`).join("")}</select></label><label class="wide">Notas da revisão<textarea name="notes" minlength="3" maxlength="4000" rows="3" placeholder="Registre a justificativa da transição"></textarea></label><button class="primary-button" type="submit">Atualizar workflow</button></form>` : "";
  const history = (workflow.history || []).map(item => `<li><strong>${escapeHtml(item.after?.state || item.details?.action || "Tentativa")}</strong> · ${escapeHtml(item.actor)} · ${formatDate(item.created_at)}${item.success ? "" : ` · bloqueada: ${escapeHtml(item.details?.reason || "regra do workflow")}`}</li>`).join("");
  const reportState = validated ? "completed" : "attention";
  const stateLabel = validated ? "Versão validada" : "Revisão pendente";
  return step({ id: "report", number: 3, title: "Workflow humano e relatório", subtitle: "Validação formal vinculada à versão atual", state: reportState, stateLabel, body: `<div class="analysis-grid"><div class="analysis-stat"><span>Estado da análise</span><strong>${escapeHtml(label(workflow.state))}</strong></div><div class="analysis-stat"><span>Versão em revisão</span><strong>${escapeHtml(workflow.report_version || "—")}</strong></div><div class="analysis-stat"><span>Validado por</span><strong>${escapeHtml(workflow.validated_by || "Pendente")}</strong></div><div class="analysis-stat"><span>Validado em</span><strong>${formatDate(workflow.validated_at)}</strong></div></div>${workflow.notes ? `<div class="analysis-opinion">${escapeHtml(workflow.notes)}</div>` : ""}<p class="analysis-empty">Enquanto a revisão obrigatória estiver pendente, o documento não pode ser emitido como relatório completo validado.</p>${workflowForm}${history ? `<h3>Histórico do workflow</h3><ul class="analysis-alerts">${history}</ul>` : ""}<div class="analysis-action-row">${action}</div>` });
}

function renderProgress(data) {
  const item = data.analise_consolidada;
  const steps = [
    ["unified", "Análise única", item ? (item.revisao.validada ? "completed" : "attention") : "pending"],
    ["opinion", "Parecer humano", item?.parecer_humano ? "completed" : "pending"],
    ["report", "Validação e relatório", item?.revisao.validada ? "completed" : "pending"],
  ];
  progress.innerHTML = steps.map(([id, title, state], index) => `<li class="${state}"><a href="#step-${id}"><i>${state === "completed" ? "✓" : index + 1}</i><span>${escapeHtml(title)}</span></a></li>`).join("");
}

function render(data) {
  analysis = data;
  renderSummary(data);
  renderProgress(data);
  sections.innerHTML = [renderUnified(data), renderOpinion(data), renderReport(data)].join("");
  if (data.permissoes.relatorio_gerar && data.relatorio_completo.base_disponivel && !sections.querySelector("#generate-full-report")) {
    const actions = sections.querySelector("#step-report .analysis-action-row");
    if (actions) actions.innerHTML = '<button id="generate-full-report" class="primary-button" type="button">Gerar relatório preliminar</button>';
  }
  const reportNotice = sections.querySelector("#step-report .analysis-empty");
  if (reportNotice && !data.analise_consolidada?.revisao.validada) reportNotice.textContent = "A revisão humana continua pendente, mas o relatório preliminar pode ser gerado agora. A versão preliminar é indicativa e não constitui parecer jurídico validado.";
}

async function load() {
  showMessage("Carregando análise consolidada…");
  const response = await fetch(`/v1/admin/analises/${encodeURIComponent(pesquisaId)}`);
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || "Não foi possível carregar a análise."); }
  const data = await response.json();
  render(data);
  showMessage("");
}
async function sendJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Operação não concluída.");
  return data;
}
async function downloadFullReport() {
  // Trava a versão: se a análise mudou (novo parecer, nova consolidação) desde que
  // a tela carregou, o backend recusa em vez de gerar um PDF que já não bate com o
  // que está sendo mostrado (achado da auditoria Fase 1, item 6).
  const versaoCarregada = analysis?.analise_consolidada?.versao_relatorio;
  const query = versaoCarregada ? `?versao_esperada=${encodeURIComponent(versaoCarregada)}` : "";
  const response = await fetch(`/v1/admin/pesquisas/${encodeURIComponent(pesquisaId)}/relatorio-completo.pdf${query}`, { method: "POST" });
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
    if (form.id === "consolidated-review-form") await sendJson(`/v1/admin/analises/${encodeURIComponent(pesquisaId)}/parecer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nivel_humano: values.get("nivel_humano"), observacoes_humanas: values.get("observacoes_humanas"), versao_relatorio: analysis.analise_consolidada.versao_relatorio, diretriz_acao_humana: values.get("diretriz_acao_humana") || null, veredito_humano: values.get("veredito_humano") || null }) });
    if (form.id === "workflow-form") await sendJson(`/v1/admin/analises/${encodeURIComponent(pesquisaId)}/workflow`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: values.get("action"), notes: values.get("notes")?.trim() || null, versao_relatorio: analysis.workflow.report_version }) });
    showMessage("Etapa atualizada com sucesso.", "success"); await load();
  } catch (error) { showMessage(error.message, "error"); button.disabled = false; }
});
sections.addEventListener("click", async event => {
  const help = event.target.closest(".context-help");
  if (help) { helpDrawer.showModal(); const target = document.querySelector(`#help-${help.dataset.help}`); if (target) { target.open = true; target.scrollIntoView({ block: "start" }); } return; }
  if (event.target.closest("#complete-official-analysis")) { openRegistrabilityForm(); return; }
  const consolidate = event.target.closest("#consolidate-analysis");
  if (consolidate) {
    consolidate.disabled = true;
    try {
      await sendJson(`/v1/admin/analises/${encodeURIComponent(pesquisaId)}/consolidar`, { method: "POST" });
      await load();
    } catch (error) { showMessage(error.message, "error"); consolidate.disabled = false; }
    return;
  }
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

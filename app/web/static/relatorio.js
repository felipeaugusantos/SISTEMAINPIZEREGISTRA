const reportId = location.pathname.split("/").filter(Boolean).pop();
const loading = document.querySelector("#report-loading");
const content = document.querySelector("#report-content");
const errorSection = document.querySelector("#report-error");
const pdfUrl = `/v1/pesquisas-marca/${encodeURIComponent(reportId)}/relatorio.pdf`;

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function dateLabel(value) {
  if (!value) return "Não informada";
  return new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" }).format(new Date(value.length === 10 ? `${value}T00:00:00Z` : value));
}

function ownersLabel(owners) {
  if (!owners?.length) return "Titular não informado";
  return owners.map((owner) => owner.nome).join(" · ");
}

function itemCard(item) {
  const classes = item.classificacoes.map((entry) => entry.sistema === "nice" ? `NCL ${entry.codigo}` : `${entry.sistema} ${entry.codigo}`);
  const reasons = item.criterios_encontro.map((reason) => `<span class="match-reason">${escapeHtml(reason)}</span>`).join("");
  const highRenown = item.alto_renome ? `<span class="high-renown-badge">Coincide com alto renome</span>` : "";
  const affinity = item.afinidade_classes;
  const affinityBadge = affinity ? `<span class="affinity-badge ${escapeHtml(affinity.nivel)}" title="${escapeHtml(affinity.justificativa)}">${escapeHtml(affinity.rotulo)}${affinity.revisao === "pendente" ? " · pendente de validação" : ""}</span>` : "";
  const normalizedStatus = item.situacao_normalizada ? `<p class="normalized-status">Leitura padronizada: ${escapeHtml(item.situacao_normalizada.replaceAll("_", " "))}</p>` : "";
  const relevance = `<span class="affinity-badge ${escapeHtml(item.relevancia || "baixa")}" title="${escapeHtml((item.justificativas_relevancia || []).join(" · "))}">${escapeHtml(item.relevancia_rotulo || "Baixa relevância aparente")}</span>`;
  const rpi = item.ultima_rpi ? `<p class="result-date">Última movimentação: RPI ${escapeHtml(item.ultima_rpi)} · ${dateLabel(item.data_ultima_rpi)}</p>` : "";
  return `<a class="result-card" href="${escapeHtml(item.url_detalhe || `/processos/${encodeURIComponent(item.numero)}`)}">
    <div><div class="result-topline"><span class="type-badge marca">Marca</span><span class="process-number">${escapeHtml(item.numero)}</span>${classes.length ? `<span class="class-badge">${escapeHtml(classes.join(", "))}</span>` : ""}${highRenown}</div>
    <h3 class="result-title">${escapeHtml(item.titulo || "Elemento nominativo não informado")}</h3><p class="result-owner">${escapeHtml(ownersLabel(item.titulares))}</p><div class="match-reasons" aria-label="Motivos da ocorrência">${relevance}${reasons}${affinityBadge}</div></div>
    <div class="result-meta"><p class="result-status">${escapeHtml(item.situacao || "Situação não informada")}</p>${normalizedStatus}<p class="result-date">Depósito: ${dateLabel(item.data_deposito)}</p>${rpi}</div></a>`;
}

async function loadReport() {
  try {
    const response = await fetch(`/v1/pesquisas-marca/${encodeURIComponent(reportId)}/relatorio`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    document.title = `Relatório Zé Registra — ${data.marca}`;
    document.querySelector("#report-brand").textContent = data.marca;
    document.querySelector("#report-description").textContent = `Pesquisa automática do nome completo e de variações relevantes. Atividade informada: ${data.atividade}.`;
    document.querySelector("#report-date").textContent = `Gerado em ${dateLabel(data.gerado_em || data.criado_em)}`;
    document.querySelector("#report-version").textContent = `Versão ${data.versao} · ${data.schema_versao}`;
    document.querySelector("#report-count").textContent = `${data.total} ocorrência${data.total === 1 ? "" : "s"}`;
    document.querySelector("#report-metrics").innerHTML = `<div><span>Total localizado</span><strong>${data.total}</strong></div><div><span>Exibidos</span><strong>${data.limite_exibido}</strong></div><div><span>Abrangência</span><strong>Nome, situação e classes</strong></div><div><span>Base atualizada até</span><strong>${data.ultima_rpi ? `RPI ${data.ultima_rpi}` : "Não informado"}</strong></div>`;
    const conclusion = data.conclusao;
    document.querySelector("#conclusion-title").textContent = conclusion?.titulo || "Resultado meramente indicativo";
    document.querySelector("#conclusion-summary").textContent = conclusion?.resumo || "A pesquisa não substitui uma análise profissional.";
    document.querySelector("#conclusion-review").textContent = conclusion?.revisao_humana_recomendada ? "Revisão humana recomendada antes de qualquer decisão." : "A ausência de conflito evidente não garante o registro.";
    const estimate = data.estimativa_registrabilidade;
    if (estimate) {
      const box = document.createElement("section");
      box.className = "report-classification learning-client-estimate";
      const factors = (estimate.fatores_principais || []).slice(0, 3).map((factor) => `${escapeHtml(factor.rotulo || (factor.atributo || "fator").replaceAll("_", " "))} (${escapeHtml(factor.efeito || "influência")})`).join(" · ");
      const lower = estimate.probabilidade_inferior ?? estimate.probabilidade_deferimento;
      const upper = estimate.probabilidade_superior ?? estimate.probabilidade_deferimento;
      box.innerHTML = `<div><p class="eyebrow">Estimativa estatística preliminar</p><h2>${Math.round(estimate.probabilidade_deferimento * 100)}% de deferimento no exame de mérito</h2></div>
        <p><strong>Faixa de incerteza:</strong> ${Math.round(lower * 100)}% a ${Math.round(upper * 100)}%.</p>
        <p>Nível ${escapeHtml(estimate.nivel.replaceAll("_", " "))} · confiança ${escapeHtml(estimate.confianca_rotulo)} (${Math.round(estimate.confianca * 100)}%) · cobertura ${Math.round(estimate.cobertura_entrada * 100)}%.</p>
        <p>Base: ${estimate.amostras_referencia} decisões · dados até ${dateLabel(estimate.corte_dados)} · modelo ${escapeHtml(estimate.modelo_versao)}.</p>
        ${factors ? `<p><strong>Fatores com maior influência:</strong> ${factors}</p>` : ""}
        <p class="status-message">Revisão profissional recomendada, mas não obrigatória para esta estimativa.</p>
        <p class="matrix-status">${escapeHtml(estimate.aviso)}</p>`;
      document.querySelector("#report-conclusion").after(box);
    } else {
      const box = document.createElement("section");
      box.className = "report-classification learning-client-estimate unavailable";
      const statusTitle = data.estimativa_status === "validacao_interna" ? "Em validação interna" : "Estimativa ainda indisponível";
      box.innerHTML = `<div><p class="eyebrow">Estimativa de registrabilidade</p><h2>${escapeHtml(statusTitle)}</h2></div>
        <p>${escapeHtml(data.estimativa_mensagem)}</p>
        <p class="matrix-status">A pontuação de conflito e a conclusão indicativa não representam percentual de chance de registro.</p>`;
      document.querySelector("#report-conclusion").after(box);
    }
    const evidence = data.evidencias_busca;
    document.querySelector("#search-evidence").innerHTML = evidence ? `<div class="activity-class"><strong>Expressão completa</strong><span>${escapeHtml(evidence.expressao_completa)}</span><small>${evidence.expressoes_completas} ocorrência(s)</small></div><div class="activity-class"><strong>Radicais</strong><span>${escapeHtml(evidence.radicais.join(", ") || "Nenhum")}</span><small>${evidence.ocorrencias_por_radical} ocorrência(s)</small></div><div class="activity-class"><strong>Variações consideradas</strong><span>${escapeHtml(evidence.variacoes.join(", ") || "Nenhuma")}</span><small>${escapeHtml(evidence.versao_algoritmo)}</small></div>` : "<p>Evidências não disponíveis nesta versão do relatório.</p>";
    const quality = data.qualidade_base;
    document.querySelector("#base-quality").textContent = quality ? `Qualidade da base: ${quality.status}. Seção V atualizada até RPI ${quality.ultima_rpi || "não informada"} (${dateLabel(quality.data_ultima_rpi)}). Cobertura dos depósitos: ${dateLabel(quality.deposito_mais_antigo)} a ${dateLabel(quality.deposito_mais_recente)}.` : "Qualidade da base não registrada nesta versão.";
    document.querySelector("#base-warnings").innerHTML = quality?.avisos?.map((warning) => `<p class="status-message">${escapeHtml(warning)}</p>`).join("") || "";
    document.querySelector("#activity-classes").innerHTML = data.classes_atividade.length ? data.classes_atividade.map((item) => `<div class="activity-class"><strong>Classe ${escapeHtml(item.codigo)}</strong><span>${escapeHtml(item.titulo)}</span><small>Identificada por: ${escapeHtml(item.termos_encontrados.join(", "))}</small></div>`).join("") : `<p>Não foi possível sugerir classes com segurança a partir da atividade informada.</p>`;
    document.querySelector("#matrix-status").textContent = data.matriz_afinidade_status === "validada" ? "Matriz de afinidade validada por especialista." : "Matriz inicial de afinidade pendente de validação por especialista.";
    document.querySelector("#report-results").innerHTML = data.itens.map(itemCard).join("");
    document.querySelector("#report-empty").textContent = data.total ? (data.total > data.limite_exibido ? `O relatório apresenta as ${data.limite_exibido} ocorrências mais relevantes.` : "") : "Nenhuma ocorrência foi localizada com os parâmetros informados.";
    document.querySelector("#download-report").href = pdfUrl;
    document.querySelector("#print-report").disabled = false;
    loading.hidden = true;
    content.hidden = false;
    const params = new URLSearchParams(location.search);
    if (params.get("download") === "1") {
      history.replaceState(null, "", location.pathname);
      document.querySelector("#download-status").textContent = "O download do PDF foi iniciado. Se ele não aparecer, use o botão ao lado.";
      requestAnimationFrame(() => window.location.assign(pdfUrl));
    }
  } catch {
    loading.hidden = true;
    errorSection.hidden = false;
  }
}

document.querySelector("#print-report").addEventListener("click", () => {
  window.location.assign(pdfUrl);
});
loadReport();

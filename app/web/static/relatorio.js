const reportId = location.pathname.split("/").filter(Boolean).pop();
const loading = document.querySelector("#report-loading");
const content = document.querySelector("#report-content");
const errorSection = document.querySelector("#report-error");
const fragmento = new URLSearchParams(location.hash.replace(/^#/, ""));
const relatorioToken = fragmento.get("token");
const reportHeaders = relatorioToken ? { "X-Report-Token": relatorioToken } : {};
const pdfUrl = `/v1/pesquisas-marca/${encodeURIComponent(reportId)}/relatorio.pdf`;
if (location.hash) history.replaceState(null, "", `${location.pathname}${location.search}`);

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
  const scoreFactors = (item.fatores_score_busca || []).map((factor) => `${factor.regra}: +${factor.peso}`).join(" · ");
  const searchScore = `<span class="class-badge" title="${escapeHtml(scoreFactors)}">Score de busca ${Number(item.score_busca || 0).toFixed(1)}</span>`;
  return `<a class="result-card" href="${escapeHtml(item.url_detalhe || `/processos/${encodeURIComponent(item.numero)}`)}">
    <div><div class="result-topline"><span class="type-badge marca">Marca</span><span class="process-number">${escapeHtml(item.numero)}</span>${classes.length ? `<span class="class-badge">${escapeHtml(classes.join(", "))}</span>` : ""}${searchScore}${highRenown}</div>
    <h3 class="result-title">${escapeHtml(item.titulo || "Elemento nominativo não informado")}</h3><p class="result-owner">${escapeHtml(ownersLabel(item.titulares))}</p><div class="match-reasons" aria-label="Motivos da ocorrência">${relevance}${reasons}${affinityBadge}</div></div>
    <div class="result-meta"><p class="result-status">${escapeHtml(item.situacao || "Situação não informada")}</p>${normalizedStatus}<p class="result-date">Depósito: ${dateLabel(item.data_deposito)}</p>${rpi}</div></a>`;
}

async function loadReport() {
  try {
    const response = await fetch(`/v1/pesquisas-marca/${encodeURIComponent(reportId)}/relatorio`, { headers: reportHeaders });
    if (!response.ok) throw new Error();
    const data = await response.json();
    document.title = `Relatório Zé Registra — ${data.marca}`;
    document.querySelector("#report-brand").textContent = data.marca;
    document.querySelector("#report-description").textContent = `Pesquisa automática do nome completo e de variações relevantes. Atividade informada: ${data.atividade}.`;
    document.querySelector("#report-date").textContent = `Gerado em ${dateLabel(data.gerado_em || data.criado_em)}`;
    document.querySelector("#report-version").textContent = `Versão ${data.versao} · ${data.schema_versao}`;
    document.querySelector("#report-count").textContent = `${data.total} ocorrência${data.total === 1 ? "" : "s"}`;
    document.querySelector("#report-metrics").innerHTML = `<div><span>Total localizado</span><strong>${data.total}</strong></div><div><span>Relatório entregue</span><strong>Resumo público</strong></div><div><span>Abrangência</span><strong>Nome, situação e classes</strong></div><div><span>Base atualizada até</span><strong>${data.ultima_rpi ? `RPI ${data.ultima_rpi}` : "Não informado"}</strong></div>`;
    const conclusion = data.conclusao;
    document.querySelector("#conclusion-title").textContent = conclusion?.titulo || "Resultado meramente indicativo";
    document.querySelector("#conclusion-summary").textContent = conclusion?.resumo || "A pesquisa não substitui uma análise profissional.";
    document.querySelector("#conclusion-review").textContent = conclusion?.revisao_humana_recomendada ? "Revisão humana recomendada antes de qualquer decisão." : "A ausência de conflito evidente não garante o registro.";
    const analise = data.analise_consolidada;
    if (analise) {
      // Veredito único (Favorável/Desfavorável/Inconclusiva) -- substitui a leitura
      // antiga de "prognostico_registrabilidade" (que podia mostrar "Atenção" como
      // se fosse um 4º veredito) como fonte de verdade para o cliente.
      const box = document.createElement("section");
      box.className = "report-classification prognostico";
      box.dataset.veredito = analise.situacao_codigo;
      const disclaimers = (analise.disclaimers || []).map((d) => `<li><strong>${escapeHtml(d.titulo)}:</strong> ${escapeHtml(d.texto)}</li>`).join("");
      const avaliadoPorEspecialista = analise.situacao_origem === "parecer_humano";
      box.innerHTML = `<div><p class="eyebrow">${avaliadoPorEspecialista ? "Situação da análise · avaliação de especialista" : "Situação da análise"}</p><h2>${escapeHtml(analise.titulo)}</h2><p class="prognostico-tag" data-veredito="${escapeHtml(analise.situacao_codigo)}">Situação: ${escapeHtml(analise.situacao_rotulo)}</p></div>
        <p>${escapeHtml(analise.situacao_explicacao)}</p>
        <p>${escapeHtml(analise.recomendacao)}</p>
        ${analise.diretriz_acao_rotulo ? `<p><strong>Diretriz de ação recomendada:</strong> ${escapeHtml(analise.diretriz_acao_rotulo)}</p>` : ""}
        ${disclaimers ? `<p><strong>Avisos importantes:</strong></p><ul class="prognostico-motivos">${disclaimers}</ul>` : ""}
        <p class="matrix-status">${avaliadoPorEspecialista ? "Situação revisada por especialista da equipe; " : "Análise preliminar automatizada; "}não constitui garantia de deferimento pelo INPI nem substitui avaliação jurídica especializada.</p>`;
      document.querySelector("#report-conclusion").after(box);
    } else if (data.prognostico_registrabilidade) {
      // Relatório legado, gerado antes da análise consolidada existir.
      const prognostico = data.prognostico_registrabilidade;
      const rotulos = { favoravel: "Favorável", atencao: "Atenção", desfavoravel: "Desfavorável" };
      const box = document.createElement("section");
      box.className = "report-classification prognostico";
      box.dataset.veredito = prognostico.veredito;
      const motivos = (prognostico.motivos || []).map((m) => `<li><strong>${escapeHtml(m.criterio)}:</strong> ${escapeHtml(m.conclusao)} <small>(${escapeHtml(m.referencia)})</small></li>`).join("");
      const pendencias = (prognostico.pendencias || []).map((p) => escapeHtml(p)).join(" · ");
      box.innerHTML = `<div><p class="eyebrow">Triagem determinística de registrabilidade</p><h2>${escapeHtml(prognostico.titulo)}</h2><p class="prognostico-tag" data-veredito="${escapeHtml(prognostico.veredito)}">Leitura técnica: ${escapeHtml(rotulos[prognostico.veredito] || prognostico.veredito)}</p></div>
        <p>${escapeHtml(prognostico.resumo)}</p>
        ${motivos ? `<p><strong>Motivos identificados:</strong></p><ul class="prognostico-motivos">${motivos}</ul>` : ""}
        ${pendencias ? `<p><strong>Ainda dependem de avaliação:</strong> ${pendencias}.</p>` : ""}
        <p class="matrix-status">${escapeHtml(prognostico.ressalva)}</p>`;
      document.querySelector("#report-conclusion").after(box);
    }
    const evidence = data.evidencias_busca;
    document.querySelector("#search-evidence").innerHTML = evidence ? `<div class="activity-class"><strong>Expressão completa</strong><span>${escapeHtml(evidence.expressao_completa)}</span><small>${evidence.expressoes_completas} ocorrência(s)</small></div><div class="activity-class"><strong>Radicais</strong><span>${escapeHtml(evidence.radicais.join(", ") || "Nenhum")}</span><small>${evidence.ocorrencias_por_radical} ocorrência(s)</small></div><div class="activity-class"><strong>Variações consideradas</strong><span>${escapeHtml(evidence.variacoes.join(", ") || "Nenhuma")}</span><small>${escapeHtml(evidence.versao_algoritmo)}</small></div>` : "<p>Evidências não disponíveis nesta versão do relatório.</p>";
    const quality = data.qualidade_base;
    document.querySelector("#base-quality").textContent = quality ? `Qualidade da base: ${quality.status}. Seção V atualizada até RPI ${quality.ultima_rpi || "não informada"} (${dateLabel(quality.data_ultima_rpi)}). Cobertura dos depósitos: ${dateLabel(quality.deposito_mais_antigo)} a ${dateLabel(quality.deposito_mais_recente)}.` : "Qualidade da base não registrada nesta versão.";
    document.querySelector("#base-warnings").innerHTML = quality?.avisos?.map((warning) => `<p class="status-message">${escapeHtml(warning)}</p>`).join("") || "";
    document.querySelector("#activity-classes").innerHTML = data.classes_atividade.length ? data.classes_atividade.map((item) => `<div class="activity-class"><strong>Classe ${escapeHtml(item.codigo)}</strong><span>${escapeHtml(item.titulo)}</span><small>Identificada por: ${escapeHtml(item.termos_encontrados.join(", "))}</small></div>`).join("") : `<p>Não foi possível sugerir classes com segurança a partir da atividade informada.</p>`;
    document.querySelector("#matrix-status").textContent = "A afinidade entre classes é processada na análise técnica interna.";
    document.querySelector("#report-results").innerHTML = data.total
      ? `<article class="report-public-summary"><strong>${data.total} ocorrência${data.total === 1 ? "" : "s"} localizada${data.total === 1 ? "" : "s"}</strong><p>Os processos, fundamentos e comparações detalhadas ficam reservados à análise interna da equipe.</p></article>`
      : "";
    document.querySelector("#report-empty").textContent = data.total ? "Este resumo não expõe a lista técnica completa." : "Nenhuma ocorrência foi localizada com os parâmetros informados.";
    document.querySelector("#download-report").href = pdfUrl;
    document.querySelector("#print-report").disabled = false;
    loading.hidden = true;
    content.hidden = false;
    const params = new URLSearchParams(location.search);
    if (params.get("download") === "1") {
      history.replaceState(null, "", location.pathname);
      document.querySelector("#download-status").textContent = "O download do PDF foi iniciado. Se ele não aparecer, use o botão ao lado.";
      requestAnimationFrame(() => baixarPdf().catch(() => {
        document.querySelector("#download-status").textContent = "Não foi possível baixar o PDF. Gere um novo acesso ao relatório.";
      }));
    }
  } catch {
    loading.hidden = true;
    errorSection.hidden = false;
  }
}

async function baixarPdf(abrirNoNavegador = false) {
  const response = await fetch(pdfUrl, { headers: reportHeaders });
  if (!response.ok) throw new Error("Não foi possível baixar o PDF.");
  const url = URL.createObjectURL(await response.blob());
  if (abrirNoNavegador) {
    window.location.assign(url);
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    return;
  }
  const link = document.createElement("a");
  link.href = url;
  link.download = `resumo-${reportId}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

document.querySelector("#print-report").addEventListener("click", () => baixarPdf(true).catch(() => {
  document.querySelector("#download-status").textContent = "Não foi possível baixar o PDF. Gere um novo acesso ao relatório.";
}));
document.querySelector("#download-report").addEventListener("click", event => {
  event.preventDefault();
  baixarPdf().catch(() => {
    document.querySelector("#download-status").textContent = "Não foi possível baixar o PDF. Gere um novo acesso ao relatório.";
  });
});
loadReport();

const list = document.querySelector("#explanation-list");
const message = document.querySelector("#phase4-message");
const levelLabels = { baixo: "Baixo", moderado: "Moderado", alto: "Alto", critico: "Crítico" };
const statusLabels = {
  gerada: "Gerada para uso interno",
  aguardando_revisao: "Aguardando revisão obrigatória",
  aprovada: "Aprovada por especialista",
  rejeitada: "Rejeitada por especialista",
  falhou_validacao: "Bloqueada pela validação",
  falhou_provedor: "Falha no provedor",
};
const limitationLabels = {
  indicativo_nao_conclusivo: "Resultado indicativo, não conclusivo.",
  nao_substitui_analise_humana: "Não substitui a análise de um especialista.",
  baseado_apenas_no_motor_deterministico: "Explicação baseada somente no motor determinístico.",
};

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function referenceMap(item) {
  return new Map(item.conflitos_referencia.map((reference) => [reference.conflito_id, reference]));
}

function explanationContent(item) {
  if (!item.saida_estruturada) {
    return `<p class="comparison-state divergent">${escapeHtml(item.erro || "Sem saída válida.")}</p>`;
  }
  const output = item.saida_estruturada;
  const references = referenceMap(item);
  const conflicts = output.conflitos.map((conflict) => {
    const reference = references.get(conflict.conflito_id) || {};
    return `<details class="risk-conflict">
      <summary><span>${escapeHtml(reference.numero || `Conflito ${conflict.conflito_id}`)} · ${escapeHtml(reference.titulo || "Marca sem nome")}</span></summary>
      <p>${escapeHtml(conflict.explicacao)}</p>
      <small>Regras: ${conflict.regras_referenciadas.map(escapeHtml).join(", ")}</small>
    </details>`;
  }).join("");
  return `<section class="ai-explanation-copy">
    <h3>Resumo executivo</h3><p>${escapeHtml(output.resumo_executivo)}</p>
    <h3>Leitura da pontuação</h3><p>${escapeHtml(output.leitura_pontuacao)}</p>
    <h3>Conflitos explicados</h3>${conflicts}
    <ul>${output.limitacoes.map((key) => `<li>${escapeHtml(limitationLabels[key] || key)}</li>`).join("")}</ul>
  </section>`;
}

function reviewForm(item) {
  if (!item.saida_estruturada) return "";
  return `<form class="ai-review-form" data-id="${item.id}">
    <h3>${item.revisao_obrigatoria ? "Revisão humana obrigatória" : "Revisão humana opcional"}</h3>
    <label><span>Decisão</span><select name="decisao" required>
      <option value="">Selecione</option>
      <option value="aprovada" ${item.decisao_revisao === "aprovada" ? "selected" : ""}>Aprovar</option>
      <option value="rejeitada" ${item.decisao_revisao === "rejeitada" ? "selected" : ""}>Rejeitar</option>
    </select></label>
    <label><span>Especialista</span><input name="revisor" required minlength="2" maxlength="150" value="${escapeHtml(item.revisor || "")}" /></label>
    <label><span>Observações</span><textarea name="observacoes" required minlength="3" maxlength="2000">${escapeHtml(item.observacoes_revisao || "")}</textarea></label>
    <button class="primary-button" type="submit">Registrar revisão</button>
  </form>`;
}

function card(item) {
  return `<article class="risk-review-card">
    <header>
      <div><p class="eyebrow">${escapeHtml(statusLabels[item.status] || item.status)}</p><h2>${escapeHtml(item.marca_pesquisada)}</h2><p>${escapeHtml(item.modelo)} · ${escapeHtml(item.versao_prompt)}</p></div>
      <div class="risk-score ${escapeHtml(item.nivel)}"><strong>${item.pontuacao}</strong><span>${escapeHtml(levelLabels[item.nivel])}</span></div>
    </header>
    <div class="risk-card-grid">${explanationContent(item)}${reviewForm(item)}</div>
  </article>`;
}

async function load() {
  const response = await fetch("/v1/admin/fase4");
  if (!response.ok) throw new Error("Não foi possível carregar as explicações.");
  const data = await response.json();
  document.querySelector("#ai-state").textContent = `${data.modelo} · ${data.habilitada ? "habilitada" : "desativada"} · rollout ${data.rollout_percentual}%`;
  document.querySelector("#metric-evaluations").textContent = data.total_avaliacoes;
  document.querySelector("#metric-generated").textContent = data.total_explicacoes;
  document.querySelector("#metric-pending").textContent = data.aguardando_revisao;
  document.querySelector("#metric-failed").textContent = data.falhas;
  list.innerHTML = data.itens.length
    ? data.itens.map(card).join("")
    : `<div class="status-message">Gere a primeira explicação a partir do painel do motor de risco.</div>`;
}

list.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const formData = new FormData(form);
  message.textContent = "Registrando revisão humana...";
  const response = await fetch(`/v1/admin/fase4/explicacoes/${form.dataset.id}/revisao`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      decisao: formData.get("decisao"),
      revisor: formData.get("revisor"),
      observacoes: formData.get("observacoes"),
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    message.textContent = data.detail || "Não foi possível registrar a revisão.";
    return;
  }
  message.textContent = "Revisão humana registrada.";
  await load();
});

load().catch((error) => {
  message.textContent = error.message;
});

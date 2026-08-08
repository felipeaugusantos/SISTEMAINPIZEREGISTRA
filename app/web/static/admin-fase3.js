const list = document.querySelector("#risk-list");
const message = document.querySelector("#phase3-message");
const levelLabels = { baixo: "Baixo", moderado: "Moderado", alto: "Alto", critico: "Crítico" };

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function dateTimeLabel(value) {
  if (!value) return "Não avaliado";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function factors(conflict) {
  return conflict.fatores.map((factor) => `<li><strong>${factor.pontos > 0 ? "+" : ""}${factor.pontos}</strong> ${escapeHtml(factor.evidencia)} <small>${escapeHtml(factor.regra)}</small></li>`).join("");
}

function conflicts(items) {
  if (!items.length) return `<p>Nenhum conflito pontuado.</p>`;
  return items.map((item) => `<details class="risk-conflict"><summary><span>${escapeHtml(item.numero)} · ${escapeHtml(item.titulo || "Marca sem nome")}</span><strong>${item.pontuacao} pontos</strong></summary><ul>${factors(item)}</ul></details>`).join("");
}

function card(item) {
  const agreement = item.concordancia === null ? "Aguardando avaliação humana" : item.concordancia ? "Motor e humano concordam" : "Divergência registrada";
  return `<article class="risk-review-card">
    <header><div><p class="eyebrow">${escapeHtml(item.empresa || item.contato_nome)}</p><h2>${escapeHtml(item.marca)}</h2><p>${escapeHtml(item.atividade)}</p></div><div class="risk-score ${escapeHtml(item.nivel)}"><strong>${item.pontuacao}</strong><span>${escapeHtml(levelLabels[item.nivel])}</span></div></header>
    <div class="risk-card-grid">
      <section><h3>Principais conflitos</h3>${conflicts(item.principais_conflitos)}</section>
      <form class="human-risk-form" data-id="${item.id}">
        <h3>Avaliação humana</h3>
        <label><span>Nível atribuído</span><select name="nivel_humano" required>
          <option value="">Selecione</option>
          ${Object.entries(levelLabels).map(([value, label]) => `<option value="${value}" ${item.nivel_humano === value ? "selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <label><span>Especialista</span><input name="avaliador" required minlength="2" maxlength="150" value="${escapeHtml(item.avaliador || "")}" /></label>
        <label><span>Fundamentação</span><textarea name="observacoes" required minlength="3" maxlength="2000">${escapeHtml(item.observacoes_humanas || "")}</textarea></label>
        <button class="primary-button" type="submit">Registrar comparação</button>
        <p class="comparison-state ${item.concordancia === false ? "divergent" : ""}">${escapeHtml(agreement)}${item.avaliado_em ? ` · ${dateTimeLabel(item.avaliado_em)}` : ""}</p>
        <button class="secondary-button ai-generate-button" type="button" data-ai-id="${item.id}">Gerar explicação interna com IA</button>
      </form>
    </div>
  </article>`;
}

async function load() {
  const response = await fetch("/v1/admin/fase3");
  if (!response.ok) throw new Error("Não foi possível carregar as avaliações.");
  const data = await response.json();
  document.querySelector("#engine-version").textContent = `${data.versao_motor} · ${data.modo}`;
  document.querySelector("#metric-calculated").textContent = data.total_calculadas;
  document.querySelector("#metric-reviewed").textContent = data.total_avaliadas_humanamente;
  document.querySelector("#metric-agree").textContent = data.total_concordantes;
  document.querySelector("#metric-rate").textContent = data.taxa_concordancia === null ? "—" : `${Math.round(data.taxa_concordancia * 100)}%`;
  list.innerHTML = data.itens.length ? data.itens.map(card).join("") : `<div class="status-message">As avaliações aparecerão depois que os relatórios forem abertos.</div>`;
}

list.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  message.textContent = "Registrando avaliação humana...";
  const response = await fetch(`/v1/admin/fase3/avaliacoes/${form.dataset.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nivel_humano: data.get("nivel_humano"),
      avaliador: data.get("avaliador"),
      observacoes_humanas: data.get("observacoes"),
    }),
  });
  if (!response.ok) {
    message.textContent = "Não foi possível registrar. Confira todos os campos.";
    return;
  }
  if (data.status === "falhou_validacao" || data.status === "falhou_provedor") {
    message.textContent = data.erro || "A geração foi bloqueada.";
    button.disabled = false;
    return;
  }
  message.textContent = "Comparação registrada.";
  await load();
});

list.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-ai-id]");
  if (!button) return;
  button.disabled = true;
  message.textContent = "Gerando explicação estruturada...";
  const response = await fetch(`/v1/admin/fase4/avaliacoes/${button.dataset.aiId}/gerar`, {
    method: "POST",
  });
  const data = await response.json();
  if (!response.ok) {
    message.textContent = data.detail || "Não foi possível gerar a explicação.";
    button.disabled = false;
    return;
  }
  message.textContent = data.revisao_obrigatoria
    ? "Explicação gerada e bloqueada para revisão humana."
    : "Explicação interna gerada.";
  window.location.href = "/admin/fase4";
});

load().catch((error) => {
  message.textContent = error.message;
});

const productionMessage = document.querySelector("#production-message");
const controlForm = document.querySelector("#production-control");
const rolloutInput = document.querySelector("#ai-rollout");
const rolloutOutput = document.querySelector("#ai-rollout-output");

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function dateTimeLabel(value) {
  if (!value) return "Ainda não alterado";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function percent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

function renderAudit(items) {
  const target = document.querySelector("#audit-list");
  target.innerHTML = items.length
    ? items.map((item) => `<tr>
        <td><time>${escapeHtml(dateTimeLabel(item.criado_em))}</time></td>
        <td><strong>${escapeHtml(item.ator)}</strong></td>
        <td>${escapeHtml(item.acao)}</td>
        <td><code>${escapeHtml(item.recurso)}</code></td>
        <td><span class="audit-result ${item.sucesso ? "success" : "failure"}">${item.sucesso ? "Sucesso" : `Falha ${item.status_http}`}</span></td>
      </tr>`).join("")
    : `<tr><td colspan="5">Nenhum evento administrativo registrado.</td></tr>`;
}

function render(data) {
  document.querySelector("#production-environment").textContent = `${data.ambiente} · ${data.modelo_ia}`;
  document.querySelector("#metric-requests").textContent = data.requisicoes_24h;
  document.querySelector("#metric-errors").textContent = percent(data.taxa_erros_24h);
  document.querySelector("#metric-duration").textContent = `${Math.round(data.duracao_media_ms_24h)} ms`;
  document.querySelector("#metric-divergence").textContent = percent(data.taxa_divergencia);
  document.querySelector("#metric-versions").textContent = data.relatorios_versionados;
  document.querySelector("#metric-versioned-reports").textContent = data.pesquisas_com_versao;
  document.querySelector("#ai-enabled").checked = data.ia_habilitada_operacional;
  rolloutInput.value = data.ia_rollout_percentual;
  rolloutOutput.textContent = `${data.ia_rollout_percentual}%`;
  document.querySelector("#master-key-state").textContent = data.chave_mestra_ia
    ? "Chave mestra disponível"
    : "Chave mestra desativada no ambiente";
  document.querySelector("#control-history").textContent = data.atualizado_por
    ? `Última alteração por ${data.atualizado_por} em ${dateTimeLabel(data.atualizado_em)} · ${data.justificativa || ""}`
    : "Controle ainda não alterado por um administrador.";
  renderAudit(data.auditoria);
}

async function loadProduction() {
  const response = await fetch("/v1/admin/producao");
  if (!response.ok) throw new Error("Não foi possível carregar a governança de produção.");
  render(await response.json());
}

rolloutInput.addEventListener("input", () => {
  rolloutOutput.textContent = `${rolloutInput.value}%`;
});

controlForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  productionMessage.textContent = "Salvando controle operacional…";
  const response = await fetch("/v1/admin/producao/controle", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ia_habilitada: document.querySelector("#ai-enabled").checked,
      ia_rollout_percentual: Number(rolloutInput.value),
      justificativa: document.querySelector("#production-reason").value,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    productionMessage.textContent = data.detail || "Não foi possível salvar o controle.";
    return;
  }
  productionMessage.textContent = "Controle de produção atualizado e auditado.";
  document.querySelector("#production-reason").value = "";
  render(data);
});

loadProduction().catch((error) => {
  productionMessage.textContent = error.message;
  productionMessage.classList.add("error");
});

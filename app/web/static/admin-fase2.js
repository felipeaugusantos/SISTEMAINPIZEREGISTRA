const list = document.querySelector("#affinity-list");
const message = document.querySelector("#phase2-message");

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function dateTimeLabel(value) {
  if (!value) return "Ainda não sincronizado";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function row(item) {
  const reviewed = item.status_revisao !== "pendente";
  return `<tr>
    <td><strong>${escapeHtml(item.classe_origem)} ↔ ${escapeHtml(item.classe_destino)}</strong><small>Versão ${escapeHtml(item.versao)}</small></td>
    <td><span class="review-status ${escapeHtml(item.nivel)}">${escapeHtml(item.nivel)}</span></td>
    <td>${escapeHtml(item.justificativa)}</td>
    <td><strong>${escapeHtml(item.status_revisao)}</strong>${reviewed ? `<small>${escapeHtml(item.revisor)} · ${dateTimeLabel(item.revisado_em)}</small><small>${escapeHtml(item.observacoes_revisao)}</small>` : ""}</td>
    <td>
      <form class="review-form" data-id="${item.id}">
        <input name="revisor" required minlength="2" maxlength="150" placeholder="Nome do especialista" value="${escapeHtml(item.revisor || "")}" />
        <textarea name="observacoes" required minlength="3" maxlength="1000" placeholder="Fundamento técnico da decisão">${escapeHtml(item.observacoes_revisao || "")}</textarea>
        <div><button class="secondary-button" name="decision" value="rejeitada" type="submit">Rejeitar</button><button class="primary-button" name="decision" value="aprovada" type="submit">Aprovar</button></div>
      </form>
    </td>
  </tr>`;
}

async function load() {
  const response = await fetch("/v1/admin/fase2");
  if (!response.ok) throw new Error("Não foi possível carregar a validação.");
  const data = await response.json();
  document.querySelector("#metric-high-renown").textContent = data.alto_renome_vigentes;
  document.querySelector("#metric-pending").textContent = data.afinidades_pendentes;
  document.querySelector("#metric-approved").textContent = data.afinidades_aprovadas;
  document.querySelector("#metric-rejected").textContent = data.afinidades_rejeitadas;
  document.querySelector("#high-renown-date").textContent = `Lista oficial de alto renome: ${dateTimeLabel(data.alto_renome_atualizado_em)}.`;
  list.innerHTML = data.afinidades.map(row).join("");
}

list.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const decision = event.submitter?.value;
  const data = new FormData(form);
  message.textContent = "Salvando revisão...";
  const response = await fetch(`/v1/admin/fase2/afinidades/${form.dataset.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      status_revisao: decision,
      revisor: data.get("revisor"),
      observacoes_revisao: data.get("observacoes"),
    }),
  });
  if (!response.ok) {
    message.textContent = "Não foi possível salvar. Confira os campos.";
    return;
  }
  message.textContent = "Revisão registrada.";
  await load();
});

load().catch((error) => {
  message.textContent = error.message;
});

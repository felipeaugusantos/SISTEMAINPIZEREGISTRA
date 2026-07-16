const filters = document.querySelector("#lead-filters");
const searchInput = document.querySelector("#lead-search");
const statusFilter = document.querySelector("#lead-status-filter");
const leadsList = document.querySelector("#leads-list");
const message = document.querySelector("#admin-message");

const statusLabels = {
  novo: "Novo",
  em_contato: "Em contato",
  convertido: "Convertido",
  descartado: "Descartado",
};

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "America/Sao_Paulo",
  }).format(new Date(value));
}

function leadRow(lead) {
  return `
    <tr data-lead-id="${lead.id}">
      <td><strong>${escapeHtml(lead.nome)}</strong><a href="mailto:${escapeHtml(lead.email)}">${escapeHtml(lead.email)}</a></td>
      <td><a href="tel:${escapeHtml(lead.telefone)}">${escapeHtml(lead.telefone)}</a></td>
      <td><strong>${escapeHtml(lead.marca || "Interesse geral")}</strong><small>${escapeHtml(lead.processo_numero || lead.tipo_interesse || "não informado")}</small></td>
      <td><strong>${lead.origem === "processo" ? "Página do processo" : "Resultados"}</strong><small>${escapeHtml(lead.tipo_interesse || "todos")}</small></td>
      <td><time datetime="${escapeHtml(lead.criado_em)}">${formatDate(lead.criado_em)}</time></td>
      <td>
        <select class="lead-status status-${escapeHtml(lead.status)}" aria-label="Status de ${escapeHtml(lead.nome)}">
          ${Object.entries(statusLabels).map(([value, label]) => `<option value="${value}" ${lead.status === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </td>
      <td><button class="delete-lead" type="button" aria-label="Excluir lead de ${escapeHtml(lead.nome)}">Excluir</button></td>
    </tr>
  `;
}

async function loadLeads() {
  const params = new URLSearchParams();
  if (searchInput.value.trim()) params.set("busca", searchInput.value.trim());
  if (statusFilter.value) params.set("status", statusFilter.value);
  message.className = "status-message";
  message.textContent = "Carregando leads...";
  try {
    const response = await fetch(`/v1/admin/leads?${params}`);
    if (!response.ok) throw new Error("Não foi possível carregar os leads.");
    const data = await response.json();
    document.querySelector("#metric-total").textContent = data.total;
    document.querySelector("#metric-new").textContent = data.por_status.novo || 0;
    document.querySelector("#metric-contact").textContent = data.por_status.em_contato || 0;
    document.querySelector("#metric-converted").textContent = data.por_status.convertido || 0;
    leadsList.innerHTML = data.itens.map(leadRow).join("");
    message.textContent = data.total ? "" : "Nenhum lead encontrado.";
  } catch (error) {
    message.className = "status-message error";
    message.textContent = error.message;
  }
}

filters.addEventListener("submit", (event) => {
  event.preventDefault();
  loadLeads();
});

leadsList.addEventListener("change", async (event) => {
  if (!event.target.matches(".lead-status")) return;
  const row = event.target.closest("tr");
  const response = await fetch(`/v1/admin/leads/${row.dataset.leadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: event.target.value }),
  });
  if (!response.ok) {
    message.textContent = "Não foi possível atualizar o status.";
    return;
  }
  event.target.className = `lead-status status-${event.target.value}`;
  loadLeads();
});

leadsList.addEventListener("click", async (event) => {
  if (!event.target.matches(".delete-lead")) return;
  const row = event.target.closest("tr");
  if (!confirm("Excluir permanentemente este lead?")) return;
  const response = await fetch(`/v1/admin/leads/${row.dataset.leadId}`, { method: "DELETE" });
  if (response.ok) loadLeads();
});

loadLeads();

const productionMessage = document.querySelector("#production-message");
const auditState = { offset: 0, pageSize: 10 };

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

function renderAuditPagination(data) {
  const totalPages = Math.max(1, Math.ceil(data.auditoria_total / auditState.pageSize));
  const currentPage = Math.floor(data.auditoria_deslocamento / auditState.pageSize) + 1;
  document.querySelector("#audit-page-summary").textContent = `Página ${currentPage} de ${totalPages} · ${data.auditoria_total} evento${data.auditoria_total === 1 ? "" : "s"}`;
  document.querySelector("#audit-prev").disabled = data.auditoria_deslocamento === 0;
  document.querySelector("#audit-next").disabled = data.auditoria_deslocamento + data.auditoria.length >= data.auditoria_total;
}

function render(data) {
  document.querySelector("#production-environment").textContent = data.ambiente;
  document.querySelector("#metric-requests").textContent = data.requisicoes_24h;
  document.querySelector("#metric-errors").textContent = percent(data.taxa_erros_24h);
  document.querySelector("#metric-duration").textContent = `${Math.round(data.duracao_media_ms_24h)} ms`;
  document.querySelector("#metric-divergence").textContent = percent(data.taxa_divergencia);
  document.querySelector("#metric-versions").textContent = data.relatorios_versionados;
  document.querySelector("#metric-versioned-reports").textContent = data.pesquisas_com_versao;
  renderAudit(data.auditoria);
  renderAuditPagination(data);
}

async function loadProduction() {
  const params = new URLSearchParams({
    limite_auditoria: auditState.pageSize,
    deslocamento_auditoria: auditState.offset,
  });
  const response = await fetch(`/v1/admin/producao?${params}`);
  if (!response.ok) throw new Error("Não foi possível carregar a governança de produção.");
  render(await response.json());
}

document.querySelector("#audit-prev").addEventListener("click", () => {
  auditState.offset = Math.max(0, auditState.offset - auditState.pageSize);
  loadProduction().catch(showProductionError);
});

document.querySelector("#audit-next").addEventListener("click", () => {
  auditState.offset += auditState.pageSize;
  loadProduction().catch(showProductionError);
});

function showProductionError(error) {
  productionMessage.textContent = error.message;
  productionMessage.classList.add("error");
}

loadProduction().catch(showProductionError);

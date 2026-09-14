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

// Fase 3 -- avisos de versão e confirmação de leitura.
const avisoForm = document.querySelector("#aviso-form");
const avisoFormMessage = document.querySelector("#aviso-form-message");
const avisosList = document.querySelector("#avisos-list");
const avisosResumo = document.querySelector("#avisos-resumo");
const confirmacoesDialog = document.querySelector("#aviso-confirmacoes-dialog");

const severidadeLabels = { info: "Informativo", aviso: "Aviso", critico: "Crítico" };

function renderAvisos(avisos) {
  const pendenciasTotais = avisos.reduce((total, aviso) => total + aviso.pendentes, 0);
  avisosResumo.textContent = avisos.length
    ? `${avisos.length} aviso${avisos.length === 1 ? "" : "s"} ativo${avisos.length === 1 ? "" : "s"} · ${pendenciasTotais} confirmação${pendenciasTotais === 1 ? "" : "ões"} pendente${pendenciasTotais === 1 ? "" : "s"}`
    : "Nenhum aviso ativo";
  avisosList.innerHTML = avisos.length
    ? avisos.map((aviso) => `<tr>
        <td><strong>${escapeHtml(aviso.titulo)}</strong></td>
        <td>${escapeHtml(aviso.versao)}</td>
        <td><span class="aviso-severidade-badge ${aviso.severidade}">${severidadeLabels[aviso.severidade] || aviso.severidade}</span></td>
        <td><time>${escapeHtml(dateTimeLabel(aviso.publicado_em))}</time></td>
        <td>${aviso.total_confirmados}/${aviso.total_usuarios} (${aviso.pendentes} pendente${aviso.pendentes === 1 ? "" : "s"})</td>
        <td><button type="button" class="secondary-button" data-aviso-id="${aviso.id}" data-aviso-titulo="${escapeHtml(aviso.titulo)}">Ver confirmações</button></td>
      </tr>`).join("")
    : `<tr><td colspan="6">Nenhum aviso publicado ainda.</td></tr>`;
}

async function loadAvisos() {
  const response = await fetch("/v1/admin/producao/avisos");
  if (!response.ok) return;
  renderAvisos(await response.json());
}

avisosList?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-aviso-id]");
  if (!button) return;
  const response = await fetch(`/v1/admin/producao/avisos/${button.dataset.avisoId}/confirmacoes`);
  if (!response.ok) return;
  const confirmacoes = await response.json();
  document.querySelector("#aviso-confirmacoes-titulo").textContent = button.dataset.avisoTitulo;
  document.querySelector("#aviso-confirmacoes-list").innerHTML = confirmacoes
    .map((item) => `<li class="${item.confirmado_em ? "" : "pendente"}">
        <span>${escapeHtml(item.nome)} <small>${escapeHtml(item.email)}</small></span>
        <span>${item.confirmado_em ? `<span class="confirmado">Confirmado em ${escapeHtml(dateTimeLabel(item.confirmado_em))}</span>` : "Pendente"}</span>
      </li>`)
    .join("") || "<li>Nenhum usuário encontrado.</li>";
  confirmacoesDialog.showModal();
});

document.querySelector("#aviso-confirmacoes-fechar")?.addEventListener("click", () => confirmacoesDialog.close());

avisoForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  avisoFormMessage.textContent = "";
  avisoFormMessage.className = "status-message";
  try {
    const response = await fetch("/v1/admin/producao/avisos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        versao: document.querySelector("#aviso-versao").value,
        titulo: document.querySelector("#aviso-titulo").value,
        mensagem: document.querySelector("#aviso-mensagem").value,
        severidade: document.querySelector("#aviso-severidade").value,
        critico: document.querySelector("#aviso-critico").checked,
      }),
    });
    if (!response.ok) {
      const erro = await response.json().catch(() => ({}));
      throw new Error(erro.detail || "Não foi possível publicar o aviso.");
    }
    avisoForm.reset();
    avisoFormMessage.textContent = "Aviso publicado.";
    avisoFormMessage.classList.add("success");
    await loadAvisos();
  } catch (error) {
    avisoFormMessage.textContent = error.message;
    avisoFormMessage.classList.add("error");
  }
});

fetch("/v1/auth/me").then(async (response) => {
  if (!response.ok) return;
  const user = await response.json();
  if (user.superadmin) avisoForm.hidden = false;
});

loadAvisos().catch(() => {});

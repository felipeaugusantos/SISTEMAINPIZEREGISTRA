const overviewMessage = document.querySelector("#overview-message");
const rpiActionMessage = document.querySelector("#rpi-action-message");
const syncNowButton = document.querySelector("#rpi-sync-now");

function formatNumber(value) {
  return new Intl.NumberFormat("pt-BR").format(value ?? 0);
}

function formatDate(value, includeTime = true) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    ...(includeTime ? { timeStyle: "short" } : {}),
  }).format(new Date(value));
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return `${minutes}min ${remaining}s`;
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function statusLabel(status) {
  return {
    solicitada: "Na fila",
    verificando: "Verificando",
    importando: "Importando",
    concluida: "Concluída",
    sem_atualizacoes: "Sem novidades",
    falhou: "Falhou",
  }[status] || status;
}

async function loadOverview() {
  const response = await fetch("/v1/admin/resumo");
  if (!response.ok) throw new Error("Não foi possível atualizar os indicadores.");
  const data = await response.json();

  document.querySelector("#overview-searches").textContent = formatNumber(data.pesquisas_total);
  document.querySelector("#overview-leads").textContent = `${formatNumber(data.leads_total)} leads cadastrados`;
  document.querySelector("#overview-risks").textContent = formatNumber(data.riscos_calculados);
  document.querySelector("#overview-high-risks").textContent = `${formatNumber(data.riscos_elevados)} casos elevados`;
  document.querySelector("#overview-rpi").textContent = data.ultima_rpi ? `RPI ${data.ultima_rpi}` : "Sem importação";
  document.querySelector("#overview-high-renown").textContent = formatNumber(data.alto_renome_vigentes);
  document.querySelector("#priority-leads").textContent = formatNumber(data.leads_novos);
  document.querySelector("#priority-affinities").textContent = formatNumber(data.afinidades_pendentes);
  document.querySelector("#priority-risks").textContent = formatNumber(data.riscos_elevados);
  document.querySelector("#priority-reviews").textContent = formatNumber(data.riscos_pendentes_revisao);
  document.querySelector("#overview-model").textContent = "Motor determinístico e aprendizado supervisionado ativos";
  overviewMessage.textContent = "";
  overviewMessage.classList.remove("loading");
}

function renderHealth(id, healthy) {
  const element = document.querySelector(id);
  element.classList.toggle("healthy", healthy);
  element.classList.toggle("unhealthy", !healthy);
}

function renderCurrent(item) {
  const panel = document.querySelector("#rpi-current");
  if (!item) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const interval = item.rpi_inicio
    ? item.rpi_inicio === item.rpi_fim ? `RPI ${item.rpi_inicio}` : `RPIs ${item.rpi_inicio}–${item.rpi_fim}`
    : "Consultando portal oficial";
  document.querySelector("#rpi-current-title").textContent = item.rpi_atual
    ? `Importando RPI ${item.rpi_atual} · ${interval}`
    : interval;
  document.querySelector("#rpi-current-progress").textContent = `${item.progresso_percentual}%`;
  document.querySelector("#rpi-progress-bar").style.width = `${item.progresso_percentual}%`;
  document.querySelector("#rpi-current-message").textContent = item.mensagem || statusLabel(item.status);
  document.querySelector("#rpi-count-editions").textContent = `${item.edicoes_processadas}/${item.edicoes_total}`;
  document.querySelector("#rpi-count-records").textContent = formatNumber(item.registros_processados);
  document.querySelector("#rpi-count-holders").textContent = formatNumber(item.titulares_processados);
  document.querySelector("#rpi-count-classes").textContent = formatNumber(item.classes_processadas);
  document.querySelector("#rpi-count-movements").textContent = formatNumber(item.movimentacoes_processadas);
}

function renderHistory(items) {
  const body = document.querySelector("#rpi-history");
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="7">Nenhuma execução registrada.</td></tr>';
    return;
  }
  body.innerHTML = items.map((item) => {
    const interval = item.rpi_inicio
      ? item.rpi_inicio === item.rpi_fim ? `RPI ${item.rpi_inicio}` : `${item.rpi_inicio}–${item.rpi_fim}`
      : "—";
    const result = item.erro || item.mensagem || statusLabel(item.status);
    const retry = item.status === "falhou"
      ? `<button type="button" class="rpi-retry" data-execution-id="${item.id}">Tentar novamente</button>`
      : "";
    return `<tr>
      <td><strong>#${item.id}</strong><small>${formatDate(item.solicitado_em)}</small></td>
      <td>${escapeHtml(item.origem)}<small>${escapeHtml(item.solicitado_por || "sistema")}</small></td>
      <td>${interval}<small>${item.rpi_atual ? `Atual: ${item.rpi_atual}` : ""}</small></td>
      <td><span class="rpi-run-status ${item.status}">${escapeHtml(statusLabel(item.status))}</span><small title="${escapeHtml(result)}">${escapeHtml(result)}</small></td>
      <td>${formatNumber(item.registros_processados)}<small>${item.edicoes_processadas}/${item.edicoes_total} edições</small></td>
      <td>${formatDuration(item.duracao_segundos)}<small>${formatDate(item.finalizado_em)}</small></td>
      <td>${retry}</td>
    </tr>`;
  }).join("");
}

async function loadRpiMonitor() {
  const response = await fetch("/v1/admin/rpi");
  if (!response.ok) throw new Error("Não foi possível carregar o monitoramento da RPI.");
  const data = await response.json();
  const badge = document.querySelector("#rpi-status-badge");
  badge.textContent = data.status_rotulo;
  badge.className = `rpi-status-badge ${data.status_cor}`;
  document.querySelector("#rpi-official").textContent = data.ultima_rpi_oficial ? `RPI ${data.ultima_rpi_oficial}` : "—";
  document.querySelector("#rpi-local").textContent = data.ultima_rpi_local ? `RPI ${data.ultima_rpi_local}` : "—";
  document.querySelector("#rpi-delay").textContent = data.edicoes_atraso
    ? `${data.edicoes_atraso} edição(ões) de atraso`
    : "Sem atraso";
  document.querySelector("#rpi-next").textContent = formatDate(data.proxima_verificacao_em);
  document.querySelector("#rpi-last-check").textContent = `Última: ${formatDate(data.ultima_verificacao_em)}`;
  document.querySelector("#rpi-failures").textContent = formatNumber(data.falhas_consecutivas);
  document.querySelector("#rpi-heartbeat").textContent = `Heartbeat: ${formatDate(data.heartbeat_em)}`;
  renderHealth("#rpi-health-api", data.saude.api);
  renderHealth("#rpi-health-db", data.saude.banco);
  renderHealth("#rpi-health-sync", data.saude.sincronizador);
  renderCurrent(data.execucao_atual);
  renderHistory(data.historico);
  const errorBox = document.querySelector("#rpi-error");
  errorBox.hidden = !data.ultimo_erro;
  errorBox.textContent = data.ultimo_erro ? `Último erro: ${data.ultimo_erro}` : "";
  syncNowButton.disabled = Boolean(data.execucao_atual);
}

async function requestAction(url, successMessage) {
  rpiActionMessage.textContent = "Enviando solicitação…";
  rpiActionMessage.className = "status-message loading";
  const response = await fetch(url, { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "A operação não pôde ser executada.");
  rpiActionMessage.textContent = successMessage;
  rpiActionMessage.className = "status-message success";
  await loadRpiMonitor();
}

syncNowButton.addEventListener("click", () => {
  requestAction("/v1/admin/rpi/sincronizar", "Verificação adicionada à fila.").catch((error) => {
    rpiActionMessage.textContent = error.message;
    rpiActionMessage.className = "status-message error";
  });
});

document.querySelector("#rpi-history").addEventListener("click", (event) => {
  const button = event.target.closest(".rpi-retry");
  if (!button) return;
  button.disabled = true;
  requestAction(
    `/v1/admin/rpi/sincronizacoes/${button.dataset.executionId}/tentar-novamente`,
    "Nova tentativa adicionada à fila.",
  ).catch((error) => {
    rpiActionMessage.textContent = error.message;
    rpiActionMessage.className = "status-message error";
    button.disabled = false;
  });
});

loadOverview().catch((error) => {
  overviewMessage.textContent = error.message;
  overviewMessage.classList.remove("loading");
  overviewMessage.classList.add("error");
});
loadRpiMonitor().catch((error) => {
  rpiActionMessage.textContent = error.message;
  rpiActionMessage.className = "status-message error";
});
setInterval(() => loadRpiMonitor().catch(() => {}), 10_000);

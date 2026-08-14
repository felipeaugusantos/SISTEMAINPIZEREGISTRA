const overviewMessage = document.querySelector("#overview-message");
const rpiActionMessage = document.querySelector("#rpi-action-message");
const syncNowButton = document.querySelector("#rpi-sync-now");

async function configureRecentExecutions() {
  const response = await fetch("/v1/auth/me");
  if (!response.ok) return;
  const user = await response.json();
  document.querySelector("#rpi-recent-executions").hidden = !["tech", "administrador"].includes(user.perfil);
  if (user.perfil !== "administrador") {
    document.querySelector(".overview-primary-metrics").before(document.querySelector("#overview-priorities"));
  }
}

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

function formatCurrency(value) {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value ?? 0);
}

function execCard({ url, icon, title, metrics }) {
  const cells = metrics
    .map(
      (m) =>
        `<div class="${m.tone ? `tone-${m.tone}` : ""}"><strong>${escapeHtml(m.value)}</strong><span>${escapeHtml(m.label)}</span></div>`,
    )
    .join("");
  return `<a class="exec-card" href="${url}"><div class="exec-card-head"><span aria-hidden="true">${icon}</span><h3>${escapeHtml(title)}</h3></div><div class="exec-card-metrics">${cells}</div></a>`;
}

function buildExecCards(data) {
  const cards = [];
  if (data.comercial) {
    const c = data.comercial;
    cards.push(execCard({
      url: c.url, icon: "AN", title: "Comercial",
      metrics: [
        { value: formatNumber(c.pesquisas_total), label: "Pesquisas" },
        { value: formatNumber(c.leads_novos), label: "Leads novos", tone: c.leads_novos ? "warn" : "" },
        { value: formatNumber(c.leads_total), label: "Leads totais" },
      ],
    }));
  }
  if (data.financeiro) {
    const f = data.financeiro;
    cards.push(execCard({
      url: f.url, icon: "FI", title: "Financeiro",
      metrics: [
        { value: formatCurrency(f.receber_aberto), label: "A receber" },
        { value: formatCurrency(f.pagar_aberto), label: "A pagar" },
        { value: formatCurrency(f.vencido), label: `Vencido (${formatNumber(f.parcelas_vencidas)})`, tone: f.vencido ? "danger" : "" },
      ],
    }));
  }
  if (data.juridico) {
    const j = data.juridico;
    cards.push(execCard({
      url: j.url, icon: "OJ", title: "Jurídico",
      metrics: [
        { value: formatNumber(j.vencidos), label: "Vencidos", tone: j.vencidos ? "danger" : "" },
        { value: formatNumber(j.proximos_7_dias), label: "Próx. 7 dias", tone: j.proximos_7_dias ? "warn" : "" },
        { value: formatNumber(j.aguardando_confirmacao), label: "A confirmar", tone: j.aguardando_confirmacao ? "warn" : "" },
      ],
    }));
  }
  if (data.risco) {
    const r = data.risco;
    cards.push(execCard({
      url: r.url, icon: "MR", title: "Risco",
      metrics: [
        { value: formatNumber(r.elevados), label: "Elevados", tone: r.elevados ? "danger" : "" },
        { value: formatNumber(r.pendentes_revisao), label: "Sem parecer", tone: r.pendentes_revisao ? "warn" : "" },
      ],
    }));
  }
  if (data.aprendizado) {
    const a = data.aprendizado;
    cards.push(execCard({
      url: a.url, icon: "AP", title: "Aprendizado",
      metrics: [
        { value: a.modelo_ativo ? "Ativo" : "Em sombra", label: "Modelo", tone: a.modelo_ativo ? "" : "warn" },
        { value: a.versao || "—", label: "Versão" },
      ],
    }));
  }
  return cards;
}

async function loadExecPanel() {
  const response = await fetch("/v1/admin/painel-executivo");
  if (!response.ok) return;
  const data = await response.json();
  const cards = buildExecCards(data);
  if (!cards.length) return;
  // Conteúdo montado apenas com literais e valores passados por escapeHtml/formatadores.
  document.querySelector("#exec-grid").innerHTML = cards.join("");
  document.querySelector("#exec-panel").hidden = false;
}

function notifItem(item) {
  const sev = { info: "info", aviso: "warn", critico: "danger", critica: "danger" }[item.severidade] || "info";
  const fonte = item.fonte === "juridico" ? "Jurídico" : "Sistema";
  return `<li><a href="${item.url || "#"}" data-fonte="${item.fonte}" data-id="${item.id}" data-url="${item.url || ""}"><span class="notif-dot sev-${sev}" aria-hidden="true"></span><div><strong>${escapeHtml(item.titulo)}</strong><p>${escapeHtml(item.mensagem)}</p><small>${fonte} · ${formatDate(item.criado_em)}</small></div></a></li>`;
}

async function loadNotifications() {
  const response = await fetch("/v1/admin/notificacoes");
  if (!response.ok) return;
  const data = await response.json();
  const badge = document.querySelector("#notif-badge");
  const count = document.querySelector("#notif-count");
  const list = document.querySelector("#notif-list");
  badge.hidden = data.total === 0;
  badge.textContent = data.total > 99 ? "99+" : String(data.total);
  count.textContent = data.total === 0 ? "Tudo em dia" : `${data.total} pendente(s)`;
  // Itens montados com literais + escapeHtml; URLs vêm de constantes do backend.
  list.innerHTML = data.total
    ? data.itens.map(notifItem).join("")
    : '<li class="notif-empty">Nenhuma notificação pendente. 🎉</li>';
}

const notifList = document.querySelector("#notif-list");
notifList.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-id]");
  if (!link) return;
  event.preventDefault();
  const { fonte, id, url } = link.dataset;
  try {
    await fetch(`/v1/admin/notificacoes/${encodeURIComponent(fonte)}/${encodeURIComponent(id)}/lida`, { method: "POST" });
  } catch (_) { /* segue para a tela mesmo se a marcação falhar */ }
  if (url) {
    window.location.href = url;
    return;
  }
  await loadNotifications().catch(() => {});
});

const notifToggle = document.querySelector("#notif-toggle");
const notifPanel = document.querySelector("#notif-panel");
notifToggle.addEventListener("click", (event) => {
  event.stopPropagation();
  const open = notifPanel.hidden;
  notifPanel.hidden = !open;
  notifToggle.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (event) => {
  if (!notifPanel.hidden && !event.target.closest(".notif-wrap")) {
    notifPanel.hidden = true;
    notifToggle.setAttribute("aria-expanded", "false");
  }
});

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

let historyItems = [];
let historyPage = 0;
const HISTORY_PAGE_SIZE = 5;

function renderHistory(items) {
  historyItems = items || [];
  const body = document.querySelector("#rpi-history");
  const pager = document.querySelector("#rpi-history-pager");
  if (!historyItems.length) {
    body.innerHTML = '<tr><td colspan="7">Nenhuma execução registrada.</td></tr>';
    if (pager) pager.hidden = true;
    return;
  }
  const totalPaginas = Math.max(1, Math.ceil(historyItems.length / HISTORY_PAGE_SIZE));
  if (historyPage >= totalPaginas) historyPage = totalPaginas - 1;
  if (historyPage < 0) historyPage = 0;
  const inicio = historyPage * HISTORY_PAGE_SIZE;
  const pagina = historyItems.slice(inicio, inicio + HISTORY_PAGE_SIZE);
  if (pager) {
    pager.hidden = historyItems.length <= HISTORY_PAGE_SIZE;
    const info = pager.querySelector("#rpi-history-info");
    if (info) info.textContent = `Página ${historyPage + 1} de ${totalPaginas}`;
    const prev = pager.querySelector("#rpi-history-prev");
    const next = pager.querySelector("#rpi-history-next");
    if (prev) prev.disabled = historyPage === 0;
    if (next) next.disabled = historyPage >= totalPaginas - 1;
  }
  body.innerHTML = pagina.map((item) => {
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

document.querySelector("#rpi-history-prev")?.addEventListener("click", () => {
  if (historyPage > 0) { historyPage -= 1; renderHistory(historyItems); }
});
document.querySelector("#rpi-history-next")?.addEventListener("click", () => {
  if ((historyPage + 1) * HISTORY_PAGE_SIZE < historyItems.length) { historyPage += 1; renderHistory(historyItems); }
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
loadExecPanel().catch(() => {});
loadNotifications().catch(() => {});
setInterval(() => loadNotifications().catch(() => {}), 60_000);
configureRecentExecutions().catch(() => {});
loadRpiMonitor().catch((error) => {
  rpiActionMessage.textContent = error.message;
  rpiActionMessage.className = "status-message error";
});
setInterval(() => loadRpiMonitor().catch(() => {}), 10_000);

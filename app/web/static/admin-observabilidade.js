const observabilityMessage = document.querySelector("#observability-message");
const serviceTarget = document.querySelector("#observability-services");
const metricsTarget = document.querySelector("#observability-metrics");
const rpiTarget = document.querySelector("#observability-rpi");
const updatedTarget = document.querySelector("#observability-updated");

function escapeText(value) { return String(value ?? "—"); }
function statusClass(status) { return ["ok", "saudavel", "healthy"].includes(String(status).toLowerCase()) ? "positive" : "negative"; }
function metric(value, label, detail = "") { return `<article><strong>${escapeText(value)}</strong><span>${escapeText(label)}</span>${detail ? `<small>${escapeText(detail)}</small>` : ""}</article>`; }
function bytes(value) { const n = Number(value || 0); if (!n) return "—"; const units = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = n; while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; } return `${v.toFixed(i ? 1 : 0)} ${units[i]}`; }

async function carregarObservabilidade() {
  observabilityMessage.hidden = false; observabilityMessage.className = "status-message loading"; observabilityMessage.textContent = "Atualizando indicadores…";
  try {
    const response = await fetch("/v1/admin/observabilidade");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Falha ao carregar (${response.status})`);
    serviceTarget.innerHTML = Object.entries(data.servicos).map(([key, value]) => `<article class="observability-service"><span class="observability-dot ${statusClass(value.status)}"></span><strong>${escapeText(key.replace("rpi_sync", "RPI Sync"))}</strong><small>${escapeText(value.status)}</small></article>`).join("");
    metricsTarget.innerHTML = [metric(data.api_24h.requisicoes, "Requisições (24h)"), metric(data.api_24h.erros, "Erros (24h)", `${(Number(data.api_24h.taxa_erros) * 100).toFixed(2)}% de erro`), metric(`${Number(data.api_24h.duracao_media_ms).toFixed(0)} ms`, "Latência média"), metric(data.servicos.banco.conexoes, "Conexões do banco", `${bytes(data.servicos.banco.tamanho_bytes)} usados`), metric(data.pool.em_uso, "Pool em uso", `${data.pool.tamanho ?? "—"} conexões`), metric(data.servicos.redis.pendentes, "Jobs pendentes", `${data.servicos.redis.falhas ?? "—"} falhas`)].join("");
    const rpi = data.rpi;
    rpiTarget.innerHTML = [metric(rpi.status, "Status"), metric(rpi.ultima_rpi, "Última RPI"), metric(rpi.status_integridade, "Integridade"), metric(rpi.idade_horas == null ? "—" : `${Number(rpi.idade_horas).toFixed(1)} h`, "Idade dos dados"), metric(rpi.erros_acumulados, "Falhas acumuladas"), metric((rpi.anomalias || []).length, "Anomalias")].join("");
    updatedTarget.textContent = `Atualizado ${new Date(data.gerado_em).toLocaleTimeString("pt-BR")}`;
    observabilityMessage.hidden = true;
  } catch (error) { observabilityMessage.className = "status-message error"; observabilityMessage.textContent = error.message; }
}
document.querySelector("#observability-refresh").addEventListener("click", carregarObservabilidade);
carregarObservabilidade();

// Fase 7: painel técnico de observabilidade e rollback. Critério de
// aceite: a equipe identifica rapidamente uma regressão e consegue
// limitar seu impacto.
function escapeHtmlSeguro(value) { const el = document.createElement("span"); el.textContent = value ?? ""; return el.innerHTML; }
function dataHoraCurta(value) { return value ? new Date(value).toLocaleString("pt-BR") : "—"; }
async function apiPainel(url, options = {}) {
  const resposta = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const corpo = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(corpo.detail || `Falha na operação (${resposta.status})`);
  return corpo;
}

async function carregarPainelTecnico() {
  const message = document.querySelector("#painel-tecnico-message");
  try {
    const dados = await apiPainel("/v1/admin/observabilidade/painel-tecnico");
    message.hidden = true;
    document.querySelector("#painel-tecnico-atualizado").textContent = `Atualizado ${new Date(dados.gerado_em).toLocaleTimeString("pt-BR")}`;

    const processos = dados.processos;
    document.querySelector("#painel-tecnico-processos").innerHTML = Object.entries(processos).map(([nome, info]) => metric(
      info.status === "ok" ? "Saudável" : "Indisponível",
      nome === "rpi_sync" ? "RPI Sync" : nome === "api" ? "API" : "Worker",
      `${info.versao} · commit ${(info.commit || "").slice(0, 10)}${info.heartbeat_em ? ` · heartbeat ${dataHoraCurta(info.heartbeat_em)}` : ""}`,
    )).join("");
    document.querySelector("#painel-tecnico-migration").textContent = dados.migration_atual || "—";

    const flagsRows = document.querySelector("#painel-tecnico-flags-rows");
    flagsRows.innerHTML = dados.feature_flags_ativas.length
      ? dados.feature_flags_ativas.map((flag) => `<tr class="${flag.pausado_em ? "is-erro" : ""}">
          <td>${escapeHtmlSeguro(flag.codigo)}</td>
          <td>${escapeHtmlSeguro(flag.nome)}</td>
          <td>${escapeHtmlSeguro(flag.estagio_rollout || flag.estado_padrao)}</td>
          <td>${flag.pausado_em ? `Sim — ${escapeHtmlSeguro(flag.pausado_motivo || "")}` : "Não"}</td>
          <td><button type="button" class="secondary-button" data-desligar-flag="${flag.codigo}">Desligar agora</button></td>
        </tr>`).join("")
      : `<tr><td colspan="5">Nenhuma feature flag ativa no momento.</td></tr>`;
    flagsRows.querySelectorAll("[data-desligar-flag]").forEach((botao) => {
      botao.addEventListener("click", async () => {
        const codigo = botao.dataset.desligarFlag;
        if (!confirm(`Desligar a flag "${codigo}" para TODAS as organizações imediatamente?`)) return;
        botao.disabled = true;
        try {
          await apiPainel(`/v1/admin/feature-flags/${codigo}/desligar`, { method: "POST" });
          await carregarPainelTecnico();
        } catch (error) {
          alert(error.message);
          botao.disabled = false;
        }
      });
    });

    const versoesRows = document.querySelector("#painel-tecnico-versoes-rows");
    versoesRows.innerHTML = dados.erros_por_versao.length
      ? dados.erros_por_versao.map((item) => `<tr class="${item.taxa_erro && item.taxa_erro > 0.05 ? "is-erro" : ""}">
          <td>${escapeHtmlSeguro(item.versao)}</td>
          <td>${escapeHtmlSeguro(item.titulo)}</td>
          <td>${dataHoraCurta(item.implantada_em)}</td>
          <td>${item.requisicoes}</td>
          <td>${item.erros}</td>
          <td>${item.taxa_erro != null ? `${(item.taxa_erro * 100).toFixed(2)}%` : "—"}</td>
        </tr>`).join("")
      : `<tr><td colspan="6">Nenhuma versão publicada ainda.</td></tr>`;

    const latenciaRows = document.querySelector("#painel-tecnico-latencia-rows");
    latenciaRows.innerHTML = dados.latencia_por_endpoint.length
      ? dados.latencia_por_endpoint.map((item) => `<tr class="${item.duracao_p95_ms > 2000 ? "is-erro" : ""}">
          <td>${escapeHtmlSeguro(item.componente)}</td>
          <td>${escapeHtmlSeguro(item.operacao)}</td>
          <td>${item.requisicoes}</td>
          <td>${item.duracao_media_ms}</td>
          <td>${item.duracao_p95_ms}</td>
          <td>${item.duracao_max_ms}</td>
          <td>${item.erros}</td>
        </tr>`).join("")
      : `<tr><td colspan="7">Sem endpoints com amostras suficientes nas últimas 24 horas.</td></tr>`;

    const emailsRows = document.querySelector("#painel-tecnico-emails-rejeitados-rows");
    emailsRows.innerHTML = dados.emails_rejeitados.length
      ? dados.emails_rejeitados.map((item) => `<tr class="is-erro">
          <td>${escapeHtmlSeguro(item.operacao)}</td>
          <td>${item.quantidade}</td>
          <td>${dataHoraCurta(item.ultima_em)}</td>
        </tr>`).join("")
      : `<tr><td colspan="3">Nenhum e-mail rejeitado nas últimas 24 horas.</td></tr>`;

    const recursos = dados.recursos_host;
    document.querySelector("#painel-tecnico-recursos").innerHTML = [
      metric(recursos.cpu.carga_1min ?? "—", "Carga média (1 min)", `${recursos.cpu.nucleos ?? "—"} núcleos · 5 min: ${recursos.cpu.carga_5min ?? "—"} · 15 min: ${recursos.cpu.carga_15min ?? "—"}`),
      metric(recursos.memoria.percentual_uso != null ? `${recursos.memoria.percentual_uso}%` : "—", "Memória em uso", `${bytes(recursos.memoria.disponivel_bytes)} disponíveis de ${bytes(recursos.memoria.total_bytes)}`),
      metric(recursos.disco.percentual_uso != null ? `${recursos.disco.percentual_uso}%` : "—", "Disco em uso", `${bytes(recursos.disco.usado_bytes)} usados de ${bytes(recursos.disco.total_bytes)}`),
    ].join("");

    const orgRows = document.querySelector("#painel-tecnico-organizacoes-rows");
    const orgSecao = orgRows.closest("section");
    // A API só envia esta lista ao superadmin (dado cross-tenant); para os demais vem null.
    if (dados.organizacoes_afetadas === null) {
      if (orgSecao) orgSecao.hidden = true;
    } else {
      if (orgSecao) orgSecao.hidden = false;
      orgRows.innerHTML = dados.organizacoes_afetadas.length
        ? dados.organizacoes_afetadas.map((item) => `<tr>
          <td>${escapeHtmlSeguro(item.organizacao_nome)}</td>
          <td>${item.eventos}</td>
          <td>${item.flags.map((f) => `${escapeHtmlSeguro(f.codigo)} (${f.eventos})`).join(", ")}</td>
        </tr>`).join("")
        : `<tr><td colspan="3">Nenhuma organização afetada nas últimas 24 horas.</td></tr>`;
    }

    const deployTarget = document.querySelector("#painel-tecnico-deploy");
    const deploy = dados.ultimo_deploy;
    deployTarget.innerHTML = deploy
      ? `<p><strong>${escapeHtmlSeguro(deploy.versao)}</strong> — ${escapeHtmlSeguro(deploy.titulo)} (${escapeHtmlSeguro(deploy.tipo_atualizacao)})</p>
         <p><small>Implantada em ${dataHoraCurta(deploy.implantada_em)} por ${escapeHtmlSeguro(deploy.publicado_por || "—")} · commit ${escapeHtmlSeguro((deploy.commit_sha || "").slice(0, 10))} · migration ${escapeHtmlSeguro(deploy.migration_revision || "—")} · evidências ${deploy.evidencias_aprovadas}/${deploy.evidencias_total} aprovadas</small></p>
         <p><a class="secondary-button" href="/admin/atualizacoes">Ver na central de atualizações</a> — para arquivar/reverter esta versão, use a ação de arquivamento na Central de Atualizações (gera auditoria automaticamente).</p>`
      : `<p>Nenhuma versão publicada ainda.</p>`;
  } catch (error) {
    message.hidden = false;
    message.className = "status-message error";
    message.textContent = error.message;
  }
}
carregarPainelTecnico();

// Painel de saúde dos backups -- exclusivo do superadmin. Para os demais
// usuários a API responde 403 e a seção continua oculta.
const ROTULO_SAUDE = { ok: "OK", atencao: "Atenção", critico: "Crítico", desconhecido: "Sem dados" };
function tamanhoLegivel(valor) { return bytes(valor); }
function marcaDia(valor) { return valor === null || valor === undefined ? "—" : valor ? "OK" : "Falhou"; }
async function carregarSaudeBackups() {
  const secao = document.querySelector("#saude-backups");
  const message = document.querySelector("#saude-backups-message");
  if (!secao) return;
  try {
    const dados = await apiPainel("/v1/admin/observabilidade/saude-backups");
    secao.hidden = false;
    if (!dados.disponivel) {
      message.className = "status-message";
      message.textContent = dados.motivo || "Sem leitura disponível.";
      return;
    }
    message.hidden = true;
    document.querySelector("#saude-backups-atualizado").textContent = `Lido em ${dataHoraCurta(dados.coletado_em)} · situação geral: ${ROTULO_SAUDE[dados.geral] || dados.geral}`;
    document.querySelector("#saude-backups-rows").innerHTML = dados.etapas.map(etapa => `<tr>
      <td>${escapeHtmlSeguro(etapa.nome)}</td>
      <td><span class="${etapa.status === "ok" ? "positive" : etapa.status === "critico" ? "negative" : ""}">${escapeHtmlSeguro(ROTULO_SAUDE[etapa.status] || etapa.status)}</span></td>
      <td>${escapeHtmlSeguro(dataHoraCurta(etapa.ultima_em))}</td>
      <td>${escapeHtmlSeguro(etapa.tamanho_bytes ? tamanhoLegivel(etapa.tamanho_bytes) : "—")}</td>
      <td>${escapeHtmlSeguro(etapa.detalhe)}</td>
    </tr>`).join("");
    document.querySelector("#saude-backups-historico-rows").innerHTML = dados.historico.length
      ? dados.historico.map(dia => `<tr><td>${escapeHtmlSeguro(dia.data)}</td><td>${marcaDia(dia.dump)}</td><td>${marcaDia(dia.offsite)}</td><td>${marcaDia(dia.simulado)}</td></tr>`).join("")
      : `<tr><td colspan="4">Nenhuma execução registrada ainda.</td></tr>`;
  } catch (error) {
    // 403 para quem não é superadmin: a seção segue oculta, sem poluir a tela.
    secao.hidden = true;
  }
}
carregarSaudeBackups();

// Achado de uma auditoria sistemática (08/09/2026, mesmo padrão do achado
// de EnvioCadenciaEmail): EventoOperacional era gravado a cada requisição
// mas só ficava visível de forma agregada (contagens/médias) -- ninguém
// conseguia investigar QUAL erro aconteceu, em qual componente, com qual
// payload. Esta seção lista os eventos individuais.
const eventsState = { offset: 0, pageSize: 50 };
function escapeHtml(value) {
  const el = document.createElement("span"); el.textContent = value ?? ""; return el.innerHTML;
}
function formatDateTime(value) { return value ? new Date(value).toLocaleString("pt-BR") : "—"; }

async function carregarEventos() {
  const rows = document.querySelector("#events-rows");
  const form = document.querySelector("#events-filter");
  const params = new URLSearchParams();
  const componente = form.elements.componente.value.trim();
  const codigoErro = form.elements.codigo_erro.value.trim();
  if (componente) params.set("componente", componente);
  if (codigoErro) params.set("codigo_erro", codigoErro);
  params.set("horas", form.elements.horas.value);
  params.set("apenas_erros", form.elements.apenas_erros.checked);
  params.set("limite", eventsState.pageSize);
  params.set("deslocamento", eventsState.offset);
  try {
    const response = await fetch(`/v1/admin/observabilidade/eventos?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Falha ao carregar (${response.status})`);
    rows.innerHTML = data.itens.length
      ? data.itens.map(item => `<tr class="${item.sucesso ? "" : "is-erro"}">
          <td>${formatDateTime(item.criado_em)}</td>
          <td>${escapeHtml(item.componente)}</td>
          <td>${escapeHtml(item.operacao)}</td>
          <td>${escapeHtml(item.status_http)}${item.sucesso ? "" : " · falhou"}</td>
          <td>${escapeHtml(item.codigo_erro || "—")}</td>
          <td class="events-detalhes-cell" title="${escapeHtml(JSON.stringify(item.detalhes || {}))}">${escapeHtml(JSON.stringify(item.detalhes || {}))}</td>
          <td>${item.duracao_ms} ms</td>
          <td>${escapeHtml(item.request_id || "—")}</td>
        </tr>`).join("")
      : `<tr><td colspan="8">Nenhum evento encontrado com esses filtros.</td></tr>`;
    const nav = document.querySelector("#events-pagination");
    nav.hidden = false;
    const first = data.total ? data.deslocamento + 1 : 0;
    const last = Math.min(data.deslocamento + data.itens.length, data.total);
    document.querySelector("#events-summary").textContent = `${first}–${last} de ${data.total}`;
    document.querySelector("#events-prev").disabled = data.deslocamento === 0;
    document.querySelector("#events-next").disabled = data.deslocamento + data.itens.length >= data.total;
  } catch (error) {
    rows.innerHTML = `<tr><td colspan="8">${escapeHtml(error.message)}</td></tr>`;
  }
}
document.querySelector("#events-filter").addEventListener("submit", event => {
  event.preventDefault(); eventsState.offset = 0; carregarEventos();
});
document.querySelector("#events-prev").addEventListener("click", () => {
  eventsState.offset = Math.max(0, eventsState.offset - eventsState.pageSize); carregarEventos();
});
document.querySelector("#events-next").addEventListener("click", () => {
  eventsState.offset += eventsState.pageSize; carregarEventos();
});
carregarEventos();

// Limpeza de disco -- exclusivo do superadmin. A API só enfileira o pedido; a VPS
// executa uma lista fixa de limpezas e publica o resultado (app/manutencao_limpeza.py).
let limpezaTemporizador = null;
async function carregarLimpezaDisco() {
  const secao = document.querySelector("#limpeza-disco");
  const message = document.querySelector("#limpeza-disco-message");
  const botao = document.querySelector("#limpeza-disco-botao");
  if (!secao) return;
  try {
    const dados = await apiPainel("/v1/admin/observabilidade/limpeza");
    secao.hidden = false;
    if (!dados.disponivel) {
      message.className = "status-message error";
      message.textContent = dados.motivo || "Indisponível.";
      botao.disabled = true;
      return;
    }
    const ocupado = dados.pendente || dados.em_andamento;
    botao.disabled = ocupado;
    document.querySelector("#limpeza-disco-situacao").textContent = dados.em_andamento ? "Limpeza em andamento…" : dados.pendente ? "Pedido na fila; a VPS atende em até 5 minutos." : "";
    const relatorio = dados.relatorio;
    if (!relatorio) {
      message.className = "status-message";
      message.textContent = "A VPS ainda não publicou o relatório (o script roda a cada 5 minutos).";
      document.querySelector("#limpeza-disco-rows").innerHTML = "";
    } else {
      message.hidden = true;
      document.querySelector("#limpeza-disco-atualizado").textContent = `Lido em ${dataHoraCurta(relatorio.gerado_em)}`;
      const disco = relatorio.disco || {};
      const pct = disco.total_bytes ? Math.round((disco.usado_bytes / disco.total_bytes) * 100) : null;
      document.querySelector("#limpeza-disco-resumo").innerHTML = metric(pct != null ? `${pct}%` : "—", "Disco em uso", `${bytes(disco.usado_bytes)} de ${bytes(disco.total_bytes)} · ${bytes(disco.livre_bytes)} livres`);
      document.querySelector("#limpeza-disco-rows").innerHTML = relatorio.itens.map((item) => `<tr>
        <td>${escapeHtmlSeguro(item.nome)}</td>
        <td>${item.bytes ? escapeHtmlSeguro(bytes(item.bytes)) : "—"}</td>
        <td>${escapeHtmlSeguro(item.quantidade)}</td>
        <td>${escapeHtmlSeguro(item.detalhe)}</td>
      </tr>`).join("");
    }
    const resultado = dados.resultado;
    document.querySelector("#limpeza-disco-resultado").innerHTML = resultado
      ? `<h3>Última limpeza</h3><p>${escapeHtmlSeguro(dataHoraCurta(resultado.concluido_em))} · liberou ${escapeHtmlSeguro(bytes(resultado.liberado_bytes) === "—" ? "0 B" : bytes(resultado.liberado_bytes))}</p><ul>${resultado.itens.map((item) => `<li>${item.ok ? "OK" : "Falhou"} — ${escapeHtmlSeguro(item.chave)}: ${escapeHtmlSeguro(item.detalhe)}</li>`).join("")}</ul>`
      : "";
    if (ocupado && !limpezaTemporizador) limpezaTemporizador = setInterval(carregarLimpezaDisco, 10000);
    if (!ocupado && limpezaTemporizador) { clearInterval(limpezaTemporizador); limpezaTemporizador = null; }
  } catch (error) {
    // 403 para quem não é superadmin: a seção segue oculta.
    secao.hidden = true;
  }
}
document.querySelector("#limpeza-disco-botao")?.addEventListener("click", async (evento) => {
  const botao = evento.currentTarget;
  if (!window.confirm("Solicitar a limpeza de disco? Serão removidos apenas imagens Docker órfãs, cache de build antigo, containers parados fora do projeto e arquivos temporários. Volumes, backups e uploads não são tocados.")) return;
  botao.disabled = true;
  try {
    await apiPainel("/v1/admin/observabilidade/limpeza/solicitar", { method: "POST" });
  } catch (error) {
    document.querySelector("#limpeza-disco-situacao").textContent = error.message;
  }
  carregarLimpezaDisco();
});
carregarLimpezaDisco();

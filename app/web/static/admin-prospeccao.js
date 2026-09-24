const state = { offset: 0, pageSize: 20, statusLabels: {
  novo: "Novo", aprovado: "Aprovado", rejeitado: "Rejeitado", duplicado: "Duplicado", convertido_lead: "Convertido em lead",
}, pollingCampanhas: false, canManage: false, supressoesOffset: 0, supressoesPageSize: 50 };
const TRIAGEM_LABELS = {
  nao_localizado: "Não localizado", resultado_semelhante: "Resultado semelhante",
  resultado_relevante_localizado: "Resultado relevante localizado", inconclusivo: "Inconclusivo",
  analise_humana_necessaria: "Análise humana necessária", ja_e_titular: "Já é titular da marca",
  possui_outra_marca_registrada: "Já possui outra marca registrada",
};
const MOTIVOS_DESCARTE = [
  ["ja_e_cliente", "Já é cliente"], ["fora_do_perfil", "Fora do perfil"],
  ["sem_contato_valido", "Sem contato válido"], ["cnae_incompativel", "CNAE incompatível"],
  ["ja_possui_marca_registrada", "Já possui marca registrada"], ["outro", "Outro"],
];
const message = document.querySelector("#prospeccao-message");

// Achado do usuário: filtro de UF (listagem e critério de campanha) era
// texto livre de valor único -- vira <select multiple> com as 27 UFs.
const UFS_BRASIL = [
  ["AC", "Acre"], ["AL", "Alagoas"], ["AP", "Amapá"], ["AM", "Amazonas"], ["BA", "Bahia"],
  ["CE", "Ceará"], ["DF", "Distrito Federal"], ["ES", "Espírito Santo"], ["GO", "Goiás"],
  ["MA", "Maranhão"], ["MT", "Mato Grosso"], ["MS", "Mato Grosso do Sul"], ["MG", "Minas Gerais"],
  ["PA", "Pará"], ["PB", "Paraíba"], ["PR", "Paraná"], ["PE", "Pernambuco"], ["PI", "Piauí"],
  ["RJ", "Rio de Janeiro"], ["RN", "Rio Grande do Norte"], ["RS", "Rio Grande do Sul"],
  ["RO", "Rondônia"], ["RR", "Roraima"], ["SC", "Santa Catarina"], ["SP", "São Paulo"],
  ["SE", "Sergipe"], ["TO", "Tocantins"],
];
function popularSelecionaresUf() {
  const opcoes = UFS_BRASIL.map(([sigla, nome]) => `<option value="${sigla}">${sigla} — ${nome}</option>`).join("");
  document.querySelectorAll(".prospeccao-uf-select").forEach(select => { select.innerHTML = opcoes; });
}

function escapeHtml(value) {
  const el = document.createElement("span"); el.textContent = value ?? ""; return el.innerHTML;
}
function formatDateTime(value) { return value ? new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "—"; }
function formatDate(value) { return value ? new Intl.DateTimeFormat("pt-BR").format(new Date(`${value}T12:00:00`)) : "—"; }
function readableError(detail, fallback = "Não foi possível concluir a operação.") {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map(item => readableError(item, "")).filter(Boolean);
    return messages.join(" · ") || fallback;
  }
  if (detail && typeof detail === "object") return readableError(detail.message || detail.msg || detail.detail, fallback);
  return fallback;
}
function showMessage(text, kind = "success") {
  message.hidden = false;
  message.textContent = readableError(text, kind === "error" ? "Não foi possível concluir a operação." : "Operação concluída.");
  message.className = `status-message ${kind}`;
}
async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(readableError(data.detail, `Operação não concluída (${response.status})`));
  return data;
}

function statusBadge(status) {
  return `<span class="prospeccao-status-badge is-${escapeHtml(status)}">${escapeHtml(state.statusLabels[status] || status)}</span>`;
}
function triagemBadge(status) {
  if (!status) return `<span class="prospeccao-triagem-badge">Ainda não triado</span>`;
  return `<span class="prospeccao-triagem-badge is-${escapeHtml(status)}">${escapeHtml(TRIAGEM_LABELS[status] || status)}</span>`;
}
function scoreBadge(score) {
  if (score === null || score === undefined) return `<span class="prospeccao-score-badge">— sem score</span>`;
  const nivel = score >= 70 ? "alto" : score >= 40 ? "medio" : "baixo";
  return `<span class="prospeccao-score-badge is-${nivel}">${Math.round(score)}/100</span>`;
}

async function loadDashboard() {
  const data = await api("/v1/admin/prospeccao/dashboard?dias=30");
  const funil = data.funil || {};
  const total = Object.values(funil).reduce((sum, value) => sum + value, 0);
  const entries = [
    [total, "Total no radar", ""],
    [funil.novo || 0, "Novos", "novo"],
    [funil.aprovado || 0, "Aprovados", "aprovado"],
    [funil.convertido_lead || 0, "Convertidos em lead", "convertido_lead"],
    [funil.rejeitado || 0, "Rejeitados", "rejeitado"],
    [funil.duplicado || 0, "Duplicados", "duplicado"],
  ];
  const form = document.querySelector("#prospeccao-filter");
  const current = form.elements.status.value;
  document.querySelector("#prospeccao-metrics").innerHTML = entries.map(([value, label, filterValue]) => {
    const active = filterValue === current || (filterValue === "" && !current);
    return `<button class="prospeccao-metric ${active ? "is-active" : ""}" type="button" data-status-filter="${filterValue}" aria-pressed="${active}"><strong>${value}</strong><span>${label}</span></button>`;
  }).join("");
}

function campanhaStatusLabel(status) {
  return { rascunho: "Rascunho", ativa: "Coletando/ativa", pausada: "Pausada", concluida: "Concluída" }[status] || status;
}
function comoLista(valor) {
  // Campanhas criadas antes do multi-UF/cidade gravaram uma string única
  // em vez de lista no JSON de critérios -- aceita os dois formatos.
  if (!valor) return [];
  return Array.isArray(valor) ? valor : [valor];
}
function renderCampanhaCriterios(criterios) {
  const partes = [];
  if (criterios.cnae_principal) partes.push(`CNAE ${criterios.cnae_principal}`);
  const ufs = comoLista(criterios.uf);
  if (ufs.length) partes.push(ufs.join("/"));
  const cidades = comoLista(criterios.cidade);
  if (cidades.length) partes.push(cidades.join(", "));
  if (criterios.porte) partes.push(criterios.porte);
  if (criterios.data_abertura_de) partes.push(`a partir de ${formatDate(criterios.data_abertura_de)}`);
  return partes.length ? partes.map(escapeHtml).join(" · ") : "Sem filtro (todas as empresas ativas do cache)";
}
function popularFiltroCampanha(itens) {
  const select = document.querySelector("#prospeccao-filtro-campanha");
  const atual = select.value;
  select.innerHTML = `<option value="">Todas</option>` + itens.map(item => `<option value="${item.id}">${escapeHtml(item.nome)}</option>`).join("");
  if (itens.some(item => String(item.id) === atual)) select.value = atual;
}
async function loadCampanhas() {
  const data = await api("/v1/admin/prospeccao/campanhas");
  const tbody = document.querySelector("#campanhas-rows");
  popularFiltroCampanha(data.itens);
  if (!data.itens.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="prospeccao-empty-cell">Nenhuma campanha criada ainda.</td></tr>`;
    return;
  }
  tbody.innerHTML = data.itens.map(item => `
    <tr data-id="${item.id}" data-status="${escapeHtml(item.status)}">
      <td><strong>${escapeHtml(item.nome)}</strong>${item.descricao ? `<br><small>${escapeHtml(item.descricao)}</small>` : ""}</td>
      <td>${renderCampanhaCriterios(item.criterios_busca || {})}</td>
      <td><span class="prospeccao-campanha-status is-${escapeHtml(item.status)}">${item.status === "ativa" ? `<span class="prospeccao-spinner" aria-hidden="true"></span>` : ""}${escapeHtml(campanhaStatusLabel(item.status))}</span></td>
      <td>${item.prospects_gerados}</td>
      <td>
        <button class="secondary-button" data-coletar type="button" ${item.status === "ativa" ? "disabled" : ""}>${item.status === "ativa" ? "Coletando…" : item.status === "concluida" ? "Coletar novamente" : "Coletar"}</button>
        ${item.status === "ativa" ? `<button class="secondary-button" data-cancelar-campanha type="button" title="Interrompe uma coleta travada, liberando a campanha para exclusão">Cancelar</button>` : ""}
        <button class="secondary-button" data-excluir-campanha type="button" ${item.status === "ativa" ? "disabled" : ""} title="${item.status === "ativa" ? "Não é possível excluir com coleta em andamento" : "Excluir campanha"}">Excluir</button>
      </td>
    </tr>`).join("");
  iniciarPollingCampanhasSeNecessario();
}

function prospectActions(item) {
  const botoes = [];
  botoes.push(`<button class="secondary-button" data-detalhe type="button">Detalhes</button>`);
  if (!item.presenca_digital) botoes.push(`<button class="secondary-button" data-enriquecer type="button">Verificar site</button>`);
  if (!item.triagem_marca_status) botoes.push(`<button class="secondary-button" data-triar type="button">Triar marca</button>`);
  if (item.triagem_marca_status && item.score === null) botoes.push(`<button class="secondary-button" data-score type="button">Calcular score</button>`);
  if (item.status === "novo") {
    botoes.push(`<button class="secondary-button" data-aprovar type="button">Aprovar</button>`);
    if (item.triagem_marca_status === "ja_e_titular") {
      botoes.push(`<button class="secondary-button" data-descartar-ja-titular type="button" title="Prospect já consta como titular dessa marca -- confirme antes de descartar">Descartar (já tem marca)</button>`);
    }
    botoes.push(`<button class="secondary-button" data-rejeitar type="button">Rejeitar</button>`);
  }
  // Achado do usuário (08/09/2026): o botão aparecia também em status "novo",
  // mas o backend só converte quem já está "aprovado" (POST /converter-lead,
  // achado FASE5-9) -- clicar em "novo" sempre falhava com 422, dando a
  // impressão de que "não funciona". Só mostra quando pode de fato converter.
  if (item.status === "aprovado") botoes.push(`<button class="primary-button" data-converter type="button">Converter em lead</button>`);
  return botoes.join("");
}
function renderProspects(data) {
  const target = document.querySelector("#prospeccao-list");
  if (!data.itens.length) { target.innerHTML = `<div class="prospeccao-empty">Nenhum prospect encontrado com esses filtros.</div>`; renderPagination(data); return; }
  target.innerHTML = data.itens.map(item => `
    <article class="prospeccao-item" data-id="${item.id}">
      <div class="prospeccao-identidade">
        <h3>${escapeHtml(item.nome_fantasia || item.razao_social)}</h3>
        <p>${escapeHtml(item.razao_social)}${item.cnpj ? ` · ${escapeHtml(item.cnpj)}` : ""}</p>
        <small>${[item.cidade, item.uf].filter(Boolean).map(escapeHtml).join("/") || "Localização não informada"}${item.cnae_principal ? ` · CNAE ${escapeHtml(item.cnae_principal)}${item.cnae_principal_descricao ? ` (${escapeHtml(item.cnae_principal_descricao)})` : ""}` : ""}</small>
        <small class="prospeccao-contato">${item.email ? escapeHtml(item.email) : "Sem e-mail"} · ${item.telefone ? escapeHtml(item.telefone) : "Sem telefone"}</small>
      </div>
      <div class="prospeccao-sinais">
        ${statusBadge(item.status)}
        ${scoreBadge(item.score)}
        ${triagemBadge(item.triagem_marca_status)}
      </div>
      <div class="prospeccao-acoes">${prospectActions(item)}</div>
    </article>`).join("");
  renderPagination(data);
}
function renderPagination(data) {
  const nav = document.querySelector("#prospeccao-pagination");
  const totalPages = Math.max(1, Math.ceil(data.total / state.pageSize));
  const currentPage = Math.floor(state.offset / state.pageSize) + 1;
  nav.hidden = false;
  document.querySelector("#prospeccao-page-summary").textContent = `Página ${currentPage} de ${totalPages} · ${data.total} prospect(s)`;
  document.querySelector("#prospeccao-prev").disabled = state.offset === 0;
  document.querySelector("#prospeccao-next").disabled = currentPage >= totalPages;
}
async function loadProspects() {
  // Achado do usuário: UF já vinha certo aqui (URLSearchParams a partir de
  // um FormData preserva múltiplos valores de um <select multiple>,
  // diferente de Object.fromEntries) -- só cidade precisa virar vários
  // parâmetros "cidade" a partir da lista separada por vírgula.
  const form = document.querySelector("#prospeccao-filter");
  const params = new URLSearchParams(new FormData(form));
  params.delete("cidade");
  (new FormData(form).get("cidade") || "").split(",").map(v => v.trim()).filter(Boolean)
    .forEach(cidade => params.append("cidade", cidade));
  [...params.entries()].forEach(([key, value]) => { if (!String(value).trim()) params.delete(key); });
  params.set("limite", state.pageSize); params.set("deslocamento", state.offset);
  const data = await api(`/v1/admin/prospects?${params}`);
  renderProspects(data);
}
async function reloadAll() { await Promise.all([loadDashboard(), loadProspects()]); }

// --- Cache nacional de empresas (CNPJ/RFB) -- gatilho restrito a superadmin ---

const CACHE_RFB_STATUS_LABELS = { executando: "Em andamento", concluido: "Concluída", erro: "Falhou" };
let pollingCacheRfb = false;

function renderCacheRfbStatus(execucoes) {
  const alvo = document.querySelector("#cache-rfb-status");
  if (!execucoes.length) { alvo.innerHTML = "Nenhuma importação registrada ainda."; return; }
  const ultima = execucoes[0];
  const linhas = [
    `<strong>${escapeHtml(CACHE_RFB_STATUS_LABELS[ultima.status] || ultima.status)}</strong>${ultima.status === "executando" ? `<span class="prospeccao-spinner" aria-hidden="true"></span>` : ""}`,
    ultima.periodo ? `Período: ${escapeHtml(ultima.periodo)}` : null,
    ultima.etapa_atual ? `Etapa: ${escapeHtml(ultima.etapa_atual)}` : null,
    ultima.total_processados ? `${ultima.total_processados.toLocaleString("pt-BR")} processados, ${ultima.total_validos.toLocaleString("pt-BR")} válidos` : null,
    ultima.erro ? `<span class="status-message error inline">${escapeHtml(ultima.erro)}</span>` : null,
    `Solicitado por ${escapeHtml(ultima.solicitado_por || "—")} em ${formatDateTime(ultima.solicitado_em)}${ultima.concluido_em ? ` · concluído em ${formatDateTime(ultima.concluido_em)}` : ""}`,
  ].filter(Boolean);
  alvo.innerHTML = linhas.join("<br>");

  const botao = document.querySelector("#importar-cnpj-rfb");
  botao.disabled = ultima.status === "executando";
  botao.textContent = ultima.status === "executando" ? "Importando…" : "Importar agora";

  if (ultima.status === "executando" && !pollingCacheRfb) {
    pollingCacheRfb = true;
    const intervalo = setInterval(async () => {
      try {
        const dados = await api("/v1/admin/prospeccao/importar-cnpj-rfb?limite=1");
        renderCacheRfbStatus(dados);
        if (dados[0]?.status !== "executando") {
          clearInterval(intervalo);
          pollingCacheRfb = false;
          showMessage(
            dados[0]?.status === "concluido"
              ? `Importação concluída: ${dados[0].total_validos.toLocaleString("pt-BR")} estabelecimentos válidos no cache.`
              : "Importação falhou — veja o detalhe no painel do cache nacional de empresas.",
            dados[0]?.status === "concluido" ? "success" : "error",
          );
        }
      } catch {
        clearInterval(intervalo);
        pollingCacheRfb = false;
      }
    }, 5000);
  }
}
async function loadCacheRfbStatus() {
  const dados = await api("/v1/admin/prospeccao/importar-cnpj-rfb?limite=1");
  renderCacheRfbStatus(dados);
}
async function configurarBotaoImportarCnpjRfb() {
  let usuario;
  try {
    usuario = await api("/v1/auth/me");
  } catch {
    document.querySelector("#cache-rfb-section").hidden = true;
    return;
  }
  // Achado P2 da Fase 16.1 (24/09/2026): um usuário só com prospeccao.view
  // (ex.: perfil auditor) via os botões de criar/remover supressão mesmo
  // sem permissão -- clicar só resultava em 403. Mesmo padrão de
  // admin-regras-automaticas.js/admin-financeiro-*.js.
  state.canManage = Boolean(
    usuario.superadmin || usuario.perfil === "administrador" || (usuario.permissoes || []).includes("prospeccao.manage"),
  );
  document.querySelector("#open-supressao").hidden = !state.canManage;

  // Achado 16.2 da auditoria fina do Radar de Prospecção (24/09/2026):
  // GET /importar-cnpj-rfb agora é restrito a superadmin igual ao POST --
  // a seção inteira (não só o botão de disparar) só faz sentido, e só
  // responde sem 403, pra quem é superadmin. Achado P2 do Codex (PR #138):
  // isolado num try/catch próprio pra uma falha/travamento aqui não impedir
  // a inicialização de state.canManage acima, que não depende disso.
  document.querySelector("#cache-rfb-section").hidden = !usuario.superadmin;
  if (usuario.superadmin) {
    document.querySelector("#importar-cnpj-rfb").hidden = false;
    try {
      await loadCacheRfbStatus();
    } catch (error) { showMessage(error.message, "error"); }
  }
}
document.querySelector("#importar-cnpj-rfb").addEventListener("click", async () => {
  if (!confirm("Disparar a importação do cache nacional de empresas? É uma operação pesada (vários GB) e afeta todas as organizações da plataforma.")) return;
  try {
    await api("/v1/admin/prospeccao/importar-cnpj-rfb", { method: "POST", body: JSON.stringify({}) });
    showMessage("Importação disparada — acompanhe pelo painel do cache nacional de empresas, que atualiza sozinho.");
    await loadCacheRfbStatus();
  } catch (error) { showMessage(error.message, "error"); }
});

function iniciarPollingCampanhasSeNecessario() {
  const temAtiva = document.querySelector('#campanhas-rows [data-status="ativa"]');
  if (!temAtiva || state.pollingCampanhas) return;
  state.pollingCampanhas = true;
  const intervalo = setInterval(async () => {
    try {
      await loadCampanhas(); // reentra aqui e chama iniciarPollingCampanhasSeNecessario() de novo -- não faz nada enquanto pollingCampanhas=true
      if (!document.querySelector('#campanhas-rows [data-status="ativa"]')) {
        clearInterval(intervalo);
        state.pollingCampanhas = false;
        showMessage("Coleta concluída — prospects novos já aparecem na lista.");
        await reloadAll();
      }
    } catch {
      clearInterval(intervalo);
      state.pollingCampanhas = false;
    }
  }, 4000);
}

function fatorLabel(regra) {
  return {
    SITUACAO_CADASTRAL_ATIVA: "Situação cadastral ativa", TEMPO_DE_ABERTURA: "Tempo de abertura",
    PRESENCA_DIGITAL_ATIVA: "Presença digital ativa", TRIAGEM_DE_MARCA: "Resultado da triagem de marca",
  }[regra] || regra;
}
async function abrirDetalhe(id) {
  const dialog = document.querySelector("#detalhe-dialog");
  const corpo = document.querySelector("#detalhe-corpo");
  corpo.innerHTML = `<p class="prospeccao-loading">Carregando…</p>`;
  dialog.showModal();
  try {
    const [prospect, timeline, triagens] = await Promise.all([
      api(`/v1/admin/prospects/${id}`), api(`/v1/admin/prospects/${id}/timeline`), api(`/v1/admin/prospects/${id}/triagens`),
    ]);
    document.querySelector("#detalhe-nome").textContent = prospect.nome_fantasia || prospect.razao_social;
    document.querySelector("#detalhe-status-badge").innerHTML = statusBadge(prospect.status);

    const presenca = prospect.presenca_digital
      ? (prospect.presenca_digital.ativo === null
          ? "Sem site cadastrado"
          : prospect.presenca_digital.ativo ? `Site ativo (HTTP ${prospect.presenca_digital.status_code})` : "Site fora do ar ou não respondeu")
      : "Ainda não verificado";

    const scoreFatores = (prospect.score_detalhe?.fatores || [])
      .map(fator => `<li>${escapeHtml(fatorLabel(fator.regra))}: <strong>+${fator.peso}</strong></li>`).join("") || "<li>Nenhum fator pontuado ainda.</li>";

    const triagemItens = triagens.itens.length
      ? triagens.itens.map(item => `<li><strong>${escapeHtml(TRIAGEM_LABELS[item.classificacao] || item.classificacao)}</strong> — ${escapeHtml(item.justificativa)}<br><small>Marca pesquisada: "${escapeHtml(item.marca_pesquisada)}" · ${formatDateTime(item.criado_em)}</small></li>`).join("")
      : "<li>Nenhuma triagem executada ainda.</li>";

    const timelineItens = timeline.itens.map(item => `<li><strong>${escapeHtml(state.statusLabels[item.status] || item.status)}</strong> — ${formatDateTime(item.entrou_em)}${item.por ? ` · ${escapeHtml(item.por)}` : ""}</li>`).join("");

    corpo.innerHTML = `
      <section class="prospeccao-detalhe-bloco">
        <h3>Contato</h3>
        <p>E-mail: ${prospect.email ? escapeHtml(prospect.email) : "não informado"}</p>
        <p>Telefone: ${prospect.telefone ? escapeHtml(prospect.telefone) : "não informado"}</p>
        <p>Site: ${prospect.site ? escapeHtml(prospect.site) : "não informado"}</p>
      </section>
      <section class="prospeccao-detalhe-bloco">
        <h3>Presença digital</h3><p>${escapeHtml(presenca)}</p>
      </section>
      <section class="prospeccao-detalhe-bloco">
        <h3>Score comercial ${scoreBadge(prospect.score)}</h3>
        <ul class="prospeccao-fatores">${scoreFatores}</ul>
      </section>
      <section class="prospeccao-detalhe-bloco">
        <h3>Triagem de marca</h3>
        <p class="prospeccao-disclaimer">${escapeHtml(triagens.disclaimer)}</p>
        <ul class="prospeccao-triagens">${triagemItens}</ul>
      </section>
      <section class="prospeccao-detalhe-bloco">
        <h3>Linha do tempo</h3>
        <ul class="prospeccao-timeline">${timelineItens}</ul>
      </section>`;
  } catch (error) {
    corpo.innerHTML = `<p class="status-message error">${escapeHtml(error.message)}</p>`;
  }
}

document.querySelector("#prospeccao-list").addEventListener("click", async event => {
  const card = event.target.closest("[data-id]"); if (!card) return;
  const id = card.dataset.id;
  const button = event.target.closest("button"); if (!button) return;
  try {
    if (button.dataset.detalhe !== undefined) { await abrirDetalhe(id); return; }
    if (button.dataset.enriquecer !== undefined) { await api(`/v1/admin/prospects/${id}/enriquecer`, { method: "POST" }); showMessage("Verificação de site agendada."); }
    else if (button.dataset.triar !== undefined) { await api(`/v1/admin/prospects/${id}/triar-marca`, { method: "POST" }); showMessage("Triagem de marca agendada."); }
    else if (button.dataset.score !== undefined) { await api(`/v1/admin/prospects/${id}/calcular-score`, { method: "POST" }); showMessage("Cálculo de score agendado."); }
    else if (button.dataset.aprovar !== undefined) { await api(`/v1/admin/prospects/${id}/aprovar`, { method: "POST" }); showMessage("Prospect aprovado."); }
    else if (button.dataset.descartarJaTitular !== undefined) {
      if (!confirm("O prospect já consta como titular dessa marca -- descartar mesmo assim?")) return;
      await api(`/v1/admin/prospects/${id}`, { method: "PATCH", body: JSON.stringify({ status: "rejeitado", motivo_descarte: "ja_possui_marca_registrada" }) });
      showMessage("Prospect descartado (já possui marca registrada).");
    }
    else if (button.dataset.rejeitar !== undefined) {
      const motivo = prompt(`Motivo do descarte:\n${MOTIVOS_DESCARTE.map(([valor, label]) => `${valor} — ${label}`).join("\n")}`, "fora_do_perfil");
      if (!motivo) return;
      await api(`/v1/admin/prospects/${id}`, { method: "PATCH", body: JSON.stringify({ status: "rejeitado", motivo_descarte: motivo }) });
      showMessage("Prospect rejeitado.");
    } else if (button.dataset.converter !== undefined) {
      if (!confirm("Converter este prospect em lead?")) return;
      const result = await api(`/v1/admin/prospects/${id}/converter-lead`, { method: "POST" });
      // Achado do usuário: depois de aprovar/converter, o fluxo não levava a
      // lugar nenhum -- abre direto o cadastro do lead recém-criado/vinculado
      // em vez de só mostrar um toast e deixar o operador procurar manualmente.
      window.location.href = `/admin/leads?lead_id=${result.lead_id}`;
      return;
    } else return;
    await reloadAll();
  } catch (error) { showMessage(error.message, "error"); }
});

document.querySelector("#prospeccao-metrics").addEventListener("click", event => {
  const button = event.target.closest("[data-status-filter]"); if (!button) return;
  document.querySelector("#prospeccao-filter").elements.status.value = button.dataset.statusFilter;
  state.offset = 0;
  loadProspects().then(loadDashboard).catch(error => showMessage(error.message, "error"));
});
document.querySelector("#prospeccao-filter").addEventListener("submit", event => {
  event.preventDefault(); state.offset = 0;
  loadProspects().then(loadDashboard).catch(error => showMessage(error.message, "error"));
});
document.querySelector("#prospeccao-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.pageSize); loadProspects().catch(error => showMessage(error.message, "error")); });
document.querySelector("#prospeccao-next").addEventListener("click", () => { state.offset += state.pageSize; loadProspects().catch(error => showMessage(error.message, "error")); });

document.querySelector("#close-detalhe").addEventListener("click", () => document.querySelector("#detalhe-dialog").close());

const helpDialog = document.querySelector("#prospeccao-help");
document.querySelector("#prospeccao-help-open").addEventListener("click", () => helpDialog.showModal());
document.querySelector("#prospeccao-help-close").addEventListener("click", () => helpDialog.close());

const manualDialog = document.querySelector("#manual-dialog");
document.querySelector("#open-manual").addEventListener("click", () => { document.querySelector("#manual-form").reset(); manualDialog.showModal(); });
document.querySelector("#close-manual").addEventListener("click", () => manualDialog.close());
document.querySelector("#cancel-manual").addEventListener("click", () => manualDialog.close());
document.querySelector("#manual-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form));
  try {
    await api("/v1/admin/prospects", { method: "POST", body: JSON.stringify({ ...values, cnpj: values.cnpj || null, nome_fantasia: values.nome_fantasia || null }) });
    showMessage("Prospect cadastrado."); manualDialog.close(); form.reset(); await reloadAll();
  } catch (error) { showMessage(error.message, "error"); }
});

const importDialog = document.querySelector("#import-dialog");
const importResult = document.querySelector("#import-result");
document.querySelector("#open-import").addEventListener("click", () => { importResult.hidden = true; document.querySelector("#import-form").reset(); importDialog.showModal(); });
document.querySelector("#close-import").addEventListener("click", () => importDialog.close());
document.querySelector("#cancel-import").addEventListener("click", () => importDialog.close());
document.querySelector("#import-form").addEventListener("submit", async event => {
  event.preventDefault();
  const file = document.querySelector("#import-file").files[0];
  if (!file) return;
  const submit = document.querySelector("#import-submit");
  submit.disabled = true; importResult.hidden = true;
  const body = new FormData(); body.append("arquivo", file);
  try {
    const response = await fetch("/v1/admin/prospects/importar", { method: "POST", body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(readableError(data.detail, `Falha na importação (${response.status})`));
    importResult.hidden = false; importResult.className = "import-result success";
    importResult.innerHTML = `<strong>${data.criados} prospect(s) importado(s).</strong><ul><li>${data.duplicados} já existiam (radar, lead ou cliente)</li><li>${data.invalidos} linha(s) inválida(s)</li></ul>`;
    await reloadAll();
  } catch (error) { importResult.hidden = false; importResult.className = "import-result error"; importResult.textContent = error.message; }
  finally { submit.disabled = false; }
});

const campanhaDialog = document.querySelector("#campanha-dialog");
document.querySelector("#open-campanha").addEventListener("click", () => { document.querySelector("#campanha-form").reset(); campanhaDialog.showModal(); });
document.querySelector("#close-campanha").addEventListener("click", () => campanhaDialog.close());
document.querySelector("#cancel-campanha").addEventListener("click", () => campanhaDialog.close());
document.querySelector("#campanha-form").addEventListener("submit", async event => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const values = Object.fromEntries(formData);
  const criterios_busca = {};
  ["cnae_principal", "porte", "data_abertura_de"].forEach(campo => { if (values[campo]) criterios_busca[campo] = values[campo]; });
  // Achado do usuário: UF/cidade passam a aceitar vários valores --
  // Object.fromEntries descarta tudo exceto o último valor de um
  // <select multiple>, por isso usa getAll() aqui.
  const ufsSelecionadas = formData.getAll("uf").map(v => v.trim().toUpperCase()).filter(Boolean);
  if (ufsSelecionadas.length) criterios_busca.uf = ufsSelecionadas;
  const cidadesInformadas = (values.cidade || "").split(",").map(v => v.trim()).filter(Boolean);
  if (cidadesInformadas.length) criterios_busca.cidade = cidadesInformadas;
  try {
    await api("/v1/admin/prospeccao/campanhas", {
      method: "POST",
      body: JSON.stringify({ nome: values.nome, descricao: values.descricao || null, criterios_busca, meta_prospects: Number(values.meta_prospects) || null }),
    });
    showMessage("Campanha criada."); campanhaDialog.close(); await loadCampanhas();
  } catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#campanhas-rows").addEventListener("click", async event => {
  const coletarButton = event.target.closest("[data-coletar]");
  if (coletarButton) {
    const id = coletarButton.closest("[data-id]").dataset.id;
    coletarButton.disabled = true;
    try {
      await api(`/v1/admin/prospeccao/campanhas/${id}/coletar`, { method: "POST" });
      showMessage("Coleta em andamento — acompanhe pelo status \"Coletando/ativa\" na lista abaixo, que atualiza sozinho.");
      await loadCampanhas();
    } catch (error) { showMessage(error.message, "error"); coletarButton.disabled = false; }
    return;
  }
  const cancelarButton = event.target.closest("[data-cancelar-campanha]");
  if (cancelarButton) {
    const row = cancelarButton.closest("[data-id]");
    const id = row.dataset.id;
    const nome = row.querySelector("strong")?.textContent || "esta campanha";
    if (!confirm(`Interromper a coleta de "${nome}"? Use isso quando a campanha ficar travada em "Coletando/ativa" sem progredir.`)) return;
    cancelarButton.disabled = true;
    try {
      await api(`/v1/admin/prospeccao/campanhas/${id}/cancelar`, { method: "POST" });
      showMessage("Coleta interrompida. A campanha já pode ser excluída ou coletada novamente.");
      await loadCampanhas();
    } catch (error) { showMessage(error.message, "error"); cancelarButton.disabled = false; }
    return;
  }
  const excluirButton = event.target.closest("[data-excluir-campanha]");
  if (excluirButton) {
    const row = excluirButton.closest("[data-id]");
    const id = row.dataset.id;
    const nome = row.querySelector("strong")?.textContent || "esta campanha";
    if (!confirm(`Excluir "${nome}"? Os prospects já gerados por ela permanecem no radar, só perdem o vínculo com a campanha.`)) return;
    excluirButton.disabled = true;
    try {
      await api(`/v1/admin/prospeccao/campanhas/${id}`, { method: "DELETE" });
      showMessage("Campanha excluída.");
      await loadCampanhas();
    } catch (error) { showMessage(error.message, "error"); excluirButton.disabled = false; }
  }
});

const politicaDialog = document.querySelector("#politica-dialog");
document.querySelector("#open-politica").addEventListener("click", async () => {
  try {
    const politica = await api("/v1/admin/prospeccao/politica");
    const form = document.querySelector("#politica-form");
    form.elements.aprovacao_automatica_ativa.checked = politica.aprovacao_automatica_ativa;
    form.elements.score_minimo_aprovacao.value = politica.score_minimo_aprovacao ?? "";
    politicaDialog.showModal();
  } catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#close-politica").addEventListener("click", () => politicaDialog.close());
document.querySelector("#cancel-politica").addEventListener("click", () => politicaDialog.close());
document.querySelector("#politica-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api("/v1/admin/prospeccao/politica", {
      method: "PUT",
      body: JSON.stringify({
        aprovacao_automatica_ativa: form.elements.aprovacao_automatica_ativa.checked,
        score_minimo_aprovacao: form.elements.score_minimo_aprovacao.value ? Number(form.elements.score_minimo_aprovacao.value) : null,
      }),
    });
    showMessage("Política de aprovação atualizada."); politicaDialog.close();
  } catch (error) { showMessage(error.message, "error"); }
});

// --- Fase 1 do roadmap pos-auditoria do CRM (06/09/2026): central de
// duplicidades e mesclagem assistida. ---

const CRITERIO_DUPLICATA_LABELS = { cnpj: "CNPJ", email: "E-mail", telefone: "Telefone" };

function renderDuplicatas(data) {
  const alvo = document.querySelector("#duplicatas-lista");
  if (!data.grupos.length) {
    alvo.innerHTML = `<p class="prospeccao-empty">Nenhuma duplicidade encontrada entre os prospects em aberto.</p>`;
    return;
  }
  alvo.innerHTML = data.grupos.map((grupo, indiceGrupo) => `
    <article class="prospeccao-duplicata-grupo">
      <h4>${escapeHtml(CRITERIO_DUPLICATA_LABELS[grupo.criterio] || grupo.criterio)}: ${escapeHtml(grupo.valor)}</h4>
      <ul class="prospeccao-duplicata-itens">
        ${grupo.itens.map(item => `
          <li>
            <div>
              <strong>${escapeHtml(item.nome_fantasia || item.razao_social)}</strong>
              <small>${escapeHtml(item.razao_social)}${item.cnpj ? ` · ${escapeHtml(item.cnpj)}` : ""} · ${escapeHtml(item.email || "sem e-mail")} · ${escapeHtml(item.telefone || "sem telefone")}</small>
            </div>
            <button class="secondary-button" type="button" data-manter-grupo="${indiceGrupo}" data-manter-id="${item.id}">Manter este e mesclar os outros</button>
          </li>`).join("")}
      </ul>
    </article>`).join("");
  alvo.dataset.grupos = JSON.stringify(data.grupos);
}

async function loadDuplicatas() {
  const data = await api("/v1/admin/prospects/duplicatas");
  renderDuplicatas(data);
}

document.querySelector("#duplicatas-verificar").addEventListener("click", async () => {
  try { await loadDuplicatas(); } catch (error) { showMessage(error.message, "error"); }
});

document.querySelector("#duplicatas-lista").addEventListener("click", async event => {
  const botao = event.target.closest("button[data-manter-id]");
  if (!botao) return;
  const grupos = JSON.parse(document.querySelector("#duplicatas-lista").dataset.grupos || "[]");
  const grupo = grupos[Number(botao.dataset.manterGrupo)];
  const primarioId = Number(botao.dataset.manterId);
  const outrosIds = grupo.itens.map(item => item.id).filter(id => id !== primarioId);
  if (!confirm(`Mesclar ${outrosIds.length} prospect(s) dentro de "${grupo.itens.find(item => item.id === primarioId).razao_social}"? Os dados vazios do principal serão completados com os do(s) duplicado(s); o histórico é preservado.`)) return;
  try {
    for (const duplicadoId of outrosIds) {
      await api(`/v1/admin/prospects/${primarioId}/mesclar`, { method: "POST", body: JSON.stringify({ duplicado_id: duplicadoId }) });
    }
    showMessage("Prospects mesclados.");
    await Promise.all([loadDuplicatas(), loadProspects(), loadDashboard()]);
  } catch (error) { showMessage(error.message, "error"); }
});

// --- Fase 16.1 da auditoria fina do Radar de Prospecção (24/09/2026):
// lista de supressão (opt-out) tinha backend completo desde a Fase 5 mas
// nenhuma tela -- só dava pra usar via API direta. ---

function renderSupressoes(data) {
  const alvo = document.querySelector("#supressoes-lista");
  if (!data.itens.length) {
    alvo.innerHTML = `<p class="prospeccao-empty">Nenhuma supressão cadastrada ainda.</p>`;
  } else {
    alvo.innerHTML = `<ul class="prospeccao-supressoes-lista">${data.itens.map(item => `
      <li data-id="${item.id}">
        <div>
          <strong>${item.cnpj ? escapeHtml(item.cnpj) : escapeHtml(item.email)}</strong>
          ${item.cnpj && item.email ? ` · ${escapeHtml(item.email)}` : ""}
          ${item.motivo ? `<br><small>${escapeHtml(item.motivo)}</small>` : ""}
          <br><small>Adicionado por ${escapeHtml(item.criado_por)} em ${formatDateTime(item.criado_em)}</small>
        </div>
        ${state.canManage ? `<button class="secondary-button" data-remover-supressao type="button">Remover</button>` : ""}
      </li>`).join("")}</ul>`;
  }
  renderSupressoesPagination(data);
}
function renderSupressoesPagination(data) {
  const nav = document.querySelector("#supressoes-pagination");
  if (data.total <= data.itens.length && state.supressoesOffset === 0) { nav.hidden = true; return; }
  const totalPages = Math.max(1, Math.ceil(data.total / state.supressoesPageSize));
  const currentPage = Math.floor(state.supressoesOffset / state.supressoesPageSize) + 1;
  nav.hidden = false;
  document.querySelector("#supressoes-page-summary").textContent = `Página ${currentPage} de ${totalPages} · ${data.total} supressão(ões)`;
  document.querySelector("#supressoes-prev").disabled = state.supressoesOffset === 0;
  document.querySelector("#supressoes-next").disabled = currentPage >= totalPages;
}
async function loadSupressoes() {
  const data = await api(`/v1/admin/prospeccao/supressoes?limite=${state.supressoesPageSize}&deslocamento=${state.supressoesOffset}`);
  renderSupressoes(data);
}
document.querySelector("#supressoes-prev").addEventListener("click", () => {
  state.supressoesOffset = Math.max(0, state.supressoesOffset - state.supressoesPageSize);
  loadSupressoes().catch(error => showMessage(error.message, "error"));
});
document.querySelector("#supressoes-next").addEventListener("click", () => {
  state.supressoesOffset += state.supressoesPageSize;
  loadSupressoes().catch(error => showMessage(error.message, "error"));
});

const supressaoDialog = document.querySelector("#supressao-dialog");
document.querySelector("#open-supressao").addEventListener("click", () => { document.querySelector("#supressao-form").reset(); supressaoDialog.showModal(); });
document.querySelector("#close-supressao").addEventListener("click", () => supressaoDialog.close());
document.querySelector("#cancel-supressao").addEventListener("click", () => supressaoDialog.close());
document.querySelector("#supressao-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form));
  if (!values.cnpj && !values.email) { showMessage("Informe pelo menos o CNPJ ou o e-mail.", "error"); return; }
  try {
    await api("/v1/admin/prospeccao/supressoes", {
      method: "POST",
      body: JSON.stringify({ cnpj: values.cnpj || null, email: values.email || null, motivo: values.motivo || null }),
    });
    showMessage("Supressão adicionada."); supressaoDialog.close(); form.reset();
    state.supressoesOffset = 0;
    await Promise.all([loadSupressoes(), loadProspects(), loadDashboard()]);
  } catch (error) { showMessage(error.message, "error"); }
});
document.querySelector("#supressoes-lista").addEventListener("click", async event => {
  const botao = event.target.closest("[data-remover-supressao]"); if (!botao) return;
  const id = botao.closest("[data-id]").dataset.id;
  if (!confirm("Remover esta supressão? O CNPJ/e-mail volta a poder ser prospectado normalmente.")) return;
  botao.disabled = true;
  try {
    await api(`/v1/admin/prospeccao/supressoes/${id}`, { method: "DELETE" });
    showMessage("Supressão removida.");
    await loadSupressoes();
  } catch (error) { showMessage(error.message, "error"); botao.disabled = false; }
});

popularSelecionaresUf();
// configurarBotaoImportarCnpjRfb() precisa terminar antes de loadSupressoes()
// pra state.canManage já estar certo quando os botões de remover renderizarem.
configurarBotaoImportarCnpjRfb()
  .then(loadSupressoes)
  .catch(error => showMessage(error.message, "error"));
Promise.all([loadDashboard(), loadCampanhas(), loadProspects()]).catch(error => showMessage(error.message, "error"));

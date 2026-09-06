async function funilApi(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Falha ao carregar (${r.status})`);
  return r.json();
}
function funilEsc(v) { const s = document.createElement("span"); s.textContent = v ?? ""; return s.innerHTML; }
function funilMsg(text) { const m = document.querySelector("#funil-message"); m.hidden = false; m.textContent = text; m.className = "status-message error"; }
function funilPct(x) { return (x * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%"; }
function funilMoeda(x) { return Number(x || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }); }
// Evita style="width:...%" inline (bloqueado pelo CSP style-src estrito): arredonda
// para o multiplo de 5 mais proximo e retorna a classe utilitaria w-pct-N.
function funilPctClass(value) { return `w-pct-${Math.min(100, Math.max(0, Math.round((Number(value) || 0) / 5) * 5))}`; }

function funilRender(d) {
  const r = d.resultado || {};
  const tiles = [
    ["Abertas", r.aberto ?? 0],
    ["Ganhos", r.ganho ?? 0],
    ["Perdidos", r.perdido ?? 0],
    ["Conversão", funilPct(d.taxa_conversao || 0)],
    // Itens 48-49 da auditoria completa do CRM (06/09/2026): pipeline_previsto
    // soma o valor de propostas enviadas/visualizadas ainda sem decisão;
    // forecast_ponderado pondera cada uma pela chance histórica de fechar,
    // dado a fase atual do lead -- nenhum dos dois existia antes.
    ["Pipeline em aberto", funilMoeda(d.pipeline_previsto)],
    ["Forecast ponderado", funilMoeda(d.forecast_ponderado)],
  ];
  document.querySelector("#funil-tiles").innerHTML = tiles
    .map(([l, v]) => `<article class="funil-tile"><span>${funilEsc(l)}</span><strong>${funilEsc(String(v))}</strong></article>`)
    .join("");

  // Itens 41-43 da auditoria completa do CRM (06/09/2026): taxa_da_etapa_anterior
  // mostra o quanto sobrevive da etapa anterior para esta -- diferente do total
  // absoluto da barra, que só mostra a foto atual (quantos estão parados ali agora).
  const funil = d.funil || [];
  const conversaoPorEtapa = Object.fromEntries((d.conversao_por_etapa || []).map(c => [c.fase, c]));
  const max = Math.max(1, ...funil.map(f => f.total));
  document.querySelector("#funil-bars").innerHTML = funil
    .map(f => {
      const c = conversaoPorEtapa[f.fase];
      const taxa = c && typeof c.taxa_da_etapa_anterior === "number" ? ` <span class="funil-bar-taxa">(${funilPct(c.taxa_da_etapa_anterior)} da etapa anterior)</span>` : "";
      return `<div class="funil-bar"><span class="funil-bar-label">${funilEsc(f.label)}${taxa}</span><div class="funil-bar-track"><div class="funil-bar-fill ${funilPctClass(f.total / max * 100)}"></div></div><strong class="funil-bar-total">${funilEsc(String(f.total))}</strong></div>`;
    })
    .join("");

  const motivos = d.perdas_por_motivo || [];
  document.querySelector("#funil-motivos").innerHTML = motivos.length
    ? motivos.map(m => `<div class="funil-motivo"><span>${funilEsc(m.label)}</span><strong>${funilEsc(String(m.total))}</strong></div>`).join("")
    : `<p class="funil-empty">Nenhuma perda registrada ainda.</p>`;

  const prod = d.produtividade || [];
  const rows = prod
    .map(p => `<tr><td><strong>${funilEsc(p.nome)}</strong></td><td>${funilEsc(String(p.abertas))}</td><td class="${p.atrasadas ? "funil-alerta" : ""}">${funilEsc(String(p.atrasadas))}</td><td>${funilEsc(String(p.ganhos))}</td><td>${funilEsc(String(p.perdidos))}</td><td>${funilEsc(funilPct(p.taxa_conversao || 0))}</td></tr>`)
    .join("");
  document.querySelector("#funil-prod").innerHTML = `<div class="funil-table-scroll"><table class="funil-table"><thead><tr><th>Responsável</th><th>Abertas</th><th>Atrasadas</th><th>Ganhos</th><th>Perdidos</th><th>Conversão</th></tr></thead><tbody>${rows || `<tr><td colspan="6" class="funil-empty">Sem dados.</td></tr>`}</tbody></table></div>`;

  const origens = d.conversao_por_origem || [];
  const linhasOrigem = origens
    .map(o => `<tr><td><strong>${funilEsc(o.origem)}</strong></td><td>${funilEsc(String(o.total))}</td><td>${funilEsc(String(o.ganho))}</td><td>${funilEsc(String(o.perdido))}</td><td>${funilEsc(funilPct(o.taxa_conversao || 0))}</td></tr>`)
    .join("");
  document.querySelector("#funil-origem").innerHTML = `<div class="funil-table-scroll"><table class="funil-table"><thead><tr><th>Origem</th><th>Total</th><th>Ganhos</th><th>Perdidos</th><th>Conversão</th></tr></thead><tbody>${linhasOrigem || `<tr><td colspan="5" class="funil-empty">Sem dados.</td></tr>`}</tbody></table></div>`;
}

funilApi("/v1/admin/leads-dashboard")
  .then(funilRender)
  .catch(() => { const s = document.querySelector("#overview-funil"); if (s) s.hidden = true; });

// Item 50 da auditoria completa do CRM (06/09/2026): metas mensais de leads
// ganhos e valor faturado por operador. Não existe registro de "meta de
// equipe" -- a linha "Equipe" é a soma das metas/resultados individuais,
// já calculada pelo backend.
function metasPeriodoAtual() {
  const agora = new Date();
  return `${agora.getFullYear()}-${String(agora.getMonth() + 1).padStart(2, "0")}`;
}

function metasLinha(item, editavel) {
  const progresso = item.meta_leads_ganhos
    ? Math.min(100, Math.round((item.leads_ganhos / item.meta_leads_ganhos) * 100))
    : null;
  const progressoValor = item.meta_valor_faturado > 0
    ? Math.min(100, Math.round((Number(item.valor_faturado) / Number(item.meta_valor_faturado)) * 100))
    : null;
  const metaLeadsCell = editavel
    ? `<input type="number" min="0" class="meta-input" data-campo="meta_leads_ganhos" value="${item.meta_leads_ganhos}">`
    : funilEsc(String(item.meta_leads_ganhos));
  const metaValorCell = editavel
    ? `<input type="number" min="0" step="0.01" class="meta-input" data-campo="meta_valor_faturado" value="${item.meta_valor_faturado}">`
    : funilMoeda(item.meta_valor_faturado);
  return `<tr${item.operador_id ? ` data-operador-id="${item.operador_id}"` : ""}>
    <td><strong>${funilEsc(item.nome || "Equipe")}</strong></td>
    <td>${metaLeadsCell}</td>
    <td>${funilEsc(String(item.leads_ganhos))}${progresso !== null ? ` <small>(${progresso}%)</small>` : ""}</td>
    <td>${metaValorCell}</td>
    <td>${funilMoeda(item.valor_faturado)}${progressoValor !== null ? ` <small>(${progressoValor}%)</small>` : ""}</td>
    ${editavel ? `<td><button type="button" class="secondary-button salvar-meta">Salvar</button></td>` : "<td></td>"}
  </tr>`;
}

function renderMetas(dados) {
  const linhas = [metasLinha(dados.equipe, false), ...dados.operadores.map(item => metasLinha(item, true))].join("");
  document.querySelector("#funil-metas").innerHTML = `<div class="funil-table-scroll"><table class="funil-table"><thead><tr><th>Operador</th><th>Meta leads</th><th>Leads ganhos</th><th>Meta R$</th><th>Faturado</th><th></th></tr></thead><tbody>${linhas}</tbody></table></div>`;
  document.querySelectorAll("#funil-metas .salvar-meta").forEach(botao => botao.addEventListener("click", async () => {
    const linha = botao.closest("tr");
    const operadorId = linha.dataset.operadorId;
    const metaLeads = linha.querySelector('[data-campo="meta_leads_ganhos"]').value || 0;
    const metaValor = linha.querySelector('[data-campo="meta_valor_faturado"]').value || 0;
    const status = document.querySelector("#metas-message");
    botao.disabled = true;
    try {
      const r = await fetch(`/v1/admin/crm/metas/${operadorId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          periodo: document.querySelector("#metas-periodo").value,
          meta_leads_ganhos: Number(metaLeads),
          meta_valor_faturado: Number(metaValor),
        }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Não foi possível salvar a meta.");
      status.hidden = false; status.className = "status-message success"; status.textContent = "Meta salva.";
      carregarMetas();
    } catch (error) {
      status.hidden = false; status.className = "status-message error"; status.textContent = error.message;
    } finally {
      botao.disabled = false;
    }
  }));
}

function carregarMetas() {
  const periodo = document.querySelector("#metas-periodo").value || metasPeriodoAtual();
  funilApi(`/v1/admin/crm/metas?periodo=${encodeURIComponent(periodo)}`)
    .then(renderMetas)
    .catch(() => { document.querySelector("#funil-metas").innerHTML = `<p class="funil-empty">Não foi possível carregar as metas.</p>`; });
}

const metasPeriodoInput = document.querySelector("#metas-periodo");
if (metasPeriodoInput) {
  metasPeriodoInput.value = metasPeriodoAtual();
  metasPeriodoInput.addEventListener("change", carregarMetas);
  carregarMetas();
}

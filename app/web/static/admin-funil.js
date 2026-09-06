async function funilApi(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Falha ao carregar (${r.status})`);
  return r.json();
}
function funilEsc(v) { const s = document.createElement("span"); s.textContent = v ?? ""; return s.innerHTML; }
function funilMsg(text) { const m = document.querySelector("#funil-message"); m.hidden = false; m.textContent = text; m.className = "status-message error"; }
function funilPct(x) { return (x * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%"; }
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

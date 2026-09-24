const form = document.querySelector("#figurativa-form");
const statusEl = document.querySelector("#figurativa-status");
const resultado = document.querySelector("#figurativa-resultado");
const lista = document.querySelector("#figurativa-lista");
const resumo = document.querySelector("#fig-resumo");
const imagemInput = document.querySelector("#imagem-referencia");
const imagemStatus = document.querySelector("#imagem-status");

function setStatus(texto, tipo) {
  statusEl.hidden = false;
  statusEl.textContent = texto;
  statusEl.className = `status-message ${tipo}`;
}

async function api(url, options = {}) {
  // Achado da Fase 14.6 (auditoria fina da busca figurativa, 23/09/2026):
  // o Content-Type era sempre forçado pra application/json, mesmo em
  // requisições sem corpo (como o GET de busca). Isso funcionava só porque
  // o upload de imagem evita este helper (usa fetch direto) -- se algum
  // POST com FormData um dia reusar api(), o header fixo sobrescreveria o
  // boundary do multipart e quebraria silenciosamente. Só força JSON
  // quando há corpo e ele não é FormData.
  const corpoJson = options.body !== undefined && !(options.body instanceof FormData);
  options.headers = corpoJson
    ? { "Content-Type": "application/json", ...(options.headers || {}) }
    : { ...(options.headers || {}) };
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detalhe = Array.isArray(payload.detail)
      ? payload.detail.map(item => item.msg).join("; ")
      : payload.detail || `Falha na operação (${response.status})`;
    throw new Error(detalhe);
  }
  return payload;
}

function celula(texto) {
  const td = document.createElement("td");
  td.textContent = texto;
  return td;
}

function formatarData(iso) {
  if (!iso) return "—";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? "—" : data.toLocaleDateString("pt-BR");
}

function linha(item) {
  const tr = document.createElement("tr");

  tr.append(celula(item.titulo || "—"));

  const processo = document.createElement("td");
  const link = document.createElement("a");
  link.href = item.url_detalhe;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = item.numero;
  processo.append(link);
  tr.append(processo);

  tr.append(celula(item.apresentacao || "—"));
  tr.append(celula(formatarData(item.data_deposito)));

  const comum = document.createElement("td");
  const badge = document.createElement("strong");
  badge.textContent = String(item.codigos_em_comum);
  comum.append(badge);
  tr.append(comum);

  const codigos = document.createElement("td");
  codigos.textContent = (item.codigos_viena || []).join(", ") || "—";
  tr.append(codigos);

  return tr;
}

const botaoBuscar = form.querySelector(".form-submit");

form.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(form));
  const codigos = dados.codigos.trim();
  if (!codigos) return;
  // Achado da Fase 14.4 (auditoria fina, 23/09/2026): nada impedia um
  // clique duplo rápido no botão de disparar duas buscas em paralelo --
  // desabilita o botão enquanto a busca está em andamento.
  if (botaoBuscar) botaoBuscar.disabled = true;
  setStatus("Buscando anterioridades figurativas…", "loading");
  resultado.hidden = true;
  try {
    const params = new URLSearchParams({ codigos, limite: dados.limite });
    if (dados.apresentacao) params.set("apresentacao", dados.apresentacao);
    const resposta = await api(`/v1/admin/figurativa/anterioridades?${params.toString()}`);
    lista.replaceChildren(...resposta.anterioridades.map(linha));
    if (!resposta.anterioridades.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.textContent = "Nenhuma marca com esses elementos figurativos foi encontrada.";
      tr.append(td);
      lista.replaceChildren(tr);
    }
    // Achado da Fase 14.4 (auditoria fina, 23/09/2026): "total" era
    // len(resultados) DEPOIS do limite aplicado -- o operador podia achar
    // que "50 resultados" era o total real quando existiam muito mais
    // anterioridades na base. Agora o backend devolve o total real
    // (resposta.total) separado do que foi de fato retornado
    // (resposta.retornados), e a tela deixa isso explícito quando são
    // diferentes.
    const resumoTexto = resposta.retornados < resposta.total
      ? `Mostrando ${resposta.retornados} de ${resposta.total} anterioridade(s) para ${resposta.codigos.join(", ")} -- aumente o limite pra ver mais.`
      : `${resposta.total} anterioridade(s) para ${resposta.codigos.join(", ")}`;
    resumo.textContent = resumoTexto;
    resultado.hidden = false;
    setStatus(`Busca concluída — ${resposta.total} resultado(s).`, "success");
    resultado.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    if (botaoBuscar) botaoBuscar.disabled = false;
  }
});

document.querySelector("#validar-imagem")?.addEventListener("click", async () => {
  const arquivo = imagemInput?.files?.[0];
  if (!arquivo) {
    imagemStatus.textContent = "Selecione uma imagem antes de validar.";
    return;
  }
  imagemStatus.textContent = "Validando imagem…";
  const dados = new FormData();
  dados.append("arquivo", arquivo);
  try {
    const resposta = await fetch("/v1/admin/figurativa/validar-imagem", { method: "POST", body: dados });
    const payload = await resposta.json().catch(() => ({}));
    if (!resposta.ok) throw new Error(payload.detail || "Não foi possível validar a imagem.");
    // Achado da Fase 14.2 (auditoria fina, 23/09/2026): a tela nunca exibia
    // o texto do OCR nem avisava que não existe pontuação de similaridade
    // ainda -- o operador não tinha como saber que o upload não compara
    // com nada de verdade.
    // Achado P2 do Codex no PR #127: o "aviso" genérico só dizia que não
    // há decisão jurídica automática, sem deixar claro que não existe
    // NENHUMA comparação com acervo/pontuação de similaridade ainda --
    // o operador podia interpretar a validação como uma comparação real.
    const textoOcr = payload.ocr?.status === "concluido" && payload.ocr.texto
      ? ` Texto identificado por OCR: "${payload.ocr.texto}".`
      : "";
    const semScore = payload.score_visual?.disponivel === false ? ` ${payload.score_visual.motivo}` : "";
    imagemStatus.textContent = `Imagem válida (${payload.pixels} pixels de assinatura).${textoOcr}${semScore} ${payload.aviso}`;
  } catch (error) {
    imagemStatus.textContent = error.message;
  }
});

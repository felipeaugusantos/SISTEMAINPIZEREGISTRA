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
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
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

form.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(form));
  const codigos = dados.codigos.trim();
  if (!codigos) return;
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
    resumo.textContent = `${resposta.total} anterioridade(s) para ${resposta.codigos.join(", ")}`;
    resultado.hidden = false;
    setStatus(`Busca concluída — ${resposta.total} resultado(s).`, "success");
    resultado.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(error.message, "error");
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
    const textoOcr = payload.ocr?.status === "concluido" && payload.ocr.texto
      ? ` Texto identificado por OCR: "${payload.ocr.texto}".`
      : "";
    imagemStatus.textContent = `Imagem válida (${payload.pixels} pixels de assinatura).${textoOcr} ${payload.aviso}`;
  } catch (error) {
    imagemStatus.textContent = error.message;
  }
});

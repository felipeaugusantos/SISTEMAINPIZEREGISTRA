const form = document.querySelector("#figurativa-form");
const statusEl = document.querySelector("#figurativa-status");
const resultado = document.querySelector("#figurativa-resultado");
const lista = document.querySelector("#figurativa-lista");
const resumo = document.querySelector("#fig-resumo");

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

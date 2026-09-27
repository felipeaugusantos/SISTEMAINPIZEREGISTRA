const msg = document.querySelector("#nfse-message");
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
let canManage = false;
let searchTimer;

function esc(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function show(text, kind = "success") {
  msg.hidden = false;
  msg.textContent = text;
  msg.className = `status-message ${kind}`;
}

async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Operação não concluída (${response.status})`);
  return data;
}

async function load() {
  const data = await api("/v1/admin/financeiro/nfse");
  document.querySelector("#nfse-count").textContent = `${data.itens.length} registro${data.itens.length === 1 ? "" : "s"}`;
  const tbody = document.querySelector("#nfse-tbody");
  tbody.innerHTML = data.itens.length
    ? data.itens.map(n => `<tr><td>${esc(n.numero || "—")}</td><td><strong>${esc(n.cliente || "Cliente não informado")}</strong><small class="nfse-entry-description">${esc(n.descricao_lancamento || "Lançamento financeiro")} · referência interna #${n.lancamento_id}</small></td><td class="num">${money.format(n.valor)}</td><td><span class="finance-badge ${esc(n.status)}">${esc(n.status)}</span></td><td>${n.emitida_em ? new Date(n.emitida_em).toLocaleString("pt-BR") : "—"}</td><td>${n.status === "emitida" && canManage ? `<button class="mini-button" data-cancelar="${n.id}">Cancelar</button>` : ""}</td></tr>`).join("")
    : '<tr><td colspan="6">Nenhuma NFS-e emitida ainda.</td></tr>';
}

async function pesquisarClientes(termo) {
  const resultados = document.querySelector("#nfse-client-results");
  const valor = termo.trim();
  if (valor !== "/" && valor.length < 2) {
    resultados.hidden = true;
    resultados.replaceChildren();
    return;
  }
  resultados.hidden = false;
  resultados.innerHTML = '<p class="company-search-empty">Pesquisando clientes…</p>';
  try {
    const params = new URLSearchParams({ tipo: "receber", busca: valor });
    const data = await api(`/v1/admin/financeiro/empresas/pesquisar?${params}`);
    resultados.innerHTML = data.itens.length
      ? data.itens.map(item => `<button type="button" class="company-search-option" role="option" data-client-id="${item.id}"><strong>${esc(item.nome)}</strong></button>`).join("")
        + (data.tem_mais ? `<p class="company-search-empty">Mostrando ${data.itens.length} de ${data.total}. Refine a pesquisa para localizar o restante.</p>` : "")
      : '<p class="company-search-empty">Nenhum cliente encontrado.</p>';
  } catch (error) {
    resultados.innerHTML = `<p class="company-search-empty error">${esc(error.message)}</p>`;
  }
}

async function selecionarCliente(empresaId, nome) {
  const titulo = document.querySelector("#nfse-launch-select");
  const label = document.querySelector("#nfse-selected-client");
  const resultados = document.querySelector("#nfse-client-results");
  titulo.disabled = true;
  titulo.innerHTML = '<option value="">Carregando títulos…</option>';
  label.textContent = `Cliente selecionado: ${nome}`;
  resultados.hidden = true;
  try {
    const data = await api(`/v1/admin/financeiro/nfse/lancamentos-disponiveis?empresa_id=${encodeURIComponent(empresaId)}`);
    titulo.innerHTML = '<option value="">Selecione o título a receber</option>' + data.itens.map(item =>
      `<option value="${item.id}">${esc(item.competencia)} · ${esc(item.descricao)} · ${money.format(Number(item.valor))} · #${item.id}</option>`
    ).join("");
    titulo.disabled = data.itens.length === 0;
    if (!data.itens.length) label.textContent = `Cliente ${nome}: nenhum título disponível para emissão.`;
  } catch (error) {
    titulo.innerHTML = '<option value="">Não foi possível carregar os títulos</option>';
    titulo.disabled = true;
    show(error.message, "error");
  }
}

document.querySelector("#new-nota").addEventListener("click", () => {
  document.querySelector("#nota-form").reset();
  document.querySelector("#nfse-client-results").hidden = true;
  document.querySelector("#nfse-client-results").replaceChildren();
  document.querySelector("#nfse-selected-client").textContent = "Selecione primeiro o cliente; depois escolha o título a receber.";
  const titulo = document.querySelector("#nfse-launch-select");
  titulo.disabled = true;
  titulo.innerHTML = '<option value="">Pesquise e selecione um cliente</option>';
  document.querySelector("#nota-dialog").showModal();
});

document.querySelector("#nfse-client-search").addEventListener("input", event => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => pesquisarClientes(event.currentTarget.value), 250);
});

document.querySelector("#nfse-client-results").addEventListener("click", event => {
  const option = event.target.closest("[data-client-id]");
  if (option) selecionarCliente(option.dataset.clientId, option.querySelector("strong").textContent);
});

document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => document.getElementById(button.dataset.close).close()));

document.querySelector("#nota-form").addEventListener("submit", async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try {
    await api("/v1/admin/financeiro/nfse/emitir", { method: "POST", body: JSON.stringify({ lancamento_id: Number(data.lancamento_id), adaptador: "sandbox" }) });
    document.querySelector("#nota-dialog").close();
    show("NFS-e emitida (adaptador sandbox).");
    await load();
  } catch (error) {
    show(error.message, "error");
  }
});

document.querySelector("#nfse-tbody").addEventListener("click", async event => {
  const cancelar = event.target.closest("[data-cancelar]");
  if (!cancelar) return;
  try {
    await api(`/v1/admin/financeiro/nfse/${cancelar.dataset.cancelar}/cancelar`, { method: "POST" });
    show("NFS-e cancelada.");
    await load();
  } catch (error) {
    show(error.message, "error");
  }
});

api("/v1/auth/me").then(user => {
  const permissions = user.permissoes || [];
  canManage = user.superadmin || user.perfil === "administrador" || permissions.includes("finance.manage");
  document.querySelector("#new-nota").hidden = !canManage;
  return load();
}).catch(error => show(error.message, "error"));

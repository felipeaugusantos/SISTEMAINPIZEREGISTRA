const form = document.querySelector("#proposal-plans-form");
const message = document.querySelector("#proposal-plans-message");
const submit = document.querySelector("#save-proposal-plans");
const escapeHtml = value => { const node = document.createElement("span"); node.textContent = value ?? ""; return node.innerHTML; };
const api = async (method = "GET", body) => {
  const response = await fetch("/v1/admin/configuracao/propostas/planos-contabeis", {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Não foi possível carregar/salvar os padrões contábeis.");
  return data;
};
const options = (itens, selecionado) => '<option value="">Selecione uma conta de receita</option>' + itens.map(item =>
  `<option value="${item.id}"${Number(item.id) === Number(selecionado) ? " selected" : ""}>${escapeHtml(item.codigo)} · ${escapeHtml(item.nome)}</option>`
).join("");

async function carregar() {
  try {
    const data = await api();
    for (const field of ["conta_contabil_honorarios_id", "conta_contabil_taxa_gru_id"]) {
      form.elements[field].innerHTML = options(data.itens || [], data[field]);
    }
    submit.disabled = !(data.itens || []).length;
    message.className = "status-message";
    message.textContent = data.itens?.length ? "Confira os padrões atuais ou escolha as contas e salve." : "Nenhuma conta de receita ativa foi encontrada. Cadastre ou ative contas no Plano de contas & DRE antes de configurar propostas.";
  } catch (error) {
    message.className = "status-message error";
    message.textContent = error.message;
    submit.disabled = true;
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(form));
  dados.conta_contabil_honorarios_id = Number(dados.conta_contabil_honorarios_id);
  dados.conta_contabil_taxa_gru_id = Number(dados.conta_contabil_taxa_gru_id);
  submit.disabled = true;
  try {
    await api("PUT", dados);
    message.className = "status-message success";
    message.textContent = "Padrões contábeis salvos. As próximas propostas usarão essas contas automaticamente.";
  } catch (error) {
    message.className = "status-message error";
    message.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

carregar();

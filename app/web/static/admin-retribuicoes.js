const retState = { items: [], canManage: false };
const retMessage = document.querySelector("#retribuicao-message");
const retDialog = document.querySelector("#retribuicao-dialog");
const retForm = document.querySelector("#retribuicao-form");
const FASE_LABELS = {
  relatorio_enviado: "Relatório enviado",
  proposta_enviada: "Proposta enviada",
  proposta_aceita: "Proposta aceita",
  pagamento_realizado: "Pagamento",
  protocolo_inpi: "Protocolo INPI",
  processo_inpi: "Processo no INPI",
};

function escapeRet(value) { const s = document.createElement("span"); s.textContent = value ?? ""; return s.innerHTML; }
function money(v) { return v == null || v === "" ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }); }
async function retApi(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Operação não concluída (${response.status})`);
  return data;
}
function showRetMessage(text, kind = "success") { retMessage.hidden = false; retMessage.textContent = text; retMessage.className = `status-message ${kind}`; }

function renderRet() {
  document.querySelector("#retribuicao-count").textContent = `${retState.items.length} serviço${retState.items.length === 1 ? "" : "s"}`;
  const pendentes = retState.items.filter(x => !x.confirmado).length;
  document.querySelector("#retribuicao-alert").hidden = pendentes === 0;
  const target = document.querySelector("#retribuicao-list");
  target.innerHTML = retState.items.length ? retState.items.map(item => {
    const fase = item.fase_sugerida ? `<br><small class="ret-fase">${escapeRet(FASE_LABELS[item.fase_sugerida] || item.fase_sugerida)}</small>` : "";
    const situacao = !item.ativo
      ? `<span class="finance-status cancelado">Inativo</span>`
      : item.confirmado
        ? `<span class="finance-status pago">Confirmado</span>`
        : `<span class="finance-status pendente">A confirmar</span>`;
    return `<tr class="${item.ativo ? "" : "ret-inactive"}">
      <td><strong>${escapeRet(item.descricao)}</strong><br><small class="ret-key">${escapeRet(item.servico)}</small>${fase}</td>
      <td>${escapeRet(item.codigo || "—")}</td>
      <td>${money(item.valor_normal)}</td>
      <td>${money(item.valor_reduzido)}</td>
      <td>${situacao}</td>
      <td class="ret-actions">${retState.canManage ? `<button class="secondary-button edit-retribuicao" type="button" data-id="${item.id}">Editar</button>` : ""}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6"><div class="finance-empty">Nenhum serviço cadastrado.</div></td></tr>`;
}

async function loadRet() { const data = await retApi("/v1/admin/financeiro/retribuicoes"); retState.items = data.itens; renderRet(); }

function openRet(item = null) {
  retForm.reset();
  retForm.elements.id.value = item?.id || "";
  retForm.elements.servico.value = item?.servico || "";
  retForm.elements.servico.readOnly = !!item;
  retForm.elements.descricao.value = item?.descricao || "";
  retForm.elements.codigo.value = item?.codigo || "";
  retForm.elements.fase_sugerida.value = item?.fase_sugerida || "";
  retForm.elements.valor_normal.value = item?.valor_normal ?? "";
  retForm.elements.valor_reduzido.value = item?.valor_reduzido ?? "";
  retForm.elements.observacoes.value = item?.observacoes || "";
  retForm.elements.ativo.checked = item ? item.ativo : true;
  document.querySelector("#retribuicao-title").textContent = item ? "Editar serviço" : "Novo serviço";
  retDialog.showModal();
}

document.querySelector("#new-retribuicao").addEventListener("click", () => openRet());
document.querySelector("#retribuicao-list").addEventListener("click", event => {
  const button = event.target.closest("[data-id]");
  if (button) openRet(retState.items.find(x => String(x.id) === button.dataset.id));
});
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => document.getElementById(button.dataset.close).close()));

retForm.addEventListener("submit", async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(retForm));
  const id = data.id;
  delete data.id;
  data.ativo = retForm.elements.ativo.checked;
  data.valor_normal = data.valor_normal === "" ? null : Number(data.valor_normal);
  data.valor_reduzido = data.valor_reduzido === "" ? null : Number(data.valor_reduzido);
  data.codigo = data.codigo || null;
  data.fase_sugerida = data.fase_sugerida || null;
  data.observacoes = data.observacoes || null;
  if (id) delete data.servico;
  try {
    await retApi(id ? `/v1/admin/financeiro/retribuicoes/${id}` : "/v1/admin/financeiro/retribuicoes", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
    retDialog.close();
    showRetMessage(id ? "Serviço atualizado." : "Serviço criado.");
    await loadRet();
  } catch (error) { showRetMessage(error.message, "error"); }
});

Promise.all([retApi("/v1/auth/me"), loadRet()]).then(([user]) => {
  const permissions = user.permissoes || [];
  retState.canManage = user.superadmin || user.perfil === "administrador" || permissions.includes("finance.manage");
  document.querySelector("#new-retribuicao").hidden = !retState.canManage;
  renderRet();
}).catch(error => showRetMessage(error.message, "error"));

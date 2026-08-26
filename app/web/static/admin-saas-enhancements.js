const orgList = document.querySelector("#org-list");
const editDialog = document.querySelector("#edit-org-dialog");
const editForm = document.querySelector("#edit-org-form");
const editMessage = document.querySelector("#edit-org-form-message");

function addEditButtons() {
  document.querySelectorAll("#org-list article.user-row").forEach(card => {
    if (card.querySelector("[data-edit-cadastro]")) return;
    const id = card.querySelector("[data-key]")?.dataset.key;
    if (!id) return;
    const button = document.createElement("button");
    button.className = "secondary-button";
    button.dataset.editCadastro = id;
    button.textContent = "Editar cadastro";
    card.lastElementChild?.prepend(button);
  });
}

new MutationObserver(addEditButtons).observe(orgList, { childList: true });
addEditButtons();
orgList.addEventListener("click", async event => {
  const button = event.target.closest("[data-edit-cadastro]");
  if (!button) return;
  const org = state.organizacoes.find(item => String(item.id) === button.dataset.editCadastro);
  if (!org) return;
  const branding = org.branding || {};
  editForm.reset();
  if (!editForm.querySelector(".module-access-fieldset")) editForm.insertAdjacentHTML("beforeend", `<fieldset class="module-access-fieldset"><legend>Módulos liberados para esta empresa</legend>${[["leads","Leads"],["crm","CRM"],["processos_monitorados","Processos monitorados"],["operacao_juridica","Operação jurídica"],["financeiro","Financeiro"],["consulta","Consulta RPI"]].map(([value,label]) => `<label><input type="checkbox" name="modulos_liberados" value="${value}">${label}</label>`).join("")}</fieldset>`);
  editForm.querySelectorAll("[name='modulos_liberados']").forEach(input => { input.checked = (org.modulos_liberados || []).includes(input.value); });
  editForm.elements.id.value = org.id;
  editForm.elements.nome.value = org.nome || "";
  editForm.elements.documento.value = org.documento || branding.cnpj || "";
  editForm.elements.email_contato.value = org.email_contato || branding.email || "";
  editForm.elements.telefone_contato.value = org.telefone_contato || branding.telefone || "";
  editForm.elements.endereco.value = branding.endereco || "";
  editForm.elements.site.value = branding.site || "";
  editForm.elements.atividade.value = branding.atividade || "";
  editForm.elements.fundacao.value = branding.fundacao || "";
  editForm.elements.logo_url.value = branding.logo_url || "";
  editMessage.hidden = true;
  editDialog.showModal();
});

document.querySelectorAll("[data-edit-close]").forEach(button => button.addEventListener("click", () => editDialog.close()));
editForm.addEventListener("submit", async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(editForm));
  const org = state.organizacoes.find(item => String(item.id) === data.id);
  const branding = org?.branding || {};
  try {
    await api(`/v1/admin/saas/organizacoes/${data.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        nome: data.nome.trim(),
        documento: data.documento.trim() || null,
        email_contato: data.email_contato.trim(),
        telefone_contato: data.telefone_contato.trim() || null,
        branding: { ...branding, nome_exibido: data.nome.trim(), cnpj: data.documento.trim() || null, endereco: data.endereco.trim() || null, telefone: data.telefone_contato.trim() || null, email: data.email_contato.trim(), site: data.site.trim() || null, atividade: data.atividade.trim() || null, fundacao: data.fundacao.trim() || null, logo_url: data.logo_url.trim() || null },
        modulos_liberados: [...editForm.querySelectorAll("[name='modulos_liberados']:checked")].map(input => input.value),
      }),
    });
    editDialog.close();
    await load();
  } catch (error) {
    editMessage.hidden = false;
    editMessage.className = "status-message error";
    editMessage.textContent = error.message;
  }
});

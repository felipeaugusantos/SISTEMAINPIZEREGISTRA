const orgList = document.querySelector("#org-list");

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
  const endereco = prompt("Endereço completo:", branding.endereco || "");
  if (endereco === null) return;
  const telefone = prompt("Telefone:", branding.telefone || org.telefone_contato || "");
  if (telefone === null) return;
  const email = prompt("E-mail:", branding.email || org.email_contato || "");
  if (email === null) return;
  const site = prompt("Site:", branding.site || "");
  if (site === null) return;
  try {
    await api(`/v1/admin/saas/organizacoes/${org.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        nome: org.nome,
        documento: org.documento,
        email_contato: email,
        telefone_contato: telefone,
        branding: { ...branding, nome_exibido: org.nome, cnpj: org.documento, endereco, telefone, email, site },
      }),
    });
    await load();
    alert("Cadastro atualizado.");
  } catch (error) { alert(error.message); }
});

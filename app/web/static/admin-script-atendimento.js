let scripts = [];
const message = document.querySelector("#script-config-message");
const dialog = document.querySelector("#script-dialog");
const form = document.querySelector("#script-form");
const escSA = value => { const node = document.createElement("span"); node.textContent = value ?? ""; return node.innerHTML; };

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(" · ") : data.detail;
    throw new Error(detail || `Não foi possível concluir (${response.status})`);
  }
  return data;
}

function showMessage(text, kind = "") {
  message.className = `status-message ${kind}`.trim();
  message.textContent = text;
}

function renderScripts() {
  const box = document.querySelector("#script-list");
  box.innerHTML = scripts.length
    ? scripts.map(item => `<article class="script-atendimento-card" data-id="${escSA(item.id)}"><header><h3>${escSA(item.titulo)}</h3><div class="script-atendimento-actions"><button class="script-edit secondary-button" type="button" data-id="${escSA(item.id)}">Editar</button><button class="script-del secondary-button" type="button" data-id="${escSA(item.id)}">Excluir</button></div></header><pre>${escSA(item.corpo)}</pre></article>`).join("")
    : `<p class="script-atendimento-empty">Nenhum script cadastrado.</p>`;
}

async function carregar() {
  try {
    const data = await api("/v1/admin/configuracao/scripts-atendimento");
    scripts = data.itens || [];
    renderScripts();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

function abrirDialog(item = null) {
  form.reset();
  form.elements.id.value = item?.id || "";
  form.elements.titulo.value = item?.titulo || "";
  form.elements.corpo.value = item?.corpo || "";
  document.querySelector("#script-dialog-title").textContent = item ? "Editar script" : "Novo script";
  dialog.showModal();
}

document.querySelector("#new-script").addEventListener("click", () => abrirDialog());
document.querySelector("#cancel-script").addEventListener("click", () => dialog.close());
document.querySelector("#script-list").addEventListener("click", event => {
  const edit = event.target.closest(".script-edit");
  const del = event.target.closest(".script-del");
  if (edit) abrirDialog(scripts.find(item => String(item.id) === edit.dataset.id));
  if (del && confirm("Excluir este script?")) {
    api(`/v1/admin/configuracao/scripts-atendimento/${del.dataset.id}`, { method: "DELETE" })
      .then(data => { scripts = data.itens || []; renderScripts(); showMessage("Script excluído.", "success"); })
      .catch(error => showMessage(error.message, "error"));
  }
});
form.addEventListener("submit", async () => {
  const id = form.elements.id.value;
  const payload = { titulo: form.elements.titulo.value.trim(), corpo: form.elements.corpo.value.trim() };
  try {
    const data = await api(id ? `/v1/admin/configuracao/scripts-atendimento/${id}` : "/v1/admin/configuracao/scripts-atendimento", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    scripts = data.itens || [];
    renderScripts();
    dialog.close();
    showMessage("Script salvo.", "success");
  } catch (error) {
    showMessage(error.message, "error");
  }
});

let campoSelecionado = null;
const inserirCampo = (token, destino = null) => {
  const target = destino || campoSelecionado || document.querySelector("#script-corpo:focus");
  if (!target || !token) return;
  const inicio = target.selectionStart ?? target.value.length;
  const fim = target.selectionEnd ?? inicio;
  target.value = `${target.value.slice(0, inicio)}${token}${target.value.slice(fim)}`;
  target.focus();
  target.selectionStart = target.selectionEnd = inicio + token.length;
};
document.querySelector("#script-corpo").addEventListener("focus", () => { campoSelecionado = document.querySelector("#script-corpo"); });
document.querySelectorAll("#script-field-palette [data-token]").forEach(button => {
  button.addEventListener("dragstart", event => {
    if (event.dataTransfer) { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/plain", button.dataset.token || ""); }
    button.classList.add("dragging");
  });
  button.addEventListener("dragend", () => button.classList.remove("dragging"));
  button.addEventListener("click", () => inserirCampo(button.dataset.token));
});
document.querySelector("#script-corpo").addEventListener("dragover", event => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = "copy"; });
document.querySelector("#script-corpo").addEventListener("drop", event => {
  event.preventDefault();
  inserirCampo(event.dataTransfer?.getData("text/plain") || "", document.querySelector("#script-corpo"));
});

carregar();

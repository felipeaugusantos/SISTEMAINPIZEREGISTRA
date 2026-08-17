let cadencias = [];
async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(" · ") : data.detail;
    throw new Error(detail || `Não foi possível concluir (${response.status})`);
  }
  return data;
}
const cadenciaDialog = document.querySelector("#cadencia-dialog");
const cadenciaForm = document.querySelector("#cadencia-form");
const canais = { email: "E-mail", whatsapp: "WhatsApp", ligacao: "Ligação", reuniao: "Reunião", outro: "Outro" };
const escCad = value => { const node = document.createElement("span"); node.textContent = value ?? ""; return node.innerHTML; };

function passoRow(p = {}) {
  const options = Object.entries(canais).map(([value, label]) => `<option value="${value}" ${p.canal === value ? "selected" : ""}>${label}</option>`).join("");
  const row = document.createElement("div");
  row.className = "crm-cad-passo";
  row.innerHTML = `<label>Dia <input type="number" class="passo-dia" min="0" max="365" value="${escCad(String(p.dia ?? 0))}"></label><label>Canal <select class="passo-canal">${options}</select></label><label class="passo-titulo-field">Tarefa <input class="passo-titulo" maxlength="180" value="${escCad(p.titulo || "")}" placeholder="Ex.: Enviar e-mail de follow-up"></label><button type="button" class="passo-del" aria-label="Remover passo">×</button>`;
  return row;
}

function renderCadencias() {
  const box = document.querySelector("#crm-cadencias");
  box.innerHTML = cadencias.length ? cadencias.map(item => `<div class="crm-cadencia" data-id="${item.id}"><div><strong>${escCad(item.nome)}</strong>${item.ativo ? "" : " <span class=\"crm-cad-inativa\">inativa</span>"}<br><small>${item.passos.length} passo(s)${item.passos.length ? " · " + escCad(item.passos.map(p => `dia ${p.dia} ${canais[p.canal] || p.canal}`).join(", ")) : ""}</small></div><div class="crm-cad-acts"><button class="cad-edit secondary-button" data-id="${item.id}" type="button">Editar</button><button class="cad-del secondary-button" data-id="${item.id}" type="button" aria-label="Excluir">×</button></div></div>`).join("") : `<p class="crm-cad-empty">Nenhuma sequência criada.</p>`;
}

async function loadCadencias() {
  const data = await api("/v1/admin/crm/cadencias");
  cadencias = data.itens || [];
  renderCadencias();
}

function openCadencia(item = null) {
  cadenciaForm.reset();
  cadenciaForm.elements.id.value = item?.id || "";
  cadenciaForm.elements.nome.value = item?.nome || "";
  cadenciaForm.elements.descricao.value = item?.descricao || "";
  cadenciaForm.elements.ativo.checked = item ? item.ativo : true;
  const box = document.querySelector("#cadencia-passos");
  box.innerHTML = "";
  (item?.passos?.length ? item.passos : [{ dia: 0, canal: "email", titulo: "" }]).forEach(passo => box.appendChild(passoRow(passo)));
  document.querySelector("#cadencia-title").textContent = item ? "Editar sequência" : "Nova sequência";
  cadenciaDialog.showModal();
}

document.querySelector("#new-cadencia").addEventListener("click", () => openCadencia());
document.querySelector("#add-passo").addEventListener("click", () => document.querySelector("#cadencia-passos").appendChild(passoRow()));
document.querySelector("#cadencia-passos").addEventListener("click", event => { const button = event.target.closest(".passo-del"); if (button) button.closest(".crm-cad-passo").remove(); });
document.querySelector("#close-cadencia").addEventListener("click", () => cadenciaDialog.close());
document.querySelector("#cancel-cadencia").addEventListener("click", () => cadenciaDialog.close());
document.querySelector("#crm-cadencias").addEventListener("click", event => {
  const edit = event.target.closest(".cad-edit");
  const del = event.target.closest(".cad-del");
  if (edit) openCadencia(cadencias.find(item => String(item.id) === edit.dataset.id));
  if (del && confirm("Excluir esta sequência?")) api(`/v1/admin/crm/cadencias/${del.dataset.id}`, { method: "DELETE" }).then(loadCadencias);
});
cadenciaForm.addEventListener("submit", async event => {
  event.preventDefault();
  const passos = [...document.querySelectorAll("#cadencia-passos .crm-cad-passo")].map(row => ({ dia: Number(row.querySelector(".passo-dia").value) || 0, canal: row.querySelector(".passo-canal").value, titulo: row.querySelector(".passo-titulo").value.trim() })).filter(passo => passo.titulo);
  const payload = { nome: cadenciaForm.elements.nome.value.trim(), descricao: cadenciaForm.elements.descricao.value || null, ativo: cadenciaForm.elements.ativo.checked, passos };
  const id = cadenciaForm.elements.id.value;
  try { await api(id ? `/v1/admin/crm/cadencias/${id}` : "/v1/admin/crm/cadencias", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) }); cadenciaDialog.close(); await loadCadencias(); } catch (error) { const message = document.querySelector("#cadencia-message"); message.hidden = false; message.className = "status-message error"; message.textContent = error.message; }
});
loadCadencias().catch(() => { document.querySelector("#crm-cadencias").innerHTML = `<p class="status-message error">Não foi possível carregar as sequências.</p>`; });

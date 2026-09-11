const form = document.querySelector("#email-leads-config-form");
const message = document.querySelector("#email-leads-config-message");
const api = async (url, options = {}) => { options.headers = { ...(options.headers || {}), "Content-Type": "application/json" }; const response = await fetch(url, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || "Não foi possível concluir a operação."); return data; };
let defaults = null;
function preencher(data) { Object.entries(data).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value || ""; }); }
async function carregar() {
  try {
    const data = await api("/v1/admin/configuracao/email-leads");
    defaults = data;
    preencher(data);
  } catch (error) { message.className = "status-message error"; message.textContent = error.message; }
}
form.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await api("/v1/admin/configuracao/email-leads", { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    message.className = "status-message success";
    message.textContent = "Modelo salvo. Ele será usado no próximo envio a partir do card do lead.";
  } catch (error) { message.className = "status-message error"; message.textContent = error.message; }
});
document.querySelector("#email-leads-config-reset").addEventListener("click", () => { if (defaults) preencher(defaults); });
let campoSelecionado = null;
const inserirCampo = (token, destino = null) => {
  const target = destino || campoSelecionado || document.querySelector("#email-leads-config-form textarea:focus, #email-leads-config-form input:focus");
  if (!target || !token) return;
  const inicio = target.selectionStart ?? target.value.length;
  const fim = target.selectionEnd ?? inicio;
  target.value = `${target.value.slice(0, inicio)}${token}${target.value.slice(fim)}`;
  target.focus();
  target.selectionStart = target.selectionEnd = inicio + token.length;
  target.dispatchEvent(new Event("input", { bubbles: true }));
};
document.querySelectorAll("#email-leads-config-form textarea, #email-leads-config-form input[name='assunto']").forEach(campo => {
  campo.addEventListener("focus", () => { campoSelecionado = campo; });
  campo.addEventListener("dragenter", event => { event.preventDefault(); campo.classList.add("drop-target-active"); });
  campo.addEventListener("dragover", event => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = "copy"; });
  campo.addEventListener("dragleave", () => campo.classList.remove("drop-target-active"));
  campo.addEventListener("drop", event => {
    event.preventDefault();
    campo.classList.remove("drop-target-active");
    inserirCampo(event.dataTransfer?.getData("text/plain") || "", campo);
  });
});
document.querySelectorAll("#email-leads-field-palette [data-token]").forEach(button => {
  button.addEventListener("dragstart", event => {
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("text/plain", button.dataset.token || "");
    }
    button.classList.add("dragging");
  });
  button.addEventListener("dragend", () => button.classList.remove("dragging"));
  button.addEventListener("click", () => inserirCampo(button.dataset.token));
});
carregar();

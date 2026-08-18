const message = document.querySelector("#clicksign-message");
const status = document.querySelector("#clicksign-status");
const esc = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[c]));
function item(label, value, state = "") { return `<div class="clicksign-status-item"><span>${label}</span><strong class="${state}">${value}</strong></div>`; }
async function load() {
  try {
    const response = await fetch("/v1/admin/configuracao/clicksign");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Não foi possível carregar a configuração.");
    status.innerHTML = item("Status", data.habilitado ? "Habilitado" : "Desabilitado", data.habilitado ? "ok" : "muted") + item("Ambiente", esc(data.ambiente)) + item("Token", data.token_configurado ? "Configurado" : "Não configurado", data.token_configurado ? "ok" : "muted") + item("Webhook", data.webhook_url ? esc(data.webhook_url) : "Não configurado", data.webhook_url ? "ok" : "muted") + item("Segredo do webhook", data.webhook_segredo_configurado ? "Configurado" : "Não configurado", data.webhook_segredo_configurado ? "ok" : "muted");
    const form = document.querySelector("#clicksign-form");
    if (form) { form.elements.ambiente.value = data.ambiente; form.elements.webhook_url.value = data.webhook_url || ""; form.elements.habilitado.checked = data.habilitado; }
    message.textContent = "Configuração carregada."; message.className = "status-message success";
  } catch (error) { message.textContent = error.message; message.className = "status-message error"; }
}
load();
document.querySelector("#clicksign-form")?.addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget; const save = document.querySelector("#clicksign-save-message"); const payload = Object.fromEntries(new FormData(form)); payload.habilitado = form.elements.habilitado.checked; save.textContent = "Salvando…"; try { const response = await fetch("/v1/admin/configuracao/clicksign", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || "Não foi possível salvar."); save.textContent = data.mensagem; await load(); } catch (error) { save.textContent = error.message; } });

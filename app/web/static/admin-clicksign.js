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
    message.textContent = "Configuração carregada."; message.className = "status-message success";
  } catch (error) { message.textContent = error.message; message.className = "status-message error"; }
}
load();

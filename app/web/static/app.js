const form = document.querySelector("#research-form");
const statusMessage = document.querySelector("#form-status");
const submitButton = form.querySelector("button[type='submit']");

// Identifica este site publico como o tenant padrao junto ao backend (mesmo
// mecanismo usado por /static/tenant-branding.js). Sem isso, a pesquisa recebe
// 401 do gate de integracao antes mesmo de tentar resolver a organizacao.
const chaveIntegracaoPromise = fetch("/v1/tenant/branding")
  .then((response) => (response.ok ? response.json() : {}))
  .then((tenant) => tenant.chave_integracao || null)
  .catch(() => null);

function errorMessage(data) {
  if (!Array.isArray(data?.detail)) return data?.detail || "Não foi possível gerar o relatório.";
  return data.detail.map((item) => item.msg.replace(/^Value error, /, "")).join(" ");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusMessage.className = "status-message loading";
  statusMessage.textContent = "Preparando a pesquisa na base de marcas...";
  submitButton.disabled = true;

  const payload = {
    nome: document.querySelector("#contact-name").value.trim(),
    empresa: document.querySelector("#company").value.trim() || null,
    email_corporativo: document.querySelector("#corporate-email").value.trim(),
    telefone: document.querySelector("#corporate-phone").value.trim(),
    marca: document.querySelector("#brand-input").value.trim(),
    atividade: document.querySelector("#business-activity").value.trim(),
    aceite_privacidade: document.querySelector("#privacy-consent").checked,
    aceite_marketing: document.querySelector("#marketing-consent").checked,
    website: document.querySelector("#website").value,
  };

  try {
    const chaveIntegracao = await chaveIntegracaoPromise;
    const headers = { "Content-Type": "application/json" };
    if (chaveIntegracao) headers["X-Integration-Key"] = chaveIntegracao;
    const response = await fetch("/v1/pesquisas-marca", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(data));
    const chaveParam = chaveIntegracao ? `&chave_integracao=${encodeURIComponent(chaveIntegracao)}` : "";
    location.assign(`${data.relatorio_url}?download=1${chaveParam}`);
  } catch (error) {
    statusMessage.className = "status-message error";
    statusMessage.textContent = error.message;
    submitButton.disabled = false;
  }
});

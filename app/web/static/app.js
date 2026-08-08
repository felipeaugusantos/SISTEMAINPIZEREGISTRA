const form = document.querySelector("#research-form");
const statusMessage = document.querySelector("#form-status");
const submitButton = form.querySelector("button[type='submit']");

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
    const response = await fetch("/v1/pesquisas-marca", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(data));
    location.assign(`${data.relatorio_url}?download=1`);
  } catch (error) {
    statusMessage.className = "status-message error";
    statusMessage.textContent = error.message;
    submitButton.disabled = false;
  }
});

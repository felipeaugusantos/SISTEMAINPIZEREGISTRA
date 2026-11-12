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
    // CAPTCHA (Turnstile), quando ativo -- ver captcha-publico.js.
    const headers = { "Content-Type": "application/json", ...((await window.zeCaptcha?.cabecalhos()) || {}) };
    const response = await fetch("/v1/pesquisas-marca", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(data));
    if (!data.relatorio_token) {
      // Resposta uniforme sem token: a pesquisa pode já existir para este
      // e-mail. O acesso anterior exige o código enviado à caixa (prova de
      // posse) -- verificação no painel de recuperação.
      statusMessage.className = "status-message info";
      statusMessage.textContent = "Se esta marca já tiver uma pesquisa neste e-mail, enviamos um código de acesso para ele.";
      abrirRecuperacao(payload.email_corporativo, payload.marca);
      solicitarCodigo();
      return;
    }
    location.assign(`${data.relatorio_url}?download=1#token=${encodeURIComponent(data.relatorio_token)}`);
  } catch (error) {
    statusMessage.className = "status-message error";
    statusMessage.textContent = error.message;
    submitButton.disabled = false;
    // O token do CAPTCHA vale uma vez: nova tentativa precisa de um novo.
    window.zeCaptcha?.reiniciar();
  }
});


// Recuperação de acesso a pesquisas anteriores com prova de posse do e-mail.
const recoveryPanel = document.querySelector("#recovery-panel");
const recoveryRequestForm = document.querySelector("#recovery-request-form");
const recoveryConfirmForm = document.querySelector("#recovery-confirm-form");
const recoveryStatus = document.querySelector("#recovery-status");
const recoveryConfirmStatus = document.querySelector("#recovery-confirm-status");

function abrirRecuperacao(email, marca) {
  if (!recoveryPanel) return;
  recoveryPanel.hidden = false;
  document.querySelector("#recovery-email").value = email || "";
  document.querySelector("#recovery-brand").value = marca || "";
  recoveryPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function solicitarCodigo() {
  const email = document.querySelector("#recovery-email").value.trim();
  const marca = document.querySelector("#recovery-brand").value.trim();
  recoveryStatus.className = "status-message loading";
  recoveryStatus.textContent = "Enviando o código...";
  try {
    const headers = { "Content-Type": "application/json", ...((await window.zeCaptcha?.cabecalhos()) || {}) };
    const response = await fetch("/v1/pesquisas-marca/recuperar-acesso", {
      method: "POST",
      headers,
      body: JSON.stringify({ email_corporativo: email, marca }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(data));
    recoveryStatus.className = "status-message info";
    recoveryStatus.textContent = data.detalhe;
    recoveryConfirmForm.hidden = false;
    document.querySelector("#recovery-code").focus();
  } catch (error) {
    recoveryStatus.className = "status-message error";
    recoveryStatus.textContent = error.message;
    window.zeCaptcha?.reiniciar();
  }
}

recoveryRequestForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await solicitarCodigo();
});

recoveryConfirmForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  recoveryConfirmStatus.className = "status-message loading";
  recoveryConfirmStatus.textContent = "Confirmando o código...";
  try {
    const headers = { "Content-Type": "application/json", ...((await window.zeCaptcha?.cabecalhos()) || {}) };
    const response = await fetch("/v1/pesquisas-marca/recuperar-acesso/confirmar", {
      method: "POST",
      headers,
      body: JSON.stringify({
        email_corporativo: document.querySelector("#recovery-email").value.trim(),
        marca: document.querySelector("#recovery-brand").value.trim(),
        codigo: document.querySelector("#recovery-code").value.trim(),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(data));
    if (!data.relatorio_token) throw new Error("Não foi possível autorizar o relatório.");
    location.assign(`${data.relatorio_url}#token=${encodeURIComponent(data.relatorio_token)}`);
  } catch (error) {
    recoveryConfirmStatus.className = "status-message error";
    recoveryConfirmStatus.textContent = error.message;
    window.zeCaptcha?.reiniciar();
  }
});

document.querySelector("#recovery-open")?.addEventListener("click", () => {
  abrirRecuperacao(
    document.querySelector("#corporate-email")?.value.trim(),
    document.querySelector("#brand-input")?.value.trim(),
  );
});

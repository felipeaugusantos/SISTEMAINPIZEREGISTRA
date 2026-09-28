// Script externo (nao inline): o CSP padrao (app/observability.py) usa
// script-src 'self' e bloqueia <script> embutido no HTML.
const form = document.querySelector("#privacy-delete-form");
const msg = document.querySelector("#privacy-message");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  msg.hidden = false;
  msg.className = "status-message loading";
  msg.textContent = "Enviando...";
  try {
    const email = new FormData(form).get("email");
    const resposta = await fetch("/v1/privacidade/solicitar-exclusao", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const dados = await resposta.json().catch(() => ({}));
    msg.className = resposta.ok ? "status-message success" : "status-message error";
    msg.textContent = resposta.ok
      ? (dados.mensagem || "Se o e-mail existir na nossa base, você vai receber instruções.")
      : "Não foi possível enviar sua solicitação. Tente novamente em instantes.";
  } catch {
    msg.className = "status-message error";
    msg.textContent = "Não foi possível enviar sua solicitação. Tente novamente em instantes.";
  }
});

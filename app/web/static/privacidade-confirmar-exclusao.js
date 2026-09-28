// Script externo (nao inline): o CSP padrao (app/observability.py) usa
// script-src 'self' e bloqueia <script> embutido no HTML.
const msg = document.querySelector("#privacy-message");
const token = new URLSearchParams(location.hash.slice(1)).get("token");
if (!token) {
  msg.className = "status-message error";
  msg.textContent = "Link inválido -- faltou o token de confirmação.";
} else {
  fetch("/v1/privacidade/confirmar-exclusao", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  })
    .then((resposta) => resposta.json())
    .then((dados) => {
      const sucesso = dados.status === "ok";
      msg.className = sucesso ? "status-message success" : "status-message error";
      msg.textContent = dados.mensagem || (sucesso ? "Seus dados foram removidos." : "Não foi possível confirmar.");
    })
    .catch(() => {
      msg.className = "status-message error";
      msg.textContent = "Não foi possível confirmar. Tente novamente em instantes.";
    });
}

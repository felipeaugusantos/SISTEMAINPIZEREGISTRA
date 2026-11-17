const form = document.querySelector("#assistente-form");
const input = document.querySelector("#assistente-input");
const historicoEl = document.querySelector("#assistente-historico");
const message = document.querySelector("#assistente-message");
const historico = [];

function escapeHtml(texto) {
  const node = document.createElement("span");
  node.textContent = texto ?? "";
  return node.innerHTML;
}

function adicionarMensagem(papel, texto) {
  const bloco = document.createElement("div");
  bloco.className = `assistente-msg assistente-msg-${papel}`;
  bloco.innerHTML = `<p>${escapeHtml(texto).replace(/\n/g, "<br>")}</p>`;
  historicoEl.appendChild(bloco);
  historicoEl.scrollTop = historicoEl.scrollHeight;
  return bloco;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const pergunta = input.value.trim();
  if (!pergunta) return;
  message.textContent = "";
  const blocoPergunta = adicionarMensagem("user", pergunta);
  input.value = "";
  input.disabled = true;
  const botao = form.querySelector("button");
  botao.disabled = true;
  const carregando = adicionarMensagem("model", "Consultando…");
  carregando.classList.add("assistente-msg-loading");
  try {
    const response = await fetch("/v1/admin/assistente/perguntar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pergunta, historico }),
    });
    const data = await response.json().catch(() => ({}));
    // R11: tratar rejeição/HTTP/expiração com mensagem acionável.
    if (response.status === 401) throw new Error("Sua sessão expirou. Entre novamente e reenvie a pergunta.");
    if (!response.ok) throw new Error(data.detail || "Não foi possível consultar o assistente.");
    carregando.remove();
    adicionarMensagem("model", data.resposta);
    historico.push({ role: "user", texto: pergunta }, { role: "model", texto: data.resposta });
    if (historico.length > 12) historico.splice(0, historico.length - 12);
  } catch (error) {
    carregando.remove();
    // R11: em falha a pergunta volta ao campo (recuperável) e a bolha
    // otimista sai do histórico, para tentar de novo sem duplicar mensagem.
    blocoPergunta.remove();
    input.value = pergunta;
    message.className = "status-message error";
    message.textContent = error.message || "Sem conexão com o servidor. Verifique sua internet e tente novamente.";
  } finally {
    input.disabled = false;
    botao.disabled = false;
    input.focus();
  }
});

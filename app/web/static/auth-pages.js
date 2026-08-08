function cookie(nome) {
  return document.cookie.split("; ").find((item) => item.startsWith(`${nome}=`))?.split("=").slice(1).join("=") || "";
}
async function enviar(url, dados) {
  const headers = { "Content-Type": "application/json" };
  const csrf = cookie("zr_csrf");
  if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
  const resposta = await fetch(url, { method: "POST", headers, body: JSON.stringify(dados) });
  const payload = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    const mensagem = payload.detail
      || (resposta.status >= 500
        ? `Erro interno (${resposta.status}). Tente novamente em instantes.`
        : `Não foi possível concluir (${resposta.status}).`);
    throw new Error(mensagem);
  }
  return payload;
}
const login = document.querySelector("#login-form");
login?.addEventListener("submit", async (event) => {
  event.preventDefault(); const msg = document.querySelector("#auth-message"); msg.textContent = "Entrando…";
  try { const dados = Object.fromEntries(new FormData(login)); if (!dados.codigo_mfa) delete dados.codigo_mfa; const r = await enviar("/v1/auth/login", dados); location.href = r.destino; }
  catch (erro) { msg.textContent = erro.message; msg.className = "status-message error"; }
});
document.querySelector("#forgot-password")?.addEventListener("click", async () => {
  const email = prompt("Informe o e-mail cadastrado:");
  if (!email) return;
  const msg = document.querySelector("#auth-message");
  try {
    const resposta = await enviar("/v1/auth/recuperacao/solicitar", { email });
    msg.textContent = resposta.mensagem;
    if (resposta.token_teste_local) {
      const nova_senha = prompt("Ambiente local: informe a nova senha (mínimo de 12 caracteres):");
      if (nova_senha) {
        await enviar("/v1/auth/recuperacao/redefinir", {
          token: resposta.token_teste_local,
          nova_senha,
        });
        msg.textContent = "Senha redefinida. Entre com a nova senha.";
      }
    }
    msg.className = "status-message success";
  } catch (erro) { msg.textContent = erro.message; msg.className = "status-message error"; }
});
const senha = document.querySelector("#password-form");
senha?.addEventListener("submit", async (event) => {
  event.preventDefault(); const dados = Object.fromEntries(new FormData(senha)); const msg = document.querySelector("#auth-message");
  if (dados.nova_senha !== dados.confirmacao) { msg.textContent = "As novas senhas não coincidem."; return; }
  try { const r = await enviar("/v1/auth/trocar-senha", { senha_atual: dados.senha_atual, nova_senha: dados.nova_senha }); location.href = r.destino; }
  catch (erro) { msg.textContent = erro.message; msg.className = "status-message error"; }
});

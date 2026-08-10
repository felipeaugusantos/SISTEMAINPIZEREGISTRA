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
const recuperacao = document.querySelector("#forgot-password-form");
recuperacao?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const msg = document.querySelector("#auth-message");
  msg.textContent = "Enviando…";
  msg.className = "status-message loading";
  try {
    const { email } = Object.fromEntries(new FormData(recuperacao));
    const resposta = await enviar("/v1/auth/recuperacao/solicitar", { email });
    msg.textContent = resposta.mensagem;
    if (resposta.token_teste_local) {
      location.href = `/redefinir-senha#token=${encodeURIComponent(resposta.token_teste_local)}`;
      return;
    }
    msg.className = "status-message success";
  } catch (erro) { msg.textContent = erro.message; msg.className = "status-message error"; }
});
const redefinicao = document.querySelector("#reset-password-form");
let tokenRecuperacao = "";
if (redefinicao) {
  const fragmento = new URLSearchParams(location.hash.replace(/^#/, ""));
  tokenRecuperacao = fragmento.get("token") || "";
  history.replaceState(null, "", "/redefinir-senha");
  if (!tokenRecuperacao) {
    const msg = document.querySelector("#auth-message");
    msg.textContent = "Link de recuperação ausente ou inválido. Solicite um novo e-mail.";
    msg.className = "status-message error";
    redefinicao.querySelector("button").disabled = true;
  }
}
redefinicao?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(redefinicao));
  const msg = document.querySelector("#auth-message");
  if (dados.nova_senha !== dados.confirmacao) {
    msg.textContent = "As senhas não coincidem.";
    msg.className = "status-message error";
    return;
  }
  msg.textContent = "Salvando…";
  msg.className = "status-message loading";
  try {
    await enviar("/v1/auth/recuperacao/redefinir", {
      token: tokenRecuperacao,
      nova_senha: dados.nova_senha,
    });
    redefinicao.innerHTML = '<p class="status-message success">Senha redefinida com sucesso.</p><a class="secondary-button auth-secondary-link" href="/login">Entrar com a nova senha</a>';
  } catch (erro) {
    msg.textContent = erro.message;
    msg.className = "status-message error";
  }
});
const senha = document.querySelector("#password-form");
senha?.addEventListener("submit", async (event) => {
  event.preventDefault(); const dados = Object.fromEntries(new FormData(senha)); const msg = document.querySelector("#auth-message");
  if (dados.nova_senha !== dados.confirmacao) { msg.textContent = "As novas senhas não coincidem."; return; }
  try { const r = await enviar("/v1/auth/trocar-senha", { senha_atual: dados.senha_atual, nova_senha: dados.nova_senha }); location.href = r.destino; }
  catch (erro) { msg.textContent = erro.message; msg.className = "status-message error"; }
});

function cookie(nome) {
  return document.cookie.split("; ").find(item => item.startsWith(`${nome}=`))?.split("=").slice(1).join("=") || "";
}

async function enviar(url, dados) {
  const headers = { "Content-Type": "application/json" };
  const csrf = cookie("zr_csrf");
  if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
  const resposta = await fetch(url, { method: "POST", headers, body: JSON.stringify(dados) });
  const payload = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    const detalhe = payload.detail;
    const mensagemValidacao = Array.isArray(detalhe)
      ? detalhe.map(item => item?.msg || item?.message).filter(Boolean).join(" · ")
      : (typeof detalhe === "object" && detalhe ? detalhe.msg || detalhe.message : detalhe);
    const mensagem = mensagemValidacao || (resposta.status >= 500
      ? `Erro interno (${resposta.status}). Tente novamente em instantes.`
      : `Não foi possível concluir (${resposta.status}).`);
    throw new Error(mensagem);
  }
  return payload;
}

const login = document.querySelector("#login-form");
const parametrosLogin = new URLSearchParams(location.search);
const socialMfa = parametrosLogin.get("oauth_mfa") === "1";
const mensagemOAuth = parametrosLogin.get("oauth_error");

async function carregarProvedores() {
  if (!login || socialMfa) return;
  try {
    const resposta = await fetch("/v1/auth/social/providers");
    if (!resposta.ok) return;
    const dados = await resposta.json();
    const ativos = dados.providers.filter(item => item.enabled);
    if (!ativos.length) return;
    const destino = parametrosLogin.get("next") || "/admin";
    const area = document.querySelector("#social-login");
    const botoes = document.querySelector("#social-buttons");
    ativos.forEach(provedor => {
      const link = document.createElement("a");
      link.className = `social-button social-${provedor.id}`;
      link.href = `/v1/auth/social/${provedor.id}/start?next=${encodeURIComponent(destino)}`;
      link.textContent = `Continuar com ${provedor.nome}`;
      botoes.append(link);
    });
    area.hidden = false;
  } catch {
    // O login por senha continua disponível se o catálogo social estiver indisponível.
  }
}

if (login && mensagemOAuth) {
  const msg = document.querySelector("#auth-message");
  msg.textContent = mensagemOAuth;
  msg.className = "status-message error";
}

if (login && socialMfa) {
  document.querySelector("#social-login").hidden = true;
  document.querySelector("#password-login-fields").hidden = true;
  login.elements.identificador.disabled = true;
  login.elements.senha.disabled = true;
  document.querySelector("#mfa-field").hidden = false;
  login.elements.codigo_mfa.required = true;
  document.querySelector("#login-submit").textContent = "Confirmar código de segurança";
  document.querySelector("#forgot-password-link").hidden = true;
  const msg = document.querySelector("#auth-message");
  msg.textContent = "A identidade foi confirmada. Informe agora o código do seu autenticador.";
  msg.className = "status-message loading";
  login.elements.codigo_mfa.focus();
}

login?.addEventListener("submit", async event => {
  event.preventDefault();
  const msg = document.querySelector("#auth-message");
  msg.textContent = "Entrando…";
  msg.className = "status-message loading";
  try {
    const dados = Object.fromEntries(new FormData(login));
    if (!dados.codigo_mfa) delete dados.codigo_mfa;
    const resposta = socialMfa
      ? await enviar("/v1/auth/social/mfa", { codigo: dados.codigo_mfa || "" })
      : await enviar("/v1/auth/login", dados);
    location.href = resposta.destino;
  } catch (erro) {
    // Achado do usuário (23/09/2026): com senha certa, a primeira etapa do
    // login em duas etapas mostrava "Código MFA inválido ou ausente" como
    // se fosse um erro de senha -- na verdade é só o próximo passo
    // esperado (o campo de código nem existia na tela ainda), então tem
    // estilo neutro em vez de vermelho.
    const pedindoCodigo = !socialMfa && erro.message === "Informe o código do seu autenticador";
    msg.textContent = erro.message;
    msg.className = pedindoCodigo ? "status-message loading" : "status-message error";
    if (!socialMfa && (pedindoCodigo || erro.message === "Código MFA inválido")) {
      const campoMfa = document.querySelector("#mfa-field");
      campoMfa.hidden = false;
      login.elements.codigo_mfa.required = true;
      login.elements.codigo_mfa.focus();
    }
  }
});

carregarProvedores();

const recuperacao = document.querySelector("#forgot-password-form");
recuperacao?.addEventListener("submit", async event => {
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
  } catch (erro) {
    msg.textContent = erro.message;
    msg.className = "status-message error";
  }
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

redefinicao?.addEventListener("submit", async event => {
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
    await enviar("/v1/auth/recuperacao/redefinir", { token: tokenRecuperacao, nova_senha: dados.nova_senha });
    redefinicao.innerHTML = '<p class="status-message success">Senha redefinida com sucesso.</p><a class="secondary-button auth-secondary-link" href="/login">Entrar com a nova senha</a>';
  } catch (erro) {
    msg.textContent = erro.message;
    msg.className = "status-message error";
  }
});

const senha = document.querySelector("#password-form");
const regrasSenha = {
  length: valor => valor.length >= 12,
  lower: valor => /[a-z]/.test(valor),
  upper: valor => /[A-Z]/.test(valor),
  number: valor => /\d/.test(valor),
  special: valor => /[^A-Za-z0-9]/.test(valor),
};

function avaliarSenha(valor) {
  const resultado = Object.fromEntries(
    Object.entries(regrasSenha).map(([regra, validar]) => [regra, validar(valor)])
  );
  document.querySelectorAll("[data-password-rule]").forEach(item => {
    item.classList.toggle("met", resultado[item.dataset.passwordRule]);
  });
  return Object.values(resultado).every(Boolean);
}

senha?.elements.nova_senha.addEventListener("input", event => avaliarSenha(event.target.value));
senha?.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(senha));
  const msg = document.querySelector("#auth-message");
  if (dados.nova_senha !== dados.confirmacao) {
    msg.textContent = "As novas senhas não coincidem.";
    msg.className = "status-message error";
    return;
  }
  if (!avaliarSenha(dados.nova_senha)) {
    msg.textContent = "A nova senha ainda não atende a todos os requisitos.";
    msg.className = "status-message error";
    return;
  }
  msg.textContent = "Salvando…";
  msg.className = "status-message loading";
  try {
    const resposta = await enviar("/v1/auth/trocar-senha", { senha_atual: dados.senha_atual, nova_senha: dados.nova_senha });
    location.href = resposta.destino;
  } catch (erro) {
    msg.textContent = erro.message;
    msg.className = "status-message error";
  }
});

const mfaSetupPanel = document.querySelector("#mfa-setup-panel");
if (mfaSetupPanel) {
  const mfaSetupMsg = document.querySelector("#auth-message");
  const mfaSetupRecovery = document.querySelector("#mfa-setup-recovery");
  let destinoAposMfa = "/admin";

  function definirMensagemMfa(texto, tipo) {
    mfaSetupMsg.hidden = false;
    mfaSetupMsg.textContent = texto;
    mfaSetupMsg.className = `status-message ${tipo}`;
  }

  (async () => {
    try {
      const inicio = await enviar("/v1/auth/mfa/iniciar", {});
      document.querySelector("#mfa-setup-secret").textContent = inicio.segredo;
      new QRCode(document.querySelector("#mfa-setup-qrcode"), {
        text: inicio.uri,
        width: 160,
        height: 160,
        correctLevel: QRCode.CorrectLevel.M,
      });
      definirMensagemMfa("Escaneie o código ou cadastre o segredo manualmente e confirme com o código de 6 dígitos.", "loading");
    } catch (erro) {
      definirMensagemMfa(erro.message, "error");
    }
  })();

  document.querySelector("#mfa-setup-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(document.querySelector("#mfa-setup-secret").textContent);
      definirMensagemMfa("Segredo copiado.", "success");
    } catch {
      definirMensagemMfa("Não foi possível copiar automaticamente. Copie manualmente.", "error");
    }
  });

  document.querySelector("#mfa-setup-confirm").addEventListener("click", async () => {
    const codigo = document.querySelector("#mfa-setup-code").value.trim();
    if (!/^\d{6}$/.test(codigo)) {
      definirMensagemMfa("Informe o código de 6 dígitos gerado pelo app.", "error");
      return;
    }
    try {
      const fim = await enviar("/v1/auth/mfa/confirmar", { codigo });
      destinoAposMfa = fim.destino || destinoAposMfa;
      document.querySelector("#mfa-setup-recovery-codes").replaceChildren(...fim.codigos_recuperacao.map(item => {
        const li = document.createElement("li");
        li.textContent = item;
        return li;
      }));
      mfaSetupPanel.hidden = true;
      mfaSetupRecovery.hidden = false;
      definirMensagemMfa("MFA ativado. Guarde os códigos de recuperação antes de continuar.", "success");
    } catch (erro) {
      definirMensagemMfa(erro.message, "error");
    }
  });

  document.querySelector("#mfa-setup-download-recovery").addEventListener("click", () => {
    const codigos = [...document.querySelectorAll("#mfa-setup-recovery-codes li")].map(li => li.textContent);
    const blob = new Blob([`Códigos de recuperação MFA — Zé Registra\n\n${codigos.join("\n")}\n`], {
      type: "text/plain",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "codigos-recuperacao-mfa.txt";
    link.click();
    URL.revokeObjectURL(url);
  });

  document.querySelector("#mfa-setup-continuar").addEventListener("click", () => {
    location.href = destinoAposMfa;
  });
}

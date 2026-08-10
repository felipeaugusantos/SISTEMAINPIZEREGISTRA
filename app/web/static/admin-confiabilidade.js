const msg = document.querySelector("#reliability-message");
const contexto = document.querySelector("#reliability-context");
const form = document.querySelector("#reliability-form");
const jobStatus = document.querySelector("#job-status");
const mfaButton = document.querySelector("#enable-mfa");
const mfaPanel = document.querySelector("#mfa-panel");
const mfaRecovery = document.querySelector("#mfa-recovery");
const mfaStatus = document.querySelector("#mfa-status");
const socialIdentities = document.querySelector("#social-identities");
const socialIdentityStatus = document.querySelector("#social-identity-status");

async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Falha na operação (${response.status})`);
  return payload;
}

function definirStatus(el, texto, tipo) {
  el.hidden = false;
  el.textContent = texto;
  el.className = `status-message ${tipo}`;
}

function cardMetrica(valor, rotulo, detalhe) {
  const article = document.createElement("article");
  const strong = document.createElement("strong");
  strong.textContent = valor;
  const span = document.createElement("span");
  span.textContent = rotulo;
  article.append(strong, span);
  if (detalhe) {
    const small = document.createElement("small");
    small.textContent = detalhe;
    article.append(small);
  }
  return article;
}

async function carregar() {
  try {
    const data = await api("/v1/admin/confiabilidade");
    const uso = data.uso;
    const metrics = document.querySelector("#reliability-metrics");
    metrics.replaceChildren(
      cardMetrica(uso.usuarios, "Usuários"),
      cardMetrica(uso.pesquisas, "Pesquisas"),
      cardMetrica(uso.leads, "Leads"),
      cardMetrica(data.fila.status, "Fila", `${data.fila.pendentes ?? "—"} pendente(s)`),
    );
    const org = data.organizacao;
    form.elements.nome_exibido.value = org.branding.nome_exibido || org.nome;
    form.elements.cor_primaria.value = org.branding.cor_primaria || "#006b4f";
    form.elements.logo_url.value = org.branding.logo_url || "";
    form.elements.retencao_dados_dias.value = org.retencao_dados_dias;
    form.elements.politica_privacidade_versao.value = org.politica_privacidade_versao;
    contexto.textContent = `${org.nome} · ${org.status} · ${org.assinatura_status}`;
    msg.hidden = true;
    return data;
  } catch (error) {
    definirStatus(msg, error.message, "error");
    throw error;
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(form));
  try {
    await api("/v1/admin/confiabilidade/configuracao", {
      method: "PATCH",
      body: JSON.stringify({
        branding: {
          nome_exibido: dados.nome_exibido,
          cor_primaria: dados.cor_primaria,
          logo_url: dados.logo_url,
        },
        retencao_dados_dias: Number(dados.retencao_dados_dias),
        politica_privacidade_versao: dados.politica_privacidade_versao,
      }),
    });
    definirStatus(msg, "Configuração salva.", "success");
  } catch (error) {
    definirStatus(msg, error.message, "error");
  }
});

document.querySelectorAll("[data-job]").forEach(button => {
  button.addEventListener("click", async () => {
    const label = button.dataset.label;
    const botoes = document.querySelectorAll("[data-job]");
    botoes.forEach(item => { item.disabled = true; });
    button.textContent = "Processando…";
    definirStatus(jobStatus, `${label}: solicitação enviada para a fila…`, "loading");
    try {
      await api(`/v1/admin/confiabilidade/tarefas/${button.dataset.job}`, { method: "POST" });
      let dados = await carregar();
      for (let tentativa = 0; tentativa < 10 && dados.fila.pendentes > 0; tentativa += 1) {
        await new Promise(resolve => setTimeout(resolve, 500));
        dados = await carregar();
      }
      if (dados.fila.falhas > 0) {
        throw new Error("A rotina terminou com falha. Consulte os alertas operacionais.");
      }
      definirStatus(jobStatus, `${label}: verificação concluída com sucesso.`, "success");
    } catch (error) {
      definirStatus(jobStatus, `${label}: ${error.message}`, "error");
    } finally {
      button.textContent = label;
      botoes.forEach(item => { item.disabled = false; });
    }
  });
});

async function copiar(texto, aoConfirmar) {
  try {
    await navigator.clipboard.writeText(texto);
    aoConfirmar();
  } catch {
    definirStatus(mfaStatus, "Não foi possível copiar automaticamente. Copie manualmente.", "error");
  }
}

mfaButton.addEventListener("click", async () => {
  mfaButton.disabled = true;
  try {
    const inicio = await api("/v1/auth/mfa/iniciar", { method: "POST", body: "{}" });
    document.querySelector("#mfa-secret").textContent = inicio.segredo;
    mfaPanel.hidden = false;
    mfaRecovery.hidden = true;
    definirStatus(mfaStatus, "Cadastre o segredo no app e confirme com o código de 6 dígitos.", "loading");
    document.querySelector("#mfa-code").focus();
  } catch (error) {
    definirStatus(mfaStatus, error.message, "error");
    mfaButton.disabled = false;
  }
});

document.querySelector("#copy-secret").addEventListener("click", () => {
  copiar(
    document.querySelector("#mfa-secret").textContent,
    () => definirStatus(mfaStatus, "Segredo copiado.", "success"),
  );
});

document.querySelector("#confirm-mfa").addEventListener("click", async () => {
  const codigo = document.querySelector("#mfa-code").value.trim();
  if (!/^\d{6}$/.test(codigo)) {
    definirStatus(mfaStatus, "Informe o código de 6 dígitos gerado pelo app.", "error");
    return;
  }
  try {
    const fim = await api("/v1/auth/mfa/confirmar", {
      method: "POST",
      body: JSON.stringify({ codigo }),
    });
    const lista = document.querySelector("#recovery-codes");
    lista.replaceChildren(...fim.codigos_recuperacao.map(codigo => {
      const li = document.createElement("li");
      li.textContent = codigo;
      return li;
    }));
    mfaPanel.hidden = true;
    mfaRecovery.hidden = false;
    definirStatus(mfaStatus, "MFA ativado. Guarde os códigos de recuperação.", "success");
    mfaButton.textContent = "MFA ativo";
    mfaButton.disabled = true;
  } catch (error) {
    definirStatus(mfaStatus, error.message, "error");
  }
});

document.querySelector("#download-recovery").addEventListener("click", () => {
  const codigos = [...document.querySelectorAll("#recovery-codes li")].map(li => li.textContent);
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

carregar();

async function carregarIdentidades() {
  try {
    const dados = await api("/v1/auth/social/identities/me");
    socialIdentities.replaceChildren(...dados.providers.map(provedor => {
      const card = document.createElement("article");
      card.className = "social-identity-card";
      const texto = document.createElement("div");
      const titulo = document.createElement("strong");
      titulo.textContent = provedor.nome;
      const estado = document.createElement("span");
      estado.textContent = provedor.linked
        ? `Vinculado${provedor.email ? ` · ${provedor.email}` : ""}`
        : (provedor.enabled ? "Disponível para vincular" : "Não configurado neste ambiente");
      texto.append(titulo, estado);
      const acao = document.createElement(provedor.linked ? "button" : "a");
      acao.className = "secondary-button";
      if (provedor.linked) {
        acao.type = "button";
        acao.textContent = "Desvincular";
        acao.addEventListener("click", async () => {
          if (!confirm(`Desvincular a conta ${provedor.nome}?`)) return;
          try {
            await api(`/v1/auth/social/identities/${provedor.id}`, { method: "DELETE" });
            definirStatus(socialIdentityStatus, `${provedor.nome} desvinculado.`, "success");
            await carregarIdentidades();
          } catch (error) {
            definirStatus(socialIdentityStatus, error.message, "error");
          }
        });
      } else {
        acao.textContent = "Vincular";
        acao.href = `/v1/auth/social/${provedor.id}/link`;
        if (!provedor.enabled) {
          acao.setAttribute("aria-disabled", "true");
          acao.removeAttribute("href");
        }
      }
      card.append(texto, acao);
      return card;
    }));
  } catch (error) {
    definirStatus(socialIdentityStatus, error.message, "error");
  }
}

const socialParams = new URLSearchParams(location.search);
if (socialParams.get("oauth_linked")) {
  definirStatus(socialIdentityStatus, "Conta externa vinculada com sucesso.", "success");
  history.replaceState(null, "", location.pathname);
} else if (socialParams.get("oauth_error")) {
  definirStatus(socialIdentityStatus, socialParams.get("oauth_error"), "error");
  history.replaceState(null, "", location.pathname);
}
carregarIdentidades();

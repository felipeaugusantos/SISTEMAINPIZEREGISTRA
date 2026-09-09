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
const simulationOutput = document.querySelector("#retention-simulation");
const legalHoldForm = document.querySelector("#legal-hold-form");
const legalHoldStatus = document.querySelector("#legal-hold-status");
const logoPreview = document.querySelector("#branding-logo-preview");
const logoStatus = document.querySelector("#branding-logo-status");
const privacyPolicyForm = document.querySelector("#privacy-policy-form");
const privacyPolicyStatus = document.querySelector("#privacy-policy-status");
const privacyPolicyList = document.querySelector("#privacy-policy-list");
let prazoRetencaoCarregado = null;
let ultimaSimulacaoId = null;

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  options.headers = headers;
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  const detalhe = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || {});
  if (!response.ok) throw new Error(detalhe || `Falha na operação (${response.status})`);
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
    logoPreview.hidden = !org.branding.logo_url;
    if (org.branding.logo_url) {
      logoPreview.referrerPolicy = "no-referrer";
      logoPreview.src = org.branding.logo_url;
    } else {
      logoPreview.removeAttribute("src");
    }
    prazoRetencaoCarregado = data.retencao?.matriz?.find(item => item.categoria === "lead")?.prazo_dias
      ?? org.retencao_dados_dias;
    form.elements.retencao_dados_dias.value = prazoRetencaoCarregado;
    contexto.textContent = `${org.nome} · ${org.status} · ${org.assinatura_status}`;
    document.querySelector("#learning-pipeline-job").hidden = !data.permissoes?.superadmin;
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
  const novoPrazo = Number(dados.retencao_dados_dias);
  const alterouRetencao = novoPrazo !== prazoRetencaoCarregado;
  const vigencia = dados.retencao_vigencia_em ? new Date(dados.retencao_vigencia_em).toISOString() : null;
  try {
    await api("/v1/admin/confiabilidade/configuracao", {
      method: "PATCH",
      body: JSON.stringify({
        branding: {
          nome_exibido: dados.nome_exibido,
          cor_primaria: dados.cor_primaria,
          logo_url: dados.logo_url,
        },
        retencao_dados_dias: novoPrazo,
        retencao_justificativa: alterouRetencao ? dados.retencao_justificativa : null,
        retencao_finalidade: alterouRetencao ? dados.retencao_finalidade : null,
        retencao_base_legal: alterouRetencao ? dados.retencao_base_legal : null,
        retencao_vigencia_em: alterouRetencao ? vigencia : null,
        confirmar_reducao_retencao: dados.confirmar_reducao_retencao === "on",
        retencao_simulacao_id: alterouRetencao ? ultimaSimulacaoId : null,
      }),
    });
    definirStatus(msg, "Configuração salva.", "success");
    form.elements.retencao_justificativa.value = "";
    form.elements.retencao_finalidade.value = "";
    form.elements.retencao_base_legal.value = "";
    form.elements.retencao_vigencia_em.value = "";
    form.elements.confirmar_reducao_retencao.checked = false;
    ultimaSimulacaoId = null;
    await carregar();
  } catch (error) {
    definirStatus(msg, error.message, "error");
  }
});

document.querySelector("#upload-branding-logo").addEventListener("click", async () => {
  const arquivo = document.querySelector("#branding-logo-file").files[0];
  if (!arquivo) return definirStatus(logoStatus, "Selecione uma imagem.", "error");
  const dados = new FormData();
  dados.append("arquivo", arquivo);
  try {
    await api("/v1/admin/confiabilidade/identidade/logo", { method: "POST", body: dados });
    definirStatus(logoStatus, "Logotipo validado, normalizado e publicado.", "success");
    document.querySelector("#branding-logo-file").value = "";
    await carregar();
  } catch (error) {
    definirStatus(logoStatus, error.message, "error");
  }
});

document.querySelector("#remove-branding-logo").addEventListener("click", async () => {
  if (!confirm("Remover o logotipo personalizado e voltar ao padrão?")) return;
  try {
    await api("/v1/admin/confiabilidade/identidade/logo", { method: "DELETE" });
    definirStatus(logoStatus, "Logotipo personalizado removido.", "success");
    await carregar();
  } catch (error) {
    definirStatus(logoStatus, error.message, "error");
  }
});

function limparFormularioPolitica() {
  privacyPolicyForm.reset();
  privacyPolicyForm.elements.politica_id.value = "";
  privacyPolicyForm.querySelector('[type="submit"]').textContent = "Salvar rascunho";
}

function botaoPolitica(rotulo, acao) {
  const botao = document.createElement("button");
  botao.type = "button";
  botao.className = "secondary-button";
  botao.textContent = rotulo;
  botao.addEventListener("click", acao);
  return botao;
}

async function carregarPoliticas() {
  try {
    const dados = await api("/v1/admin/politicas-privacidade");
    const resumo = document.querySelector("#privacy-policy-summary");
    resumo.textContent = dados.vigente
      ? `Vigente: versão ${dados.vigente.versao} · SHA-256 ${dados.vigente.sha256.slice(0, 12)}… · ${dados.consentimentos_pendentes} aceite(s) pendente(s)`
      : "Nenhuma política vigente. Crie e publique um rascunho antes de captar novos consentimentos.";
    document.querySelector("#privacy-policy-rule").textContent = dados.regra_novo_consentimento;
    privacyPolicyList.replaceChildren(...dados.politicas.map(item => {
      const article = document.createElement("article");
      article.className = "privacy-policy-item";
      const texto = document.createElement("div");
      const titulo = document.createElement("strong");
      titulo.textContent = `Versão ${item.versao}`;
      const meta = document.createElement("span");
      meta.textContent = `${item.status} · SHA-256 ${item.sha256.slice(0, 12)}…${item.requer_novo_consentimento ? " · novo aceite" : ""}`;
      const motivo = document.createElement("small");
      motivo.textContent = item.motivo_alteracao;
      texto.append(titulo, meta, motivo);
      const acoes = document.createElement("div");
      acoes.className = "reliability-actions";
      if (item.status === "rascunho") {
        acoes.append(
          botaoPolitica("Editar", () => {
            privacyPolicyForm.elements.politica_id.value = item.id;
            privacyPolicyForm.elements.versao.value = item.versao;
            privacyPolicyForm.elements.conteudo.value = item.conteudo || "";
            privacyPolicyForm.elements.documento_referencia.value = item.documento_referencia || "";
            privacyPolicyForm.elements.motivo_alteracao.value = item.motivo_alteracao;
            privacyPolicyForm.elements.requer_novo_consentimento.checked = item.requer_novo_consentimento;
            privacyPolicyForm.querySelector('[type="submit"]').textContent = "Atualizar rascunho";
            privacyPolicyForm.scrollIntoView({ behavior: "smooth", block: "start" });
          }),
          botaoPolitica("Publicar", async () => {
            if (!confirm(`Publicar a versão ${item.versao}? A versão vigente será revogada e esta não poderá mais ser editada.`)) return;
            try {
              await api(`/v1/admin/politicas-privacidade/${item.id}/publicar`, {
                method: "POST",
                body: JSON.stringify({ confirmar_publicacao: true }),
              });
              definirStatus(privacyPolicyStatus, `Versão ${item.versao} publicada e auditada.`, "success");
              limparFormularioPolitica();
              await carregarPoliticas();
            } catch (error) {
              definirStatus(privacyPolicyStatus, error.message, "error");
            }
          }),
        );
      }
      article.append(texto, acoes);
      return article;
    }));
  } catch (error) {
    definirStatus(privacyPolicyStatus, error.message, "error");
  }
}

privacyPolicyForm.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(privacyPolicyForm));
  const politicaId = dados.politica_id;
  const payload = {
    versao: dados.versao,
    conteudo: dados.conteudo || null,
    documento_referencia: dados.documento_referencia || null,
    motivo_alteracao: dados.motivo_alteracao,
    requer_novo_consentimento: dados.requer_novo_consentimento === "on",
  };
  try {
    await api(politicaId ? `/v1/admin/politicas-privacidade/${politicaId}` : "/v1/admin/politicas-privacidade", {
      method: politicaId ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    definirStatus(privacyPolicyStatus, politicaId ? "Rascunho atualizado." : "Rascunho criado.", "success");
    limparFormularioPolitica();
    await carregarPoliticas();
  } catch (error) {
    definirStatus(privacyPolicyStatus, error.message, "error");
  }
});

document.querySelector("#privacy-policy-clear").addEventListener("click", limparFormularioPolitica);

form.elements.retencao_dados_dias.addEventListener("input", () => {
  ultimaSimulacaoId = null;
});

document.querySelector("#simulate-retention").addEventListener("click", async () => {
  try {
    const resultado = await api("/v1/admin/confiabilidade/retencao/simular", {
      method: "POST",
      body: JSON.stringify({ prazo_dias: Number(form.elements.retencao_dados_dias.value) }),
    });
    ultimaSimulacaoId = resultado.simulacao_id;
    simulationOutput.hidden = false;
    simulationOutput.textContent = [
      "Simulação — nenhum descarte executado",
      `Registros afetados: ${resultado.total_afetado}`,
      `Mais antigo: ${resultado.registro_mais_antigo || "—"}`,
      `Por status: ${JSON.stringify(resultado.por_status)}`,
      `Bloqueios: ${JSON.stringify(resultado.bloqueios)}`,
      `Elegíveis para revisão humana: ${resultado.elegiveis_revisao_humana}`,
    ].join("\n");
  } catch (error) {
    definirStatus(simulationOutput, error.message, "error");
  }
});

legalHoldForm.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(legalHoldForm));
  try {
    await api(`/v1/admin/confiabilidade/retencao/leads/${Number(dados.lead_id)}/legal-hold`, {
      method: "POST",
      body: JSON.stringify({ motivo: dados.motivo }),
    });
    definirStatus(legalHoldStatus, "Legal hold ativado e auditado.", "success");
  } catch (error) {
    definirStatus(legalHoldStatus, error.message, "error");
  }
});

document.querySelector("#release-legal-hold").addEventListener("click", async () => {
  const leadId = Number(legalHoldForm.elements.lead_id.value);
  if (!leadId) return definirStatus(legalHoldStatus, "Informe o ID do lead.", "error");
  try {
    await api(`/v1/admin/confiabilidade/retencao/leads/${leadId}/legal-hold`, { method: "DELETE" });
    definirStatus(legalHoldStatus, "Legal hold liberado e auditado.", "success");
  } catch (error) {
    definirStatus(legalHoldStatus, error.message, "error");
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
      const estadoAnterior = await carregar();
      const falhasAnteriores = Number(estadoAnterior.fila.falhas || 0);
      const rotinaAnteriorId = estadoAnterior.ultima_rotina_aprendizado?.id;
      await api(`/v1/admin/confiabilidade/tarefas/${button.dataset.job}`, { method: "POST" });
      let dados = await carregar();
      for (let tentativa = 0; tentativa < 180 && (dados.fila.pendentes > 0 || dados.fila.processando > 0); tentativa += 1) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        dados = await carregar();
      }
      if (dados.fila.pendentes > 0 || dados.fila.processando > 0) {
        throw new Error("A rotina continua em processamento. Acompanhe os alertas operacionais.");
      }
      if (Number(dados.fila.falhas || 0) > falhasAnteriores) {
        throw new Error("A rotina terminou com falha. Consulte os alertas operacionais.");
      }
      const rotina = dados.ultima_rotina_aprendizado;
      if (rotina && rotina.id !== rotinaAnteriorId) {
        definirStatus(
          jobStatus,
          `${label}: ${rotina.mensagem}`,
          rotina.severidade === "aviso" ? "error" : "success",
        );
      } else {
        definirStatus(jobStatus, `${label}: verificação concluída com sucesso.`, "success");
      }
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
carregarPoliticas();

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

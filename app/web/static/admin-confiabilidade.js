const msg = document.querySelector("#reliability-message");
const form = document.querySelector("#reliability-form");
const jobStatus = document.querySelector("#job-status");

async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Falha na operação (${response.status})`);
  return payload;
}

async function carregar() {
  try {
    const data = await api("/v1/admin/confiabilidade");
    const uso = data.uso;
    document.querySelector("#reliability-metrics").innerHTML = `
      <article><strong>${uso.usuarios}</strong><span>Usuários</span></article>
      <article><strong>${uso.pesquisas}</strong><span>Pesquisas</span></article>
      <article><strong>${uso.leads}</strong><span>Leads</span></article>
      <article><strong>${data.fila.status}</strong><span>Fila · ${data.fila.pendentes ?? "—"} pendente(s)</span></article>`;
    const org = data.organizacao;
    form.elements.nome_exibido.value = org.branding.nome_exibido || org.nome;
    form.elements.cor_primaria.value = org.branding.cor_primaria || "#006b4f";
    form.elements.logo_url.value = org.branding.logo_url || "";
    form.elements.retencao_dados_dias.value = org.retencao_dados_dias;
    form.elements.politica_privacidade_versao.value = org.politica_privacidade_versao;
    msg.textContent = `${org.nome} · ${org.status} · ${org.assinatura_status}`;
    msg.className = "status-message success";
    return data;
  } catch (error) {
    msg.textContent = error.message;
    msg.className = "status-message error";
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
    msg.textContent = "Configuração salva.";
    msg.className = "status-message success";
  } catch (error) {
    msg.textContent = error.message;
    msg.className = "status-message error";
  }
});

document.querySelectorAll("[data-job]").forEach(button => {
  button.addEventListener("click", async () => {
    const label = button.dataset.label;
    const botoes = document.querySelectorAll("[data-job]");
    botoes.forEach(item => { item.disabled = true; });
    button.textContent = "Processando…";
    jobStatus.textContent = `${label}: solicitação enviada para a fila…`;
    jobStatus.className = "status-message loading";
    try {
      await api(`/v1/admin/confiabilidade/tarefas/${button.dataset.job}`, { method: "POST" });
      let dados = await carregar();
      for (let tentativa = 0; tentativa < 10 && dados.fila.pendentes > 0; tentativa += 1) {
        await new Promise(resolve => setTimeout(resolve, 500));
        dados = await carregar();
      }
      if (dados.fila.falhas > 0) throw new Error("A rotina terminou com falha. Consulte os alertas operacionais.");
      jobStatus.textContent = `${label}: verificação concluída com sucesso.`;
      jobStatus.className = "status-message success";
    } catch (error) {
      jobStatus.textContent = `${label}: ${error.message}`;
      jobStatus.className = "status-message error";
    } finally {
      button.textContent = label;
      botoes.forEach(item => { item.disabled = false; });
    }
  });
});

document.querySelector("#enable-mfa").addEventListener("click", async () => {
  try {
    const inicio = await api("/v1/auth/mfa/iniciar", { method: "POST", body: "{}" });
    const codigo = prompt(`Cadastre este segredo no autenticador:\n${inicio.segredo}\n\nDepois informe o código de 6 dígitos:`);
    if (!codigo) return;
    const fim = await api("/v1/auth/mfa/confirmar", {
      method: "POST",
      body: JSON.stringify({ codigo }),
    });
    prompt("Guarde estes códigos de recuperação em local seguro:", fim.codigos_recuperacao.join(" "));
    document.querySelector("#enable-mfa").textContent = "MFA ativo";
    document.querySelector("#enable-mfa").disabled = true;
  } catch (error) {
    alert(error.message);
  }
});

carregar();

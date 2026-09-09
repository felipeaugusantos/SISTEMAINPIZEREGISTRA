const ffMessage = document.querySelector("#ff-message");

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function dateLabel(value) {
  return value ? new Date(value).toLocaleString("pt-BR") : "—";
}

function mostrarMensagem(texto, tipo = "success") {
  ffMessage.hidden = false;
  ffMessage.className = `status-message ${tipo}`;
  ffMessage.textContent = texto;
}

async function api(url, options = {}) {
  const resposta = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const corpo = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(corpo.detail || `Falha na operação (${resposta.status})`);
  return corpo;
}

const ESTADOS_LABEL = {
  ativo: "Ativo",
  somente_administradores: "Somente administradores",
  adiado: "Adiado",
  desativado: "Desativado",
};

function renderFlagCard(flag) {
  const artigo = document.createElement("article");
  artigo.className = "overview-section ff-card";
  artigo.dataset.codigo = flag.codigo;
  artigo.innerHTML = `
    <header>
      <div>
        <p class="eyebrow">${escapeHtml(flag.codigo)} · padrão: ${escapeHtml(flag.estado_padrao)}${flag.ativo ? "" : " · <strong>desligada globalmente</strong>"}</p>
        <h2>${escapeHtml(flag.nome)}</h2>
        <p>${escapeHtml(flag.descricao)}</p>
        <p><small>Módulos: ${flag.modulos_envolvidos.map(escapeHtml).join(", ") || "—"} · Dependências: ${flag.dependencias.map(escapeHtml).join(", ") || "nenhuma"}</small></p>
        <p class="ff-ativacao-info"><small>Ativação: ${dateLabel(flag.data_ativacao)} · Expira: ${dateLabel(flag.data_expiracao)}</small></p>
      </div>
      <button type="button" class="secondary-button ff-excluir" title="Excluir esta feature flag">Excluir</button>
    </header>
    <form class="admin-filters ff-acao-form">
      <label><span>ID da organização</span><input type="number" name="organizacao_id" min="1" required></label>
      <button type="submit" class="primary-button" data-acao="ativar">Ativar agora</button>
      <button type="submit" class="secondary-button" data-acao="testar-administradores">Testar somente com administradores</button>
      <button type="submit" class="secondary-button" data-acao="adiar">Adiar ativação</button>
      <button type="submit" class="secondary-button" data-acao="desativar">Desativar funcionalidade</button>
    </form>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Organização</th><th>Estado</th><th>Adiado até</th><th>Atualizado em</th></tr></thead>
        <tbody class="ff-organizacoes-rows"><tr><td colspan="4">Carregando…</td></tr></tbody>
      </table>
    </div>
  `;
  artigo.querySelector(".ff-excluir").addEventListener("click", async () => {
    if (!confirm(`Excluir definitivamente a flag "${flag.codigo}"? Isso remove também os estados por organização.`)) return;
    try {
      await api(`/v1/admin/feature-flags/${flag.codigo}`, {
        method: "DELETE",
        body: JSON.stringify({ confirmar_exclusao: true }),
      });
      mostrarMensagem(`Flag "${flag.codigo}" excluída.`);
      await carregarFlags();
    } catch (error) {
      mostrarMensagem(error.message, "error");
    }
  });
  const form = artigo.querySelector(".ff-acao-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const acao = event.submitter.dataset.acao;
    const organizacaoId = form.elements.organizacao_id.value;
    if (!organizacaoId) return;
    try {
      if (acao === "adiar") {
        const dias = Number(prompt("Adiar por quantos dias?", "7") || 0);
        if (!dias) return;
        await api(`/v1/admin/feature-flags/${flag.codigo}/organizacoes/${organizacaoId}/adiar`, {
          method: "POST",
          body: JSON.stringify({ dias }),
        });
      } else {
        await api(`/v1/admin/feature-flags/${flag.codigo}/organizacoes/${organizacaoId}/${acao}`, { method: "POST" });
      }
      mostrarMensagem(`Aplicado à organização ${organizacaoId}.`);
      await carregarOrganizacoes(flag.codigo, artigo);
    } catch (error) {
      mostrarMensagem(error.message, "error");
    }
  });
  return artigo;
}

async function carregarOrganizacoes(codigo, artigo) {
  const linhas = artigo.querySelector(".ff-organizacoes-rows");
  try {
    const detalhe = await api(`/v1/admin/feature-flags/${codigo}`);
    const ativacaoInfo = artigo.querySelector(".ff-ativacao-info");
    if (ativacaoInfo) ativacaoInfo.innerHTML = `<small>Ativação: ${dateLabel(detalhe.data_ativacao)} · Expira: ${dateLabel(detalhe.data_expiracao)}</small>`;
    linhas.innerHTML = detalhe.organizacoes.length
      ? detalhe.organizacoes.map((item) => `<tr>
          <td>${escapeHtml(item.organizacao_nome)} (#${item.organizacao_id})</td>
          <td>${ESTADOS_LABEL[item.estado] || escapeHtml(item.estado)}</td>
          <td>${dateLabel(item.adiado_ate)}</td>
          <td>${dateLabel(item.atualizado_em)}</td>
        </tr>`).join("")
      : `<tr><td colspan="4">Nenhuma organização com estado explícito ainda -- todas usam o padrão da flag.</td></tr>`;
  } catch (error) {
    linhas.innerHTML = `<tr><td colspan="4">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function carregarFlags() {
  const lista = document.querySelector("#ff-lista");
  try {
    const dados = await api("/v1/admin/feature-flags");
    lista.replaceChildren();
    if (!dados.itens.length) {
      lista.innerHTML = "<p>Nenhuma feature flag cadastrada ainda.</p>";
      return;
    }
    for (const flag of dados.itens) {
      const artigo = renderFlagCard(flag);
      lista.append(artigo);
      carregarOrganizacoes(flag.codigo, artigo);
    }
  } catch (error) {
    lista.innerHTML = `<p class="status-message error">${escapeHtml(error.message)}</p>`;
  }
}

document.querySelector("#ff-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const modulos = form.elements.modulos_envolvidos.value.split(",").map((v) => v.trim()).filter(Boolean);
  const dependencias = form.elements.dependencias.value.split(",").map((v) => v.trim()).filter(Boolean);
  const expiracao = form.elements.data_expiracao.value;
  try {
    await api("/v1/admin/feature-flags", {
      method: "POST",
      body: JSON.stringify({
        codigo: form.elements.codigo.value.trim(),
        nome: form.elements.nome.value.trim(),
        descricao: form.elements.descricao.value.trim(),
        modulos_envolvidos: modulos,
        dependencias,
        estado_padrao: form.elements.estado_padrao.value,
        data_expiracao: expiracao ? new Date(expiracao).toISOString() : null,
        confirmar_nao_e_correcao_seguranca: form.elements.confirmar_nao_e_correcao_seguranca.checked,
      }),
    });
    form.reset();
    mostrarMensagem("Feature flag criada.");
    await carregarFlags();
  } catch (error) {
    mostrarMensagem(error.message, "error");
  }
});

carregarFlags();

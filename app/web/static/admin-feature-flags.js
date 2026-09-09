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

const ESTAGIOS_ROLLOUT = ["ambiente_interno", "administradores", "organizacoes_piloto", "percentual_limitado", "liberacao_geral"];
const ESTAGIOS_LABEL = {
  ambiente_interno: "1. Ambiente interno",
  administradores: "2. Administradores",
  organizacoes_piloto: "3. Organizações-piloto",
  percentual_limitado: "4. Percentual limitado",
  liberacao_geral: "5. Liberação geral",
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
    <div class="ff-rollout">
      <p class="eyebrow">Fase 5 · liberação gradual</p>
      <p class="ff-rollout-status"></p>
      <form class="admin-filters ff-rollout-form">
        <label><span>Estágio</span>
          <select name="estagio_rollout">
            ${ESTAGIOS_ROLLOUT.map((estagio) => `<option value="${estagio}">${ESTAGIOS_LABEL[estagio]}</option>`).join("")}
          </select>
        </label>
        <label><span>Percentual (estágio 4)</span><input type="number" name="percentual_rollout" min="0" max="100" value="${flag.percentual_rollout ?? 100}"></label>
        <label><span>Limite de taxa de erro p/ interrupção automática</span><input type="number" name="limite_taxa_erro" min="1" max="100" step="1" placeholder="ex.: 5 (%)" value="${flag.limite_taxa_erro != null ? Math.round(flag.limite_taxa_erro * 100) : ""}"></label>
        <label><span>Amostra mínima</span><input type="number" name="limite_eventos_minimo" min="1" value="${flag.limite_eventos_minimo ?? 20}"></label>
        <button type="submit" class="primary-button">Avançar estágio</button>
      </form>
      <div class="ff-rollout-acoes">
        <button type="button" class="secondary-button ff-interromper">Interromper rollout</button>
        <button type="button" class="secondary-button ff-retomar" hidden>Retomar rollout</button>
        <button type="button" class="secondary-button ff-ver-monitoramento">Ver monitoramento por grupo</button>
      </div>
      <div class="table-wrap ff-monitoramento" hidden>
        <table>
          <thead><tr><th>Grupo</th><th>Uso</th><th>Erros</th><th>Falhas integração</th><th>Taxa de erro</th><th>Duração média</th></tr></thead>
          <tbody class="ff-monitoramento-rows"></tbody>
        </table>
        <p class="ff-reclamacoes"></p>
      </div>
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

  const rolloutForm = artigo.querySelector(".ff-rollout-form");
  rolloutForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const limitePercentual = rolloutForm.elements.limite_taxa_erro.value;
    try {
      await api(`/v1/admin/feature-flags/${flag.codigo}/rollout`, {
        method: "PATCH",
        body: JSON.stringify({
          estagio_rollout: rolloutForm.elements.estagio_rollout.value,
          percentual_rollout: Number(rolloutForm.elements.percentual_rollout.value || 0),
          limite_taxa_erro: limitePercentual ? Number(limitePercentual) / 100 : null,
          limite_eventos_minimo: Number(rolloutForm.elements.limite_eventos_minimo.value || 20),
        }),
      });
      mostrarMensagem(`Estágio de "${flag.codigo}" atualizado.`);
      await carregarOrganizacoes(flag.codigo, artigo);
    } catch (error) {
      mostrarMensagem(error.message, "error");
    }
  });

  artigo.querySelector(".ff-interromper").addEventListener("click", async () => {
    const motivo = prompt("Motivo da interrupção (mín. 10 caracteres):");
    if (!motivo || motivo.trim().length < 10) return;
    try {
      const resposta = await api(`/v1/admin/feature-flags/${flag.codigo}/interromper`, {
        method: "POST",
        body: JSON.stringify({ motivo: motivo.trim() }),
      });
      mostrarMensagem(`Rollout interrompido: ${resposta.resultado}`);
      await carregarOrganizacoes(flag.codigo, artigo);
    } catch (error) {
      mostrarMensagem(error.message, "error");
    }
  });

  artigo.querySelector(".ff-retomar").addEventListener("click", async () => {
    try {
      await api(`/v1/admin/feature-flags/${flag.codigo}/retomar`, { method: "POST" });
      mostrarMensagem(`Rollout de "${flag.codigo}" retomado -- avance o estágio quando quiser.`);
      await carregarOrganizacoes(flag.codigo, artigo);
    } catch (error) {
      mostrarMensagem(error.message, "error");
    }
  });

  artigo.querySelector(".ff-ver-monitoramento").addEventListener("click", () => carregarMonitoramento(flag.codigo, artigo));

  atualizarStatusRollout(artigo, flag);
  return artigo;
}

function atualizarStatusRollout(artigo, detalhe) {
  const status = artigo.querySelector(".ff-rollout-status");
  const estagioLabel = ESTAGIOS_LABEL[detalhe.estagio_rollout] || detalhe.estagio_rollout || "—";
  status.innerHTML = detalhe.pausado_em
    ? `<strong>Interrompido</strong> em ${dateLabel(detalhe.pausado_em)} por ${escapeHtml(detalhe.pausado_por || "—")}: ${escapeHtml(detalhe.pausado_motivo || "")} · estágio atual: ${escapeHtml(estagioLabel)}`
    : `Estágio atual: ${escapeHtml(estagioLabel)}`;
  const select = artigo.querySelector('.ff-rollout-form select[name="estagio_rollout"]');
  if (select && detalhe.estagio_rollout) select.value = detalhe.estagio_rollout;
  artigo.querySelector(".ff-retomar").hidden = !detalhe.pausado_em;
}

async function carregarMonitoramento(codigo, artigo) {
  const bloco = artigo.querySelector(".ff-monitoramento");
  const linhas = artigo.querySelector(".ff-monitoramento-rows");
  const reclamacoesEl = artigo.querySelector(".ff-reclamacoes");
  bloco.hidden = false;
  linhas.innerHTML = `<tr><td colspan="6">Carregando…</td></tr>`;
  try {
    const dados = await api(`/v1/admin/feature-flags/${codigo}/monitoramento?horas=24`);
    linhas.innerHTML = dados.grupos.map((grupo) => `<tr>
        <td>${ESTAGIOS_LABEL[grupo.grupo] || escapeHtml(grupo.grupo)}</td>
        <td>${grupo.uso}</td>
        <td>${grupo.erros}</td>
        <td>${grupo.falhas_integracao}</td>
        <td>${grupo.taxa_erro != null ? `${(grupo.taxa_erro * 100).toFixed(1)}%` : "—"}</td>
        <td>${grupo.duracao_media_ms != null ? `${grupo.duracao_media_ms} ms` : "—"}</td>
      </tr>`).join("");
    reclamacoesEl.textContent = `Reclamações relatadas (últimas ${dados.horas}h, módulos desta flag): ${dados.reclamacoes}`;
  } catch (error) {
    linhas.innerHTML = `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function carregarOrganizacoes(codigo, artigo) {
  const linhas = artigo.querySelector(".ff-organizacoes-rows");
  try {
    const detalhe = await api(`/v1/admin/feature-flags/${codigo}`);
    const ativacaoInfo = artigo.querySelector(".ff-ativacao-info");
    if (ativacaoInfo) ativacaoInfo.innerHTML = `<small>Ativação: ${dateLabel(detalhe.data_ativacao)} · Expira: ${dateLabel(detalhe.data_expiracao)}</small>`;
    atualizarStatusRollout(artigo, detalhe);
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

const form = document.querySelector("#consulta-form");
const statusEl = document.querySelector("#consulta-status");
const resultado = document.querySelector("#resultado");
const deleteButton = document.querySelector("#delete-research");
const deleteDialog = document.querySelector("#delete-research-dialog");
const deleteForm = document.querySelector("#delete-research-form");
let podeExcluirPesquisa = false;
let exclusaoPendente = false;

function setStatus(texto, tipo) {
  statusEl.hidden = false;
  statusEl.textContent = texto;
  statusEl.className = `status-message ${tipo}`;
}

async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detalhe = Array.isArray(payload.detail)
      ? payload.detail.map(item => item.msg).join("; ")
      : payload.detail || `Falha na operação (${response.status})`;
    throw new Error(detalhe);
  }
  return payload;
}

api("/v1/auth/me").then(usuario => {
  podeExcluirPesquisa = Boolean(
    usuario.superadmin
    || usuario.perfil === "administrador"
    || (usuario.permissoes || []).includes("leads.delete")
  );
  atualizarAcaoExclusao();
}).catch(() => {});

// Achado da auditoria completa do CRM (06/09/2026, item 4): classe Nice
// agora é capturada aqui e usada de verdade pelo motor de busca/risco.
api("/v1/admin/consulta/classes-nice").then(classes => {
  const select = document.querySelector("#consulta-classe-nice");
  for (const { codigo, titulo } of classes) {
    const option = document.createElement("option");
    option.value = codigo;
    option.textContent = `${codigo} — ${titulo}`;
    select.append(option);
  }
}).catch(() => {});

function card(valor, rotulo) {
  const article = document.createElement("article");
  const strong = document.createElement("strong");
  strong.textContent = valor;
  const span = document.createElement("span");
  span.textContent = rotulo;
  article.append(strong, span);
  return article;
}

function paragrafo(texto, className) {
  const p = document.createElement("p");
  p.textContent = texto;
  if (className) p.className = className;
  return p;
}

function renderPrognostico(prog) {
  const box = document.querySelector("#res-prognostico");
  box.replaceChildren();
  if (!prog) return;
  const rotulos = { favoravel: "Favorável", atencao: "Atenção", desfavoravel: "Desfavorável" };
  const sec = document.createElement("section");
  sec.className = "report-classification prognostico";
  sec.dataset.veredito = prog.veredito;

  const cabecalho = document.createElement("div");
  const eyebrow = paragrafo("Triagem determinística de registrabilidade", "eyebrow");
  const titulo = document.createElement("h2");
  titulo.textContent = prog.titulo;
  const tag = paragrafo(`Leitura técnica: ${rotulos[prog.veredito] || prog.veredito}`, "prognostico-tag");
  tag.dataset.veredito = prog.veredito;
  cabecalho.append(eyebrow, titulo, tag);
  sec.append(cabecalho, paragrafo(prog.resumo));

  if (prog.motivos && prog.motivos.length) {
    sec.append(paragrafo("Motivos identificados:"));
    const ul = document.createElement("ul");
    ul.className = "prognostico-motivos";
    for (const motivo of prog.motivos) {
      const li = document.createElement("li");
      const forte = document.createElement("strong");
      forte.textContent = `${motivo.criterio}: `;
      li.append(forte, document.createTextNode(`${motivo.conclusao} (${motivo.referencia})`));
      ul.append(li);
    }
    sec.append(ul);
  }
  if (prog.pendencias && prog.pendencias.length) {
    sec.append(paragrafo(`Ainda dependem de avaliação: ${prog.pendencias.join(" · ")}.`));
  }
  sec.append(paragrafo(prog.ressalva, "matrix-status"));
  box.append(sec);
}

function renderRelatorio(data) {
  document.querySelector("#res-marca").textContent = data.marca;
  document.querySelector("#res-metrics").replaceChildren(
    card(data.total, "Ocorrências"),
    card(data.risco_nivel || "—", "Nível de risco"),
    card(data.ultima_rpi ? `RPI ${data.ultima_rpi}` : "—", "Base até"),
    card(data.classe_nice ? `Classe ${data.classe_nice}` : "Todas", "Classe pesquisada"),
    card((data.classes_atividade || []).length, "Classes sugeridas"),
  );
  renderPrognostico(data.prognostico_registrabilidade);

  const conclusao = document.querySelector("#res-conclusao");
  conclusao.replaceChildren();
  if (data.conclusao) {
    conclusao.append(paragrafo("Conclusão indicativa", "eyebrow"));
    const h = document.createElement("h2");
    h.textContent = data.conclusao.titulo;
    conclusao.append(h, paragrafo(data.conclusao.resumo));
    if (data.conclusao.revisao_humana_recomendada) {
      conclusao.append(paragrafo("Revisão humana recomendada antes de qualquer decisão.", "matrix-status"));
    }
  }
  resultado.hidden = false;
  resultado.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function carregarRelatorio(id) {
  setStatus("Gerando relatório…", "loading");
  try {
    const data = await api(`/v1/admin/consulta/${encodeURIComponent(id)}/relatorio`);
    renderRelatorio(data);
    setStatus("Consulta concluída.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

const contatosSection = document.querySelector("#contatos-section");
const contatoForm = document.querySelector("#contato-form");
const contatoStatus = document.querySelector("#contato-status");
let leadAtual = null;
let pesquisaAtual = null;

function mostrarDuplicada(criada) {
  const badge = document.querySelector("#res-duplicada");
  if (criada.duplicada) {
    badge.hidden = false;
    badge.textContent = "⚠ Pesquisa duplicada — esta marca já foi pesquisada para este contato.";
  } else {
    badge.hidden = true;
  }
}

function itemContato(contato) {
  const li = document.createElement("li");
  const cabecalho = document.createElement("strong");
  const data = new Date(contato.criado_em).toLocaleString("pt-BR");
  cabecalho.textContent = `${contato.canal}${contato.resultado ? ` · ${contato.resultado}` : ""}`;
  const meta = document.createElement("small");
  meta.textContent = `${data}${contato.operador ? ` · ${contato.operador}` : ""}`;
  li.append(cabecalho, meta);
  if (contato.observacao) {
    li.append(paragrafo(contato.observacao));
  }
  return li;
}

async function carregarContatos(leadId) {
  const dados = await api(`/v1/admin/leads/${leadId}/contatos`);
  const lista = document.querySelector("#contatos-lista");
  lista.replaceChildren(...dados.contatos.map(itemContato));
  if (!dados.contatos.length) {
    lista.replaceChildren(paragrafo("Nenhum contato registrado ainda."));
  }
}

async function abrirContatos(leadId) {
  leadAtual = leadId;
  if (!leadId) {
    contatosSection.hidden = true;
    return;
  }
  contatosSection.hidden = false;
  try {
    await carregarContatos(leadId);
  } catch (error) {
    contatoStatus.hidden = false;
    contatoStatus.textContent = error.message;
    contatoStatus.className = "status-message error";
  }
}

contatoForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!leadAtual) return;
  const dados = Object.fromEntries(new FormData(contatoForm));
  dados.pesquisa_id = pesquisaAtual;
  try {
    await api(`/v1/admin/leads/${leadAtual}/contatos`, { method: "POST", body: JSON.stringify(dados) });
    contatoForm.reset();
    contatoStatus.hidden = false;
    contatoStatus.textContent = "Contato registrado.";
    contatoStatus.className = "status-message success";
    await carregarContatos(leadAtual);
  } catch (error) {
    contatoStatus.hidden = false;
    contatoStatus.textContent = error.message;
    contatoStatus.className = "status-message error";
  }
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(form));
  dados.atividade = dados.atividade.trim() || null;
  dados.classe_nice = dados.classe_nice || null;
  setStatus("Registrando consulta…", "loading");
  try {
    const criada = await api("/v1/admin/consulta", { method: "POST", body: JSON.stringify(dados) });
    pesquisaAtual = criada.id;
    exclusaoPendente = false;
    atualizarAcaoExclusao();
    history.pushState(null, "", criada.relatorio_url);
    mostrarDuplicada(criada);
    await carregarRelatorio(criada.id);
    await abrirContatos(criada.lead_id);
  } catch (error) {
    setStatus(error.message, "error");
  }
});

const deepLink = location.pathname.match(/\/admin\/consulta\/(.+)$/);
if (deepLink) {
  pesquisaAtual = decodeURIComponent(deepLink[1]);
  Promise.all([
    carregarRelatorio(pesquisaAtual),
    api(`/v1/admin/consulta/${encodeURIComponent(pesquisaAtual)}/contexto`),
  ]).then(([, contexto]) => {
    mostrarDuplicada(contexto);
    exclusaoPendente = contexto.exclusao_status === "pendente";
    podeExcluirPesquisa = Boolean(contexto.pode_excluir);
    atualizarAcaoExclusao();
    return abrirContatos(contexto.lead_id);
  }).catch(error => setStatus(error.message, "error"));
}

function atualizarAcaoExclusao() {
  deleteButton.disabled = exclusaoPendente || !pesquisaAtual;
  deleteButton.textContent = exclusaoPendente
    ? "Exclusão aguardando aprovação"
    : podeExcluirPesquisa ? "Excluir pesquisa" : "Solicitar exclusão";
}

deleteButton.addEventListener("click", () => {
  if (!pesquisaAtual || exclusaoPendente) return;
  deleteForm.reset();
  document.querySelector("#delete-research-password-field").hidden = !podeExcluirPesquisa;
  deleteForm.elements.senha.required = podeExcluirPesquisa;
  document.querySelector("#delete-research-title").textContent = podeExcluirPesquisa
    ? "Excluir pesquisa permanentemente?" : "Solicitar exclusão da pesquisa?";
  document.querySelector("#delete-research-description").textContent = podeExcluirPesquisa
    ? "Relatório, análises e previsões vinculadas serão removidos. Confirme com sua senha atual."
    : "A pesquisa permanecerá disponível até um administrador analisar a solicitação.";
  document.querySelector("#delete-research-message").hidden = true;
  deleteDialog.showModal();
});

document.querySelector("#cancel-delete-research").addEventListener("click", () => {
  deleteDialog.close();
});

deleteForm.addEventListener("submit", async event => {
  event.preventDefault();
  const mensagem = document.querySelector("#delete-research-message");
  const dados = Object.fromEntries(new FormData(deleteForm));
  mensagem.hidden = false;
  mensagem.className = "status-message loading";
  mensagem.textContent = podeExcluirPesquisa ? "Excluindo pesquisa…" : "Enviando solicitação…";
  try {
    if (podeExcluirPesquisa) {
      await api(`/v1/admin/pesquisas/${encodeURIComponent(pesquisaAtual)}`, {
        method: "DELETE",
        body: JSON.stringify({ senha: dados.senha, motivo: dados.motivo }),
      });
      deleteDialog.close();
      location.href = "/admin/consulta";
      return;
    }
    await api(`/v1/admin/pesquisas/${encodeURIComponent(pesquisaAtual)}/solicitar-exclusao`, {
      method: "POST",
      body: JSON.stringify({ motivo: dados.motivo }),
    });
    exclusaoPendente = true;
    atualizarAcaoExclusao();
    mensagem.className = "status-message success";
    mensagem.textContent = "Solicitação enviada ao administrador.";
  } catch (error) {
    mensagem.className = "status-message error";
    mensagem.textContent = error.message;
  }
});

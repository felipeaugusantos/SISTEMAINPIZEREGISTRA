const form = document.querySelector("#consulta-form");
const statusEl = document.querySelector("#consulta-status");
const resultado = document.querySelector("#resultado");
const deleteButton = document.querySelector("#delete-research");
const deleteDialog = document.querySelector("#delete-research-dialog");
const deleteForm = document.querySelector("#delete-research-form");
let podeExcluirPesquisa = false;
let exclusaoPendente = false;
let classesNice = [];
let reportRequest = 0;
// Restore the authenticated deep link when navigating through pushState entries.
window.addEventListener("popstate", () => location.reload());

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
function preencherClasses(select) {
  select.replaceChildren();
  for (const { codigo, titulo } of classesNice) {
    const option = document.createElement("option");
    option.value = codigo;
    option.textContent = `${codigo} — ${titulo}`;
    select.append(option);
  }
  window.ZeNiceSelector?.enhance(select);
}

function atualizarMarcas() {
  const itens = [...document.querySelectorAll(".consulta-marca-item")];
  itens.forEach((item, indice) => {
    item.querySelector("header strong").textContent = `Marca ${indice + 1}`;
    item.querySelector(".remover-marca").hidden = itens.length === 1;
  });
  document.querySelector("#adicionar-marca").disabled = itens.length >= 20;
}

function adicionarMarca() {
  const container = document.querySelector("#consulta-marcas");
  if (container.children.length >= 20) return;
  const item = document.createElement("article");
  item.className = "consulta-marca-item";
  item.innerHTML = `
    <header><strong></strong><button class="secondary-button remover-marca" type="button">Remover</button></header>
    <div class="user-fields">
      <label class="field-full">Marca<input name="marca" data-field="marca" required minlength="2" maxlength="200" placeholder="Ex.: CAFÉ DO BRASIL"></label>
      <label class="field-full">Atividade do negócio <small>(opcional)</small><input name="atividade" data-field="atividade" maxlength="500" placeholder="Ex.: cafeteria e torrefação"></label>
      <label class="field-full">Classes Nice pretendidas <small>(opcional) — segure Ctrl/Cmd para marcar mais de uma; nenhuma selecionada pesquisa em todas.</small><select name="classes_nice" data-field="classes_nice" multiple size="6"></select></label>
    </div>`;
  preencherClasses(item.querySelector('[data-field="classes_nice"]'));
  container.append(item);
  atualizarMarcas();
  item.querySelector('[data-field="marca"]').focus();
}

document.querySelector("#consulta-marcas").addEventListener("click", event => {
  const remover = event.target.closest(".remover-marca");
  if (!remover) return;
  remover.closest(".consulta-marca-item").remove();
  atualizarMarcas();
});
document.querySelector("#adicionar-marca").addEventListener("click", adicionarMarca);

api("/v1/admin/consulta/classes-nice").then(classes => {
  classesNice = classes;
  document.querySelectorAll('[data-field="classes_nice"]').forEach(preencherClasses);
}).catch(() => setStatus("Não foi possível carregar as classes Nice. Atualize a página para selecionar classes específicas.", "error"));
atualizarMarcas();

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

// IA em sombra (análise de marca): explicação em linguagem simples do
// risco já calculado -- gerada em segundo plano, pode ainda não existir
// (some a seção nesse caso, sem tentar gerar na hora).
async function renderExplicacaoIA(pesquisaId) {
  const box = document.querySelector("#res-explicacao-ia");
  if (!box) return;
  let explicacao;
  try {
    const r = await fetch(`/v1/admin/consulta/${encodeURIComponent(pesquisaId)}/explicacao-ia`);
    if (!r.ok) { if (pesquisaId === pesquisaAtual) box.hidden = true; return; }
    explicacao = await r.json();
  } catch { if (pesquisaId === pesquisaAtual) box.hidden = true; return; }
  if (pesquisaId !== pesquisaAtual) return;
  if (explicacao.erro || explicacao.status !== "pendente") { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<p class="eyebrow">Explicação da IA · tradução do risco já calculado, revisão necessária</p>
    <p class="ia-explicacao-risco-texto">${escapeHtmlConsulta(explicacao.explicacao)}</p>
    <p class="ia-explicacao-risco-aviso">Não recalcula nem substitui a análise técnica -- só traduz o resultado para linguagem simples.</p>
    <div class="ia-explicacao-risco-acoes">
      <button type="button" class="secondary-button" data-revisar-explicacao="descartada">Descartar</button>
      <button type="button" class="primary-button" data-revisar-explicacao="aprovada">Marcar como revisada</button>
    </div>`;
  box.querySelectorAll("[data-revisar-explicacao]").forEach(botao => botao.addEventListener("click", async () => {
    box.querySelectorAll("button").forEach(b => { b.disabled = true; });
    try {
      await api(`/v1/admin/consulta/${encodeURIComponent(pesquisaId)}/explicacao-ia/${explicacao.id}/revisar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: botao.dataset.revisarExplicacao }),
      });
      box.hidden = true;
    } catch (error) {
      setStatus(error.message, "error");
      box.querySelectorAll("button").forEach(b => { b.disabled = false; });
    }
  }));
}

function escapeHtmlConsulta(v) { const s = document.createElement("span"); s.textContent = v ?? ""; return s.innerHTML; }

async function carregarRelatorio(id) {
  const request = ++reportRequest;
  resultado.hidden = true;
  document.querySelector("#res-explicacao-ia").hidden = true;
  setStatus("Gerando relatório…", "loading");
  try {
    const data = await api(`/v1/admin/consulta/${encodeURIComponent(id)}/relatorio`);
    if (request !== reportRequest) return;
    renderRelatorio(data);
    renderExplicacaoIA(id);
    setStatus("Consulta concluída.", "success");
  } catch (error) {
    if (request !== reportRequest) return;
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

// Achado da auditoria completa do CRM (06/09/2026, item 4): cada classe
// marcada vira sua própria pesquisa -- renderiza abas para trocar entre elas
// sem sair da tela, todas da mesma consulta/oportunidade.
function renderOutrasClasses(itens) {
  const container = document.querySelector("#res-outras-classes");
  if (itens.length < 2) {
    container.hidden = true;
    container.replaceChildren();
    return;
  }
  container.hidden = false;
  container.replaceChildren(
    ...itens.map((item, indice) => {
      const botao = document.createElement("button");
      botao.type = "button";
      botao.className = "secondary-button";
      botao.textContent = `${item.marca || `Marca ${indice + 1}`} · ${item.classe_nice ? `NCL ${item.classe_nice}` : "todas as classes"}`;
      if (indice === 0) botao.classList.add("active");
      botao.addEventListener("click", async () => {
        container.querySelectorAll("button").forEach(outro => outro.classList.remove("active"));
        botao.classList.add("active");
        pesquisaAtual = item.id;
        history.pushState(null, "", item.relatorio_url);
        mostrarDuplicada(item);
        await carregarRelatorio(item.id);
      });
      return botao;
    }),
  );
}

// R08 (plano de correções 17/11/2026): validar combinações já no
// preenchimento (limite de 50 e marca+classe repetida), reaproveitar
// cliente existente com escolha explícita e revisar um resumo curto antes
// de pesquisar. O servidor continua revalidando tudo na gravação.
const marcasStatus = document.querySelector("#consulta-marcas-status");
function coletarMarcas() {
  return [...document.querySelectorAll(".consulta-marca-item")].map(item => ({
    marca: item.querySelector('[data-field="marca"]').value.trim(),
    atividade: item.querySelector('[data-field="atividade"]').value.trim() || null,
    classes_nice: [...item.querySelector('[data-field="classes_nice"]').selectedOptions].map(option => option.value),
  }));
}
function validarMarcas(mostrar = true) {
  const marcas = coletarMarcas();
  const erros = [];
  const combinacoes = marcas.reduce((total, item) => total + (item.classes_nice.length || 1), 0);
  const vistos = new Set();
  marcas.forEach((item, indice) => {
    const chave = `${item.marca.toLowerCase().replace(/\s+/g, " ")}::${item.classes_nice.length ? item.classes_nice.slice().sort().join(",") : "*"}`;
    if (item.marca && vistos.has(chave)) erros.push(`A marca ${indice + 1} repete uma combinação já informada.`);
    vistos.add(chave);
  });
  if (combinacoes > 50) erros.push(`Esta consulta soma ${combinacoes} combinações; o limite é 50. Reduza marcas ou classes.`);
  if (mostrar) {
    marcasStatus.hidden = !erros.length;
    marcasStatus.className = "status-message error";
    marcasStatus.textContent = erros.join(" ");
  }
  return { marcas, erros, combinacoes };
}
document.querySelector("#consulta-marcas").addEventListener("input", () => validarMarcas());
document.querySelector("#consulta-marcas").addEventListener("change", () => validarMarcas());

const clienteBusca = document.querySelector("#cliente-busca");
const clienteBuscaStatus = document.querySelector("#cliente-busca-status");
const clienteBuscaResultados = document.querySelector("#cliente-busca-resultados");
const clienteSelecionado = document.querySelector("#cliente-selecionado");
let buscaClienteVersion = 0, buscaClienteTimer;
function limparSelecaoCliente() { clienteSelecionado.hidden = true; clienteSelecionado.textContent = ""; }
clienteBusca.addEventListener("input", () => {
  clearTimeout(buscaClienteTimer);
  const version = ++buscaClienteVersion;
  limparSelecaoCliente();
  clienteBuscaResultados.replaceChildren();
  const termo = clienteBusca.value.trim();
  if (termo.length < 2) { clienteBuscaStatus.textContent = termo ? "Digite ao menos 2 caracteres." : ""; return; }
  clienteBuscaStatus.textContent = "Buscando clientes…";
  buscaClienteTimer = setTimeout(async () => {
    try {
      const params = new URLSearchParams({ busca: termo, limite: "10", ordenacao: "nome" });
      const data = await api(`/v1/admin/leads?${params}`);
      if (version !== buscaClienteVersion) return;
      clienteBuscaStatus.textContent = data.itens.length ? `${data.itens.length} resultado(s). Selecione para preencher os dados abaixo.` : "Nenhum cliente encontrado; preencha os dados abaixo para cadastrar um novo.";
      for (const lead of data.itens) {
        const li = document.createElement("li"), button = document.createElement("button");
        button.type = "button"; button.className = "secondary-button";
        button.textContent = `${lead.nome} · ${lead.empresa || "Sem empresa"} · ${lead.email || lead.telefone || "sem contato"}`;
        button.addEventListener("click", () => {
          form.elements.nome.value = lead.nome || "";
          form.elements.empresa.value = lead.empresa || "";
          form.elements.email.value = lead.email || "";
          form.elements.telefone.value = lead.telefone || "";
          if (checkboxSemEmail && lead.email) { checkboxSemEmail.checked = false; checkboxSemEmail.dispatchEvent(new Event("change")); }
          clienteSelecionado.hidden = false;
          clienteSelecionado.textContent = `Cliente selecionado: ${button.textContent}. Revise os dados antes de pesquisar.`;
          clienteBuscaResultados.replaceChildren();
          clienteBuscaStatus.textContent = "";
        });
        li.append(button); clienteBuscaResultados.append(li);
      }
    } catch (error) { if (version === buscaClienteVersion) clienteBuscaStatus.textContent = "Falha ao buscar. Digite novamente para tentar."; }
  }, 300);
});

const checkboxSemEmail = document.querySelector("#cliente-sem-email");
const campoEmail = document.querySelector("#campo-email input");
const telefoneObrigatorio = document.querySelector("#telefone-obrigatorio");
checkboxSemEmail?.addEventListener("change", () => {
  const semEmail = checkboxSemEmail.checked;
  campoEmail.required = !semEmail;
  campoEmail.disabled = semEmail;
  if (semEmail) campoEmail.value = "";
  telefoneObrigatorio.hidden = !semEmail;
});

const revisaoSection = document.querySelector("#consulta-revisao");
const revisaoResumo = document.querySelector("#consulta-revisao-resumo");
form.addEventListener("submit", event => {
  event.preventDefault();
  const { marcas, erros, combinacoes } = validarMarcas();
  if (!form.reportValidity()) return;
  if (erros.length) { marcasStatus.scrollIntoView({ block: "center" }); return; }
  const formData = new FormData(form);
  const semEmail = formData.get("cliente_sem_email") === "on";
  const cliente = semEmail
    ? `${formData.get("nome")} · ${formData.get("telefone") || "sem telefone"} (sem e-mail)`
    : `${formData.get("nome")} · ${formData.get("email")}`;
  revisaoResumo.replaceChildren();
  const lista = document.createElement("ul");
  const itemCliente = document.createElement("li");
  itemCliente.textContent = `Cliente: ${cliente}`;
  lista.append(itemCliente);
  marcas.forEach((item, indice) => {
    const li = document.createElement("li");
    li.textContent = `Marca ${indice + 1}: ${item.marca} — ${item.classes_nice.length ? `classes ${item.classes_nice.join(", ")}` : "todas as classes (1 consulta)"}`;
    lista.append(li);
  });
  const total = document.createElement("p");
  total.textContent = `Total: ${combinacoes} combinação(ões) de marca/classe. Nenhuma classe selecionada pesquisa em todas.`;
  revisaoResumo.append(lista, total);
  revisaoSection.hidden = false;
  revisaoSection.scrollIntoView({ block: "center" });
  document.querySelector("#confirmar-consulta").focus();
});
document.querySelector("#voltar-consulta").addEventListener("click", () => {
  revisaoSection.hidden = true;
  form.querySelector("button[type=submit]").focus();
});
document.querySelector("#confirmar-consulta").addEventListener("click", async () => {
  const button = document.querySelector("#confirmar-consulta");
  if (button.disabled) return;
  button.disabled = true;
  revisaoSection.hidden = true;
  const formData = new FormData(form);
  const semEmail = formData.get("cliente_sem_email") === "on";
  const dados = {
    nome: String(formData.get("nome") || "").trim(),
    empresa: String(formData.get("empresa") || "").trim() || null,
    email: semEmail ? null : String(formData.get("email") || "").trim(),
    telefone: String(formData.get("telefone") || "").trim(),
    cliente_sem_email: semEmail,
    marcas: coletarMarcas(),
  };
  setStatus("Registrando consulta…", "loading");
  try {
    const resultado = await api("/v1/admin/consulta", { method: "POST", body: JSON.stringify(dados) });
    const primeira = resultado.itens[0];
    pesquisaAtual = primeira.id;
    exclusaoPendente = false;
    atualizarAcaoExclusao();
    history.pushState(null, "", primeira.relatorio_url);
    mostrarDuplicada(primeira);
    renderOutrasClasses(resultado.itens);
    await carregarRelatorio(primeira.id);
    await abrirContatos(resultado.lead_id);
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
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

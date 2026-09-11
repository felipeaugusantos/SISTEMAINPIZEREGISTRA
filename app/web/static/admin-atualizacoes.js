const ITENS_POR_PAGINA = 8;
const updatesState = { items: [], todos: [], atualizacaoImplantadaId: null, pagina: 1 };
const message = document.querySelector("#updates-message");
const list = document.querySelector("#updates-list");
const dialog = document.querySelector("#updates-report-dialog");

const typeLabels = {
  critica: "Correção crítica",
  correcao: "Correção",
  funcionalidade: "Nova funcionalidade",
};

const moduleLabels = {
  plataforma: "Plataforma", infraestrutura: "Infraestrutura", seguranca: "Segurança",
  consulta: "Consulta", leads: "Leads", crm: "CRM", prospeccao: "Prospecção",
  processos_monitorados: "Processos monitorados", operacao_juridica: "Operação jurídica",
  financeiro: "Financeiro", validacao: "Validação", risco: "Risco", ia: "Inteligência",
  aprendizado: "Aprendizado", usuarios: "Usuários", rpi: "RPI", producao: "Produção",
  portal_cliente: "Portal do cliente", privacidade: "Privacidade",
};

function dateLabel(value) {
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "long" }).format(new Date(value));
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(label, action, id, className = "secondary-button") {
  const node = element("button", className, label);
  node.type = "button";
  node.dataset.action = action;
  node.dataset.id = id;
  return node;
}

function detailBlock(title, text) {
  const block = element("section", "updates-detail-block");
  block.append(element("h3", "", title), element("p", "", text));
  return block;
}

function renderItem(item, currentId) {
  const article = element("article", `update-card update-${item.classificacao}`);
  article.dataset.updateId = item.id;
  const header = element("header", "update-card-header");
  const heading = element("div", "");
  const meta = element("div", "update-meta");
  meta.append(element("span", `update-badge ${item.classificacao}`, typeLabels[item.classificacao]));
  if (item.id === currentId) meta.append(element("span", "update-badge current", "Em execução"));
  if (item.estado.confirmada_em) meta.append(element("span", "update-badge read", "Leitura confirmada"));
  heading.append(meta, element("h2", "", item.titulo), element("p", "update-version", `${item.versao} · Implantada em ${dateLabel(item.implantada_em)}`));
  header.append(heading);
  article.append(header);

  // Card recolhido por padrão mostra só o essencial (badges, título,
  // versão) -- impacto, módulos e detalhes técnicos ficam atrás do mesmo
  // toggle "Ver detalhes"/"Ocultar detalhes", pra manter a lista
  // escaneável mesmo com muitas versões publicadas (achado do usuário).
  const details = element("div", "update-details");
  details.hidden = true;
  details.append(detailBlock("Impacto para você", item.impacto_usuario));

  const modules = element("div", "update-modules");
  modules.append(element("strong", "", "Módulos afetados"));
  item.modulos_afetados.forEach((name) => modules.append(element("span", "", moduleLabels[name] || name)));
  details.append(modules);

  details.append(
    detailBlock("Problema identificado", item.problema),
    detailBlock("O que foi corrigido", item.correcao),
    detailBlock("Evidências resumidas", `${item.evidencias.aprovadas} aprovada(s), ${item.evidencias.falharam} com falha e ${item.evidencias.ignoradas} ignorada(s).`),
  );
  if (item.documentacao_url) {
    const documentation = element("a", "updates-doc-link", "Abrir documentação");
    documentation.href = item.documentacao_url;
    documentation.target = "_blank";
    documentation.rel = "noopener noreferrer";
    details.append(documentation);
  }
  article.append(details);

  if (item.estado.adiada_ate && !item.estado.confirmada_em) {
    article.append(element("p", "update-postponed", `Aviso adiado até ${dateLabel(item.estado.adiada_ate)}.`));
  }

  const actions = element("div", "update-actions");
  actions.append(button("Ver detalhes", "details", item.id));
  if (!item.estado.confirmada_em) {
    actions.append(button("Confirmar leitura", "confirm", item.id, item.leitura_obrigatoria ? "primary-button" : "secondary-button"));
  }
  if (item.pode_adiar && !item.estado.confirmada_em) actions.append(button("Adiar aviso", "postpone", item.id));
  actions.append(button("Reportar problema", "report", item.id));
  article.append(actions);
  return article;
}

function itensNoPeriodo(todos) {
  const form = document.querySelector("#updates-periodo-filtro");
  const de = form.elements.de.value;
  const ate = form.elements.ate.value;
  if (!de && !ate) return todos;
  return todos.filter((item) => {
    const dataImplantacao = item.implantada_em.slice(0, 10);
    if (de && dataImplantacao < de) return false;
    if (ate && dataImplantacao > ate) return false;
    return true;
  });
}

function renderPaginacao(total) {
  const nav = document.querySelector("#updates-pagination");
  if (total <= ITENS_POR_PAGINA) {
    nav.hidden = true;
    return;
  }
  nav.hidden = false;
  const inicio = (updatesState.pagina - 1) * ITENS_POR_PAGINA + 1;
  const fim = Math.min(updatesState.pagina * ITENS_POR_PAGINA, total);
  document.querySelector("#updates-page-summary").textContent = `${inicio}–${fim} de ${total}`;
  document.querySelector("#updates-page-prev").disabled = updatesState.pagina <= 1;
  document.querySelector("#updates-page-next").disabled = fim >= total;
}

function render() {
  const novidades = itensNoPeriodo(updatesState.todos);
  document.querySelector("#updates-summary").textContent = novidades.length === updatesState.todos.length
    ? `${updatesState.todos.length} atualização(ões) publicada(s)`
    : `${novidades.length} de ${updatesState.todos.length} atualização(ões) (período filtrado)`;
  updatesState.items = novidades;
  const totalPaginas = Math.max(1, Math.ceil(novidades.length / ITENS_POR_PAGINA));
  if (updatesState.pagina > totalPaginas) updatesState.pagina = totalPaginas;
  if (updatesState.pagina < 1) updatesState.pagina = 1;
  const inicio = (updatesState.pagina - 1) * ITENS_POR_PAGINA;
  const pagina = novidades.slice(inicio, inicio + ITENS_POR_PAGINA);
  list.replaceChildren();
  if (!updatesState.todos.length) list.append(element("p", "updates-empty", "Nenhuma atualização foi publicada ainda."));
  else if (!novidades.length) list.append(element("p", "updates-empty", "Nenhuma atualização publicada no período selecionado."));
  pagina.forEach((item) => list.append(renderItem(item, updatesState.atualizacaoImplantadaId)));
  renderPaginacao(novidades.length);
  message.textContent = "";
  message.className = "status-message";
}

document.querySelector("#updates-page-prev").addEventListener("click", () => {
  updatesState.pagina -= 1;
  render();
});
document.querySelector("#updates-page-next").addEventListener("click", () => {
  updatesState.pagina += 1;
  render();
});

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Não foi possível concluir a operação.");
  }
  return response.status === 204 ? null : response.json();
}

async function load() {
  const data = await api("/v1/admin/atualizacoes");
  document.querySelector("#updates-current-version").textContent = data.versao_implantada;
  updatesState.todos = data.novidades;
  updatesState.atualizacaoImplantadaId = data.atualizacao_implantada_id;
  render();
}

document.querySelector("#updates-periodo-filtro").addEventListener("submit", (event) => {
  event.preventDefault();
  updatesState.pagina = 1;
  render();
});
document.querySelector("#updates-periodo-limpar").addEventListener("click", () => {
  const form = document.querySelector("#updates-periodo-filtro");
  form.elements.de.value = "";
  form.elements.ate.value = "";
  updatesState.pagina = 1;
  render();
});

function showStatus(text, kind = "success") {
  message.textContent = text;
  message.className = `status-message ${kind}`;
}

list.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const item = updatesState.items.find((entry) => entry.id === Number(target.dataset.id));
  const card = target.closest(".update-card");
  try {
    if (target.dataset.action === "details") {
      const details = card.querySelector(".update-details");
      details.hidden = !details.hidden;
      target.textContent = details.hidden ? "Ver detalhes" : "Ocultar detalhes";
      return;
    }
    if (target.dataset.action === "confirm") {
      target.disabled = true;
      await api(`/v1/admin/atualizacoes/${item.id}/confirmar-leitura`, { method: "POST", body: JSON.stringify({ confirmar: true }) });
      await load();
      showStatus("Leitura confirmada.");
      return;
    }
    if (target.dataset.action === "postpone") {
      target.disabled = true;
      await api(`/v1/admin/atualizacoes/${item.id}/adiar`, { method: "POST", body: JSON.stringify({ dias: 7 }) });
      await load();
      showStatus("Aviso adiado por 7 dias.");
      return;
    }
    if (target.dataset.action === "report") openReport(item);
  } catch (error) {
    target.disabled = false;
    showStatus(error.message, "error");
  }
});

function openReport(item) {
  document.querySelector("#updates-report-version").value = item.id;
  document.querySelector("#updates-report-title").textContent = `${item.versao} — ${item.titulo}`;
  const select = document.querySelector("#updates-report-module");
  select.replaceChildren(new Option("Não sei informar", ""));
  item.modulos_afetados.forEach((name) => select.add(new Option(moduleLabels[name] || name, name)));
  document.querySelector("#updates-report-message").textContent = "";
  dialog.showModal();
}

document.querySelectorAll(".updates-close, .updates-cancel").forEach((botao) => {
  botao.addEventListener("click", () => botao.closest("dialog").close());
});
const TAMANHO_MAXIMO_ANEXO = 8 * 1024 * 1024;

function arquivoParaBase64(arquivo) {
  return new Promise((resolve, reject) => {
    const leitor = new FileReader();
    leitor.onload = () => resolve(String(leitor.result).split(",", 2)[1] || "");
    leitor.onerror = () => reject(new Error("Não foi possível ler o arquivo do anexo."));
    leitor.readAsDataURL(arquivo);
  });
}

document.querySelector("#updates-report-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  const reportMessage = document.querySelector("#updates-report-message");
  submit.disabled = true;
  try {
    const campoAnexo = document.querySelector("#updates-report-anexo");
    const arquivo = campoAnexo.files[0];
    let anexo = null;
    if (arquivo) {
      if (arquivo.size > TAMANHO_MAXIMO_ANEXO) throw new Error("Anexo maior que 8 MB.");
      anexo = { nome: arquivo.name, content_type: arquivo.type, conteudo_base64: await arquivoParaBase64(arquivo) };
    }
    await api(`/v1/admin/atualizacoes/${document.querySelector("#updates-report-version").value}/problemas`, {
      method: "POST",
      body: JSON.stringify({
        categoria: document.querySelector("#updates-report-category").value,
        modulo: document.querySelector("#updates-report-module").value || null,
        gravidade: document.querySelector("#updates-report-gravidade").value,
        descricao: document.querySelector("#updates-report-description").value,
        etapas_reproduzir: document.querySelector("#updates-report-etapas").value || null,
        resultado_esperado: document.querySelector("#updates-report-esperado").value || null,
        resultado_encontrado: document.querySelector("#updates-report-encontrado").value || null,
        anexo,
      }),
    });
    dialog.close();
    event.currentTarget.reset();
    showStatus("Problema reportado. A equipe poderá acompanhar o relato até a resolução.");
  } catch (error) {
    reportMessage.textContent = error.message;
    reportMessage.className = "status-message error";
  } finally {
    submit.disabled = false;
  }
});

load().catch((error) => showStatus(error.message, "error"));

// Fase 3 (auditoria): quantos usuários ainda não confirmaram cada versão
// publicada -- restrito ao departamento de Tech (production.view), por
// isso só mostra a seção depois de confirmar acesso em vez de exibir o
// formulário/tabela pra quem vai receber 403.
function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value ?? "";
  return node.innerHTML;
}

async function carregarPendenciasAuditoria() {
  const secao = document.querySelector("#updates-pendencias-section");
  const resposta = await fetch("/v1/admin/versoes-sistema/relatorio/pendencias");
  if (resposta.status === 403) { secao.hidden = true; return; }
  if (!resposta.ok) return;
  secao.hidden = false;
  const dados = await resposta.json();
  const linhas = document.querySelector("#updates-pendencias-rows");
  linhas.innerHTML = dados.itens.length
    ? dados.itens.map((item) => `<tr class="${item.critico ? "is-erro" : ""}">
        <td>${escapeHtml(item.versao)}</td>
        <td>${escapeHtml(item.titulo)}${item.critico ? " · <strong>crítico</strong>" : ""}</td>
        <td>${item.critico ? "Correção crítica" : "Não crítico"}</td>
        <td>${item.pendentes}</td>
        <td>${item.total_usuarios}</td>
      </tr>`).join("")
    : `<tr><td colspan="5">Nenhuma versão publicada ainda.</td></tr>`;
}

carregarPendenciasAuditoria();

// Auditoria (achado do usuário): "Reportar problema" gravava no banco,
// mas não existia nenhuma tela pra ver esses relatos -- caía num buraco
// negro. Restrito ao departamento de Tech, mesmo padrão da seção acima.
const STATUS_PROBLEMA_LABEL = { aberto: "Aberto", em_analise: "Em análise", resolvido: "Resolvido" };
const GRAVIDADE_LABEL = { baixa: "Baixa", media: "Média", alta: "Alta", critica: "Crítica" };

function dataHoraLabel(value) {
  return value ? new Date(value).toLocaleString("pt-BR") : "—";
}

async function carregarProblemas() {
  const secao = document.querySelector("#updates-problemas-section");
  const statusFiltro = document.querySelector("#updates-problemas-filtro").elements.status.value;
  const params = new URLSearchParams();
  if (statusFiltro) params.set("status", statusFiltro);
  const resposta = await fetch(`/v1/admin/atualizacoes/problemas?${params}`);
  if (resposta.status === 403) { secao.hidden = true; return; }
  if (!resposta.ok) return;
  secao.hidden = false;
  const dados = await resposta.json();
  const linhas = document.querySelector("#updates-problemas-rows");
  linhas.innerHTML = dados.itens.length
    ? dados.itens.map((item) => {
        const detalhesExtra = [
          item.etapas_reproduzir ? `<p><strong>Etapas para reproduzir:</strong><br>${escapeHtml(item.etapas_reproduzir).replace(/\n/g, "<br>")}</p>` : "",
          item.resultado_esperado ? `<p><strong>Resultado esperado:</strong> ${escapeHtml(item.resultado_esperado)}</p>` : "",
          item.resultado_encontrado ? `<p><strong>Resultado encontrado:</strong> ${escapeHtml(item.resultado_encontrado)}</p>` : "",
          item.anexo ? `<p><strong>Anexo:</strong> <a href="/v1/admin/atualizacoes/problemas/${item.id}/anexo" target="_blank" rel="noopener">${escapeHtml(item.anexo.nome)}</a> (${Math.round((item.anexo.tamanho || 0) / 1024)} KB)</p>` : "",
        ].filter(Boolean).join("");
        const LIMITE_DESCRICAO = 120;
        const descricaoLonga = item.descricao.length > LIMITE_DESCRICAO;
        const descricaoCurta = descricaoLonga ? `${escapeHtml(item.descricao.slice(0, LIMITE_DESCRICAO))}…` : escapeHtml(item.descricao);
        const descricaoCompletaBloco = descricaoLonga ? `<p>${escapeHtml(item.descricao)}</p>` : "";
        const temMaisDetalhes = Boolean(detalhesExtra || descricaoCompletaBloco);
        return `<tr class="${item.status === "aberto" ? "is-erro" : ""}">
        <td>${dataHoraLabel(item.criado_em)}</td>
        <td>${escapeHtml(item.versao)}</td>
        <td>${escapeHtml(item.organizacao_nome)}</td>
        <td>${escapeHtml(item.usuario_nome)}</td>
        <td>${escapeHtml(item.categoria)}</td>
        <td>${GRAVIDADE_LABEL[item.gravidade] || escapeHtml(item.gravidade)}</td>
        <td>${escapeHtml(item.modulo || "—")}</td>
        <td class="updates-problema-descricao">${descricaoCurta}${temMaisDetalhes ? `<details><summary>Mais detalhes</summary>${descricaoCompletaBloco}${detalhesExtra}</details>` : ""}</td>
        <td>
          <select data-problema-id="${item.id}">
            ${Object.entries(STATUS_PROBLEMA_LABEL).map(([valor, rotulo]) => `<option value="${valor}" ${valor === item.status ? "selected" : ""}>${rotulo}</option>`).join("")}
          </select>
        </td>
      </tr>`;
      }).join("")
    : `<tr><td colspan="9">Nenhum problema relatado com esse filtro.</td></tr>`;
  linhas.querySelectorAll("[data-problema-id]").forEach((select) => {
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await api(`/v1/admin/atualizacoes/problemas/${select.dataset.problemaId}`, {
          method: "PATCH",
          body: JSON.stringify({ status: select.value }),
        });
      } catch (error) {
        showStatus(error.message, "error");
      } finally {
        select.disabled = false;
        await carregarProblemas();
      }
    });
  });
}

document.querySelector("#updates-problemas-filtro").addEventListener("submit", (event) => {
  event.preventDefault();
  carregarProblemas();
});

carregarProblemas();

// Botao "Cadastrar atualizacao" (achado do usuario: nao existia jeito nenhum
// de publicar uma versao pela tela, so por script direto no banco). O
// formulario em si mora numa pagina separada (admin-atualizacoes-cadastro),
// restrita a superadmin no backend -- os nomes tecnicos dos campos desse
// formulario nao podem vazar pra este bundle, que qualquer usuario
// autenticado baixa (ver test_interface_nao_contem_campos_tecnicos_restritos).
async function habilitarCriacaoSeSuperadmin() {
  try {
    const usuario = await api("/v1/auth/me");
    document.querySelector("#updates-create-abrir").hidden = !usuario.superadmin;
  } catch {
    // Mantem o botao oculto se nao for possivel confirmar o perfil do usuario.
  }
}

habilitarCriacaoSeSuperadmin();

const updatesState = { items: [] };
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

  article.append(detailBlock("Impacto para você", item.impacto_usuario));

  const modules = element("div", "update-modules");
  modules.append(element("strong", "", "Módulos afetados"));
  item.modulos_afetados.forEach((name) => modules.append(element("span", "", moduleLabels[name] || name)));
  article.append(modules);

  const details = element("div", "update-details");
  details.hidden = true;
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
  if (item.leitura_obrigatoria && !item.estado.confirmada_em) {
    actions.append(button("Confirmar leitura", "confirm", item.id, "primary-button"));
  }
  if (item.pode_adiar && !item.estado.confirmada_em) actions.append(button("Adiar aviso", "postpone", item.id));
  actions.append(button("Reportar problema", "report", item.id));
  article.append(actions);
  return article;
}

function render(data) {
  document.querySelector("#updates-current-version").textContent = data.versao_implantada;
  document.querySelector("#updates-summary").textContent = `${data.novidades.length} atualização(ões) publicada(s)`;
  updatesState.items = data.novidades;
  list.replaceChildren();
  if (!data.novidades.length) list.append(element("p", "updates-empty", "Nenhuma atualização foi publicada ainda."));
  data.novidades.forEach((item) => list.append(renderItem(item, data.atualizacao_implantada_id)));
  message.textContent = "";
  message.className = "status-message";
}

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Não foi possível concluir a operação.");
  }
  return response.status === 204 ? null : response.json();
}

async function load() {
  render(await api("/v1/admin/atualizacoes"));
}

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

document.querySelector(".updates-close").addEventListener("click", () => dialog.close());
document.querySelector(".updates-cancel").addEventListener("click", () => dialog.close());
document.querySelector("#updates-report-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  const reportMessage = document.querySelector("#updates-report-message");
  submit.disabled = true;
  try {
    await api(`/v1/admin/atualizacoes/${document.querySelector("#updates-report-version").value}/problemas`, {
      method: "POST",
      body: JSON.stringify({
        categoria: document.querySelector("#updates-report-category").value,
        modulo: document.querySelector("#updates-report-module").value || null,
        descricao: document.querySelector("#updates-report-description").value,
      }),
    });
    dialog.close();
    event.currentTarget.reset();
    showStatus("Problema reportado. A equipe poderá acompanhar o relato com segurança.");
  } catch (error) {
    reportMessage.textContent = error.message;
    reportMessage.className = "status-message error";
  } finally {
    submit.disabled = false;
  }
});

load().catch((error) => showStatus(error.message, "error"));

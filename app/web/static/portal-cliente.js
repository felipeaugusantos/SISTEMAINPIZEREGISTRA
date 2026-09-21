const $ = (selector) => document.querySelector(selector);
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Não foi possível concluir a operação");
  return response.status === 204 ? null : response.json();
}
const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
function formatarDataJornada(valor) {
  if (!valor) return "";
  return new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo" }).format(new Date(valor));
}
// Item 1 do pedido de melhorias do cliente final (17/09/2026, revisado em
// 21/09/2026): a jornada era 10 passos comerciais lineares + um bloco à
// parte de acompanhamento do INPI, granular demais e redundante entre si.
// Agora é uma única jornada de até 5 macroetapas por marca/processo
// (app/api/portal_cliente.py::montar_macroetapas) -- nós condicionais
// (exigência, oposição, indeferimento) aparecem só como alerta na
// macroetapa corrente, sem virar um degrau de progresso à parte. Clicar
// num nó abre o drawer (#journey-drawer) com os sub-eventos daquela fase.
let ultimasJornadas = [];
let logoClienteUrl = null;
// Regra de dado nulo do pedido do usuário: uma macroetapa/sub-evento
// concluído sem data registrada mostra só o badge "Concluída", nunca o
// texto "data não registrada" (achado de UX do rótulo anterior).
function detalheSituacao(item) {
  if (item.ocorrido_em) return formatarDataJornada(item.ocorrido_em);
  if (item.situacao === "concluida" || item.situacao === "concluida_sem_data") return "Concluída";
  if (item.situacao === "atual") return "Etapa atual";
  return "Aguardando";
}
// Achado do usuário (21/09/2026): as bolinhas verdes da Jornada do Cliente
// deveriam usar o mesmo personagem foguete + logo do cliente já usado no
// cabeçalho do portal (#mascote-logo-cliente), não um círculo genérico.
function iconeJornada() {
  const logo = logoClienteUrl
    ? `<img class="portal-journey-logo" src="${esc(logoClienteUrl)}" alt="">`
    : "";
  return `<span class="portal-journey-icon"><img class="portal-journey-mascote" src="/static/assets/mascote-foguete.png?v=1" alt="">${logo}</span>`;
}
function macroJornada(bloco, indiceBloco) {
  const nos = bloco.macroetapas.map((macro) => {
    const classes = [`is-${esc(macro.situacao)}`, macro.alerta ? "has-alerta" : ""].filter(Boolean).join(" ");
    return `<li class="${classes}"><button class="portal-journey-node" type="button" data-bloco="${indiceBloco}" data-macro="${macro.indice}">${iconeJornada()}<span><strong>${esc(macro.titulo)}</strong><small>${esc(detalheSituacao(macro))}</small></span></button></li>`;
  }).join("");
  const atual = bloco.macroetapas.find((macro) => macro.situacao === "atual");
  const extras = atual?.alerta
    ? `<p class="portal-journey-alerta">⚠ ${esc(atual.alerta)}</p>`
    : atual?.previsao
      ? `<p class="portal-journey-previsao">${esc(atual.previsao)}</p>`
      : "";
  const titulo = bloco.marca ? `<p class="portal-journey-marca">${esc(bloco.marca)}</p>` : "";
  return `<div class="portal-journey-block">${titulo}<ol class="portal-journey">${nos}</ol>${extras}</div>`;
}
function abrirDrawerJornada(indiceBloco, indiceMacro) {
  const bloco = ultimasJornadas[indiceBloco];
  const macro = bloco?.macroetapas.find((item) => item.indice === indiceMacro);
  if (!macro) return;
  $("#journey-drawer-titulo").textContent = macro.titulo;
  $("#journey-drawer-situacao").textContent = detalheSituacao(macro);
  $("#journey-drawer-alerta").hidden = !macro.alerta;
  $("#journey-drawer-alerta").textContent = macro.alerta ? `⚠ ${macro.alerta}` : "";
  $("#journey-drawer-previsao").hidden = !macro.previsao;
  $("#journey-drawer-previsao").textContent = macro.previsao || "";
  const eventos = $("#journey-drawer-eventos");
  eventos.innerHTML = macro.sub_eventos?.length
    ? macro.sub_eventos.map((evento) => `<li><span>${esc(evento.label)}</span><span>${evento.documento_id ? `<a class="secondary-button" href="/v1/portal/documentos/${evento.documento_id}/download" target="_blank" rel="noopener">Baixar</a>` : `<small>${esc(detalheSituacao(evento))}</small>`}</span></li>`).join("")
    : "";
  $("#journey-drawer-vazio").hidden = Boolean(macro.sub_eventos?.length);
  $("#journey-drawer").showModal();
}
function showApp(data) {
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#hello").textContent = `Olá, ${data.cliente.nome}`;
  const mascoteLogo = $("#mascote-logo-cliente");
  logoClienteUrl = data.lead.logo_cliente_url || null;
  if (mascoteLogo) {
    if (data.lead.logo_cliente_url) { mascoteLogo.src = data.lead.logo_cliente_url; mascoteLogo.hidden = false; }
    else { mascoteLogo.hidden = true; mascoteLogo.removeAttribute("src"); }
  }
  const rows = (items, fields) => items?.length ? `<div class="portal-table">${items.map((item) => `<div class="portal-row">${fields.map((field) => `<span><strong>${esc(field[0])}</strong> ${esc(item[field[1]])}</span>`).join("")}</div>`).join("")}</div>` : "<p>Nenhum registro.</p>";
  ultimasJornadas = data.jornadas || [];
  $("#summary").innerHTML = `<p><strong>Marca:</strong> ${esc(data.lead.marca)} · <strong>Fase:</strong> ${esc(data.lead.fase)}</p><h3>Jornada do Cliente</h3>${ultimasJornadas.map(macroJornada).join("")}<h3>Propostas</h3>${data.propostas?.map((item) => { const podeAssinar = ["enviada", "visualizada", "aceita"].includes(item.status); const acao = item.status === "aceita" ? "<span>Assinada</span>" : podeAssinar ? `<button class="secondary-button" data-assinar-proposta="${item.id}" type="button">Assinar proposta</button>` : "<span class=\"portal-pending-status\">Aguardando envio</span>"; return `<div class="portal-row"><span><strong>${esc(item.numero)}</strong> · ${esc(item.status)}</span>${acao}</div>`; }).join("") || "<p>Nenhuma proposta.</p>"}<h3>Documentos e GRUs</h3>${data.documentos?.map((item) => `<div class="portal-row"><span><strong>${esc(item.tipo)}</strong> · ${esc(item.status)} · v${esc(item.versao)}</span><span>${item.tem_arquivo ? `<a class="secondary-button" href="/v1/portal/documentos/${item.id}/download" target="_blank" rel="noopener">Baixar</a>` : ""}${item.status !== "assinado" && !item.assinado_em ? `<button class="secondary-button" data-assinar-documento="${item.id}" type="button">Assinar</button>` : "<span>Assinado</span>"}</span></div>`).join("") || "<p>Nenhum documento.</p>"}${rows(data.guias, [["GRU", "numero_gru"], ["Status", "status"], ["Vencimento", "vencimento"]])}<h3>Pagamentos</h3>${rows(data.pagamentos, [["Descrição", "descricao"], ["Status", "status"], ["Valor", "valor_total"]])}`;
  $("#summary").querySelectorAll(".portal-journey-node").forEach((botao) => {
    botao.addEventListener("click", () => abrirDrawerJornada(Number(botao.dataset.bloco), Number(botao.dataset.macro)));
  });
}
function formatarTamanho(bytes) {
  if (!bytes) return "0 KB";
  const kb = bytes / 1024;
  return kb < 1024 ? `${kb.toFixed(0)} KB` : `${(kb / 1024).toFixed(1)} MB`;
}
// Item 2 do pedido de melhorias do cliente final (17/09/2026): área de
// Identidade Visual por cliente. Só a equipe interna cadastra os materiais
// (app/api/portal_cliente.py::enviar_material_marca_admin) -- aqui o cliente
// só lista e baixa, sem formulário de envio (mesmo padrão de #sent-files,
// mas sem #file-form).
async function carregarMateriaisMarca() {
  const box = $("#brand-materials");
  if (!box) return;
  try {
    const dados = await api("/v1/portal/materiais-marca");
    box.innerHTML = dados.materiais?.length
      ? dados.materiais.map((item) => `<div class="portal-row"><span><strong>${esc(item.nome)}</strong>${item.descricao ? ` · ${esc(item.descricao)}` : ""} · ${formatarTamanho(item.tamanho)}</span><a class="secondary-button" href="/v1/portal/materiais-marca/${item.id}/download" target="_blank" rel="noopener">Baixar</a></div>`).join("")
      : "<p>Nenhum material disponível ainda.</p>";
  } catch { box.innerHTML = "<p>Não foi possível carregar os materiais da marca.</p>"; }
}
async function carregarArquivosEnviados() {
  const box = $("#sent-files");
  if (!box) return;
  try {
    const dados = await api("/v1/portal/arquivos");
    box.innerHTML = dados.arquivos?.length
      ? `<p class="portal-sent-files-title">Documentos já enviados</p>${dados.arquivos.map((item) => `<div class="portal-row"><span><strong>${esc(item.nome)}</strong> · ${formatarTamanho(item.tamanho)} · ${new Date(item.criado_em).toLocaleString("pt-BR")}</span></div>`).join("")}`
      : "";
  } catch { /* Falha ao listar não deve impedir o restante da tela. */ }
}
async function carregar() {
  try {
    await api("/v1/portal/me");
    showApp(await api("/v1/portal/resumo"));
    const mensagens = await api("/v1/portal/mensagens");
    $("#messages").innerHTML = mensagens.mensagens.map((item) => `<p><small>${new Date(item.criado_em).toLocaleString("pt-BR")}</small><br>${esc(item.mensagem)}</p>`).join("") || "<p>Nenhuma mensagem.</p>";
    await carregarArquivosEnviados();
    await carregarMateriaisMarca();
  } catch { $("#login").hidden = false; }
}
$("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await api("/v1/portal/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); await carregar(); } catch (error) { $("#login-error").textContent = error.message; } });
$("#recovery-form").addEventListener("submit", async (event) => { event.preventDefault(); const message = $("#recovery-message"); try { await api("/v1/portal/recuperacao/solicitar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); message.textContent = "Se a conta existir, enviaremos as instruções por e-mail."; } catch { message.textContent = "Não foi possível solicitar a recuperação."; } });
$("#logout").addEventListener("click", async (event) => { const button = event.currentTarget; button.disabled = true; button.textContent = "Saindo…"; try { await api("/v1/portal/logout", { method: "POST" }); } finally { location.replace("/portal"); } });
$("#close-journey-drawer").addEventListener("click", () => $("#journey-drawer").close());
$("#message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("#message-status");
  status.className = "status-message";
  status.textContent = "";
  try {
    await api("/v1/portal/mensagens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) });
    event.target.reset();
    await carregar();
  } catch (error) {
    status.className = "status-message error";
    status.textContent = error.message;
  }
});
$("#file-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("#file-status");
  const button = event.target.querySelector("button");
  status.className = "status-message";
  status.textContent = "Enviando…";
  button.disabled = true;
  try {
    const body = new FormData(event.target);
    await api("/v1/portal/arquivos", { method: "POST", body });
    event.target.reset();
    status.className = "status-message success";
    status.textContent = "Documento enviado com sucesso.";
    await carregarArquivosEnviados();
  } catch (error) {
    status.className = "status-message error";
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("#summary").addEventListener("click", async (event) => { const proposta = event.target.closest("[data-assinar-proposta]"); const documento = event.target.closest("[data-assinar-documento]"); if (!proposta && !documento) return; const tipo = proposta ? "propostas" : "documentos"; const id = (proposta || documento).dataset[proposta ? "assinarProposta" : "assinarDocumento"]; const original = event.target.textContent; event.target.disabled = true; event.target.textContent = "Processando…"; try { await api(`/v1/portal/${tipo}/${id}/assinar`, { method: "POST" }); await carregar(); } catch (error) { event.target.disabled = false; event.target.textContent = original; const aviso = document.createElement("small"); aviso.className = "portal-action-error"; aviso.textContent = error.message; event.target.after(aviso); setTimeout(() => aviso.remove(), 5000); } });
carregar();

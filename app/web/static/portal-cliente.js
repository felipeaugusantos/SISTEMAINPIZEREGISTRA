const $ = (selector) => document.querySelector(selector);
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Não foi possível concluir a operação");
  return response.status === 204 ? null : response.json();
}
const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
function showApp(data) {
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#hello").textContent = `Olá, ${data.cliente.nome}`;
  const rows = (items, fields) => items?.length ? `<div class="portal-table">${items.map((item) => `<div class="portal-row">${fields.map((field) => `<span><strong>${esc(field[0])}</strong> ${esc(item[field[1]])}</span>`).join("")}</div>`).join("")}</div>` : "<p>Nenhum registro.</p>";
  $("#summary").innerHTML = `<p><strong>Marca:</strong> ${esc(data.lead.marca)} · <strong>Fase:</strong> ${esc(data.lead.fase)}</p><h3>Processos</h3>${rows(data.processos, [["Número", "numero"], ["Situação", "situacao"]])}<h3>Propostas</h3>${data.propostas?.map((item) => { const podeAssinar = ["enviada", "visualizada", "aceita"].includes(item.status); const acao = item.status === "aceita" ? "<span>Assinada</span>" : podeAssinar ? `<button class="secondary-button" data-assinar-proposta="${item.id}" type="button">Assinar proposta</button>` : "<span class=\"portal-pending-status\">Aguardando envio</span>"; return `<div class="portal-row"><span><strong>${esc(item.numero)}</strong> · ${esc(item.status)}</span>${acao}</div>`; }).join("") || "<p>Nenhuma proposta.</p>"}<h3>Documentos e GRUs</h3>${data.documentos?.map((item) => `<div class="portal-row"><span><strong>${esc(item.tipo)}</strong> · ${esc(item.status)} · v${esc(item.versao)}</span>${item.status !== "assinado" && !item.assinado_em ? `<button class="secondary-button" data-assinar-documento="${item.id}" type="button">Assinar</button>` : "<span>Assinado</span>"}</div>`).join("") || "<p>Nenhum documento.</p>"}${rows(data.guias, [["GRU", "numero_gru"], ["Status", "status"], ["Vencimento", "vencimento"]])}<h3>Pagamentos</h3>${rows(data.pagamentos, [["Descrição", "descricao"], ["Status", "status"], ["Valor", "valor_total"]])}`;
}
function formatarTamanho(bytes) {
  if (!bytes) return "0 KB";
  const kb = bytes / 1024;
  return kb < 1024 ? `${kb.toFixed(0)} KB` : `${(kb / 1024).toFixed(1)} MB`;
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
  } catch { $("#login").hidden = false; }
}
$("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await api("/v1/portal/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); await carregar(); } catch (error) { $("#login-error").textContent = error.message; } });
$("#recovery-form").addEventListener("submit", async (event) => { event.preventDefault(); const message = $("#recovery-message"); try { await api("/v1/portal/recuperacao/solicitar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); message.textContent = "Se a conta existir, enviaremos as instruções por e-mail."; } catch { message.textContent = "Não foi possível solicitar a recuperação."; } });
$("#logout").addEventListener("click", async (event) => { const button = event.currentTarget; button.disabled = true; button.textContent = "Saindo…"; try { await api("/v1/portal/logout", { method: "POST" }); } finally { location.replace("/portal"); } });
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

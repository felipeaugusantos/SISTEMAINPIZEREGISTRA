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
  $("#summary").innerHTML = `<p><strong>Marca:</strong> ${esc(data.lead.marca)} · <strong>Fase:</strong> ${esc(data.lead.fase)}</p><h3>Processos</h3>${rows(data.processos, [["Número", "numero"], ["Situação", "situacao"]])}<h3>Propostas</h3>${data.propostas?.map((item) => `<div class="portal-row"><span><strong>${esc(item.numero)}</strong> · ${esc(item.status)}</span>${item.status !== "aceita" ? `<button class="secondary-button" data-assinar-proposta="${item.id}" type="button">Assinar proposta</button>` : "<span>Assinada</span>"}</div>`).join("") || "<p>Nenhuma proposta.</p>"}<h3>Documentos e GRUs</h3>${data.documentos?.map((item) => `<div class="portal-row"><span><strong>${esc(item.tipo)}</strong> · ${esc(item.status)} · v${esc(item.versao)}</span>${item.status !== "assinado" && !item.assinado_em ? `<button class="secondary-button" data-assinar-documento="${item.id}" type="button">Assinar</button>` : "<span>Assinado</span>"}</div>`).join("") || "<p>Nenhum documento.</p>"}${rows(data.guias, [["GRU", "numero_gru"], ["Status", "status"], ["Vencimento", "vencimento"]])}<h3>Pagamentos</h3>${rows(data.pagamentos, [["Descrição", "descricao"], ["Status", "status"], ["Valor", "valor_total"]])}`;
}
async function carregar() {
  try {
    await api("/v1/portal/me");
    showApp(await api("/v1/portal/resumo"));
    const mensagens = await api("/v1/portal/mensagens");
    $("#messages").innerHTML = mensagens.mensagens.map((item) => `<p><small>${new Date(item.criado_em).toLocaleString("pt-BR")}</small><br>${esc(item.mensagem)}</p>`).join("") || "<p>Nenhuma mensagem.</p>";
  } catch { $("#login").hidden = false; }
}
$("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await api("/v1/portal/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); await carregar(); } catch (error) { $("#login-error").textContent = error.message; } });
$("#recovery-form").addEventListener("submit", async (event) => { event.preventDefault(); const message = $("#recovery-message"); try { await api("/v1/portal/recuperacao/solicitar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); message.textContent = "Se a conta existir, enviaremos as instruções por e-mail."; } catch { message.textContent = "Não foi possível solicitar a recuperação."; } });
$("#logout").addEventListener("click", async () => { await api("/v1/portal/logout", { method: "POST" }); location.reload(); });
$("#message-form").addEventListener("submit", async (event) => { event.preventDefault(); await api("/v1/portal/mensagens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); event.target.reset(); await carregar(); });
$("#file-form").addEventListener("submit", async (event) => { event.preventDefault(); const body = new FormData(event.target); await api("/v1/portal/arquivos", { method: "POST", body }); event.target.reset(); await carregar(); });
$("#summary").addEventListener("click", async (event) => { const proposta = event.target.closest("[data-assinar-proposta]"); const documento = event.target.closest("[data-assinar-documento]"); if (!proposta && !documento) return; const tipo = proposta ? "propostas" : "documentos"; const id = (proposta || documento).dataset[proposta ? "assinarProposta" : "assinarDocumento"]; await api(`/v1/portal/${tipo}/${id}/assinar`, { method: "POST" }); await carregar(); });
carregar();

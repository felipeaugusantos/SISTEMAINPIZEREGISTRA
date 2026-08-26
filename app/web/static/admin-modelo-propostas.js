const form = document.querySelector("#proposal-config-form");
const message = document.querySelector("#proposal-config-message");
const api = async (url, options = {}) => { options.headers = { ...(options.headers || {}), "Content-Type": "application/json" }; const response = await fetch(url, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || "Não foi possível concluir a operação."); return data; };
const defaults = { titulo: "PROPOSTA DE REGISTRO DE MARCA", escopo_padrao: "Registro de marca no INPI", prazo_texto: "Após o aceite, confirmação do pagamento e recebimento integral dos documentos, o protocolo será realizado em até 24 horas úteis, salvo pendências ou indisponibilidade dos sistemas oficiais do INPI.", condicoes_texto: "O protocolo não representa garantia de concessão. A decisão final pertence ao INPI. A pesquisa e a análise são indicativas e não substituem exame oficial ou análise jurídica especializada.", rodape: "Esta proposta foi gerada pelo Zé Registra e possui versão auditável no sistema." };
document.querySelector('#proposal-field-palette [data-token="{{empresa.cnpj}}"]')?.remove();
function preencher(data) { Object.entries({ ...defaults, ...data }).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value || ""; }); }
async function carregar() { try { preencher(await api("/v1/admin/configuracao/propostas")); } catch (error) { message.className = "status-message error"; message.textContent = error.message; } }
form.addEventListener("submit", async event => { event.preventDefault(); try { await api("/v1/admin/configuracao/propostas", { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(form))) }); message.className = "status-message success"; message.textContent = "Modelo salvo. Ele será usado em novas propostas e versões."; } catch (error) { message.className = "status-message error"; message.textContent = error.message; } });
document.querySelector("#proposal-config-reset").addEventListener("click", () => preencher(defaults));
document.querySelector("#proposal-template-form").addEventListener("submit", async event => { event.preventDefault(); const formData = new FormData(event.currentTarget); const notice = document.querySelector("#proposal-template-message"); try { const response = await fetch("/v1/admin/configuracao/propostas/template-pdf", { method: "POST", body: formData }); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || "Não foi possível importar o PDF."); notice.className = "status-message success"; notice.textContent = `Modelo importado. SHA-256: ${data.sha256}`; document.querySelector("#download-proposal-template").hidden = false; } catch (error) { notice.className = "status-message error"; notice.textContent = error.message; } });
let campoSelecionado = null;
const inserirCampo = (token, destino = null) => {
  const target = destino || campoSelecionado || document.querySelector("#proposal-config-form textarea:focus");
  if (!target || !token) return;
  const inicio = target.selectionStart ?? target.value.length;
  const fim = target.selectionEnd ?? inicio;
  target.value = `${target.value.slice(0, inicio)}${token}${target.value.slice(fim)}`;
  target.focus();
  target.selectionStart = target.selectionEnd = inicio + token.length;
  target.dispatchEvent(new Event("input", { bubbles: true }));
};
document.querySelectorAll("#proposal-config-form textarea").forEach(area => {
  area.addEventListener("focus", () => { campoSelecionado = area; });
  area.addEventListener("dragenter", event => { event.preventDefault(); area.classList.add("drop-target-active"); });
  area.addEventListener("dragover", event => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = "copy"; });
  area.addEventListener("dragleave", () => area.classList.remove("drop-target-active"));
  area.addEventListener("drop", event => {
    event.preventDefault();
    area.classList.remove("drop-target-active");
    inserirCampo(event.dataTransfer?.getData("text/plain") || "", area);
  });
});
document.querySelectorAll("#proposal-field-palette [data-token]").forEach(button => {
  button.addEventListener("dragstart", event => {
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("text/plain", button.dataset.token || "");
    }
    button.classList.add("dragging");
  });
  button.addEventListener("dragend", () => button.classList.remove("dragging"));
  button.addEventListener("click", () => inserirCampo(button.dataset.token));
});
carregar();

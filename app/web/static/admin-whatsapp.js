const msg = document.querySelector("#wa-message");
let podeGerir = false;
const esc = value => String(value ?? "—").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" }[c]));
const FINALIDADES = { vigilancia: "Vigilância", andamento: "Andamento", prospeccao: "Prospecção" };
const SITUACOES = {
  concedido: ["Concedido", "ok"], revogado: ["Revogado", "erro"],
  pendente: ["Pendente", "alerta"], enviando: ["Enviando", "alerta"], enviada: ["Enviada", "ok"],
  incerta: ["Incerta (conferir)", "alerta"], falhou: ["Falhou", "erro"], bloqueada: ["Bloqueada", "erro"], cancelada: ["Cancelada", ""],
};
const MOTIVOS = {
  sem_consentimento: "Sem consentimento registrado", consentimento_revogado: "Consentimento revogado",
  cancelada_manual: "Cancelada manualmente", finalidade_invalida: "Finalidade inválida",
};

function dataCurta(valor) {
  if (!valor) return "—";
  const data = new Date(valor);
  return Number.isNaN(data.getTime()) ? "—" : data.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}
function selo(situacao) {
  const [rotulo, classe] = SITUACOES[situacao] || [situacao, ""];
  return `<span class="wa-selo ${classe}">${esc(rotulo)}</span>`;
}
function item(rotulo, valor, estado = "") {
  return `<div class="wa-status-item"><span>${esc(rotulo)}</span><strong class="${estado}">${esc(valor)}</strong></div>`;
}
async function api(url, options = {}) {
  const resposta = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(typeof dados.detail === "string" ? dados.detail : "Não foi possível concluir a operação.");
  return dados;
}
function avisar(texto, tipo = "success") { msg.textContent = texto; msg.className = `status-message ${tipo}`; }

async function carregarStatus() {
  const dados = await api("/v1/admin/mensageria/status");
  const aviso = document.querySelector("#wa-aviso");
  if (!dados.ligado || !dados.provedor_configurado) {
    aviso.hidden = false;
    aviso.textContent = !dados.ligado
      ? "O envio por WhatsApp está desligado: nenhuma mensagem sai. Os registros abaixo já valem e serão respeitados quando o envio for ligado."
      : "O envio está ligado, mas ainda não há um gateway de WhatsApp configurado: as mensagens ficam na fila.";
  } else { aviso.hidden = true; }
  const hoje = dados.hoje || {};
  document.querySelector("#wa-status").innerHTML =
    item("Envio", dados.ligado ? "Ligado" : "Desligado", dados.ligado ? "ok" : "muted") +
    item("Gateway", dados.provedor_configurado ? "Configurado" : "Não configurado", dados.provedor_configurado ? "ok" : "muted") +
    item("Na fila", dados.pendentes) +
    item("Vigilância hoje", `${hoje.vigilancia?.enviadas ?? 0} de ${hoje.vigilancia?.limite ?? "—"}`) +
    item("Prospecção hoje", `${hoje.prospeccao?.enviadas ?? 0} de ${hoje.prospeccao?.limite ?? "—"}`);
}

async function carregarConsentimentos() {
  const params = new URLSearchParams();
  const finalidade = document.querySelector("#wa-filtro-finalidade").value;
  const status = document.querySelector("#wa-filtro-status").value;
  if (finalidade) params.set("finalidade", finalidade);
  if (status) params.set("status", status);
  const linhas = await api(`/v1/admin/mensageria/consentimentos?${params}`);
  document.querySelector("#wa-consentimentos").innerHTML = linhas.length ? linhas.map(c => `
    <tr>
      <td>${esc(FINALIDADES[c.finalidade] || c.finalidade)}</td>
      <td>${esc(c.telefone)}</td>
      <td>${selo(c.status)}</td>
      <td>${c.base_legal === "legitimo_interesse" ? "Legítimo interesse" : "Consentimento"}</td>
      <td>${esc(c.origem)}</td>
      <td>${dataCurta(c.status === "revogado" ? c.revogado_em : c.concedido_em)}</td>
      <td>${podeGerir && c.status === "concedido" ? `<button class="secondary-button" type="button" data-revogar-id="${esc(c.id)}">Revogar</button>` : ""}</td>
    </tr>`).join("") : '<tr><td colspan="7" class="wa-vazio">Nenhum registro.</td></tr>';
}

async function carregarFila() {
  const status = document.querySelector("#wa-filtro-fila").value;
  const linhas = await api(`/v1/admin/mensageria/fila${status ? `?status=${encodeURIComponent(status)}` : ""}`);
  document.querySelector("#wa-fila").innerHTML = linhas.length ? linhas.map(m => `
    <tr>
      <td>${esc(FINALIDADES[m.finalidade] || m.finalidade)}</td>
      <td>${esc(m.telefone)}</td>
      <td>${selo(m.status)}</td>
      <td>${esc(MOTIVOS[m.motivo_bloqueio] || m.ultimo_erro || m.motivo_bloqueio)}</td>
      <td>${esc(m.tentativas)}</td>
      <td>${dataCurta(m.agendada_para)}</td>
      <td>${esc(m.previa)}</td>
      <td>${podeGerir && m.status === "pendente" ? `<button class="secondary-button" type="button" data-cancelar="${esc(m.id)}">Cancelar</button>` : ""}</td>
    </tr>`).join("") : '<tr><td colspan="8" class="wa-vazio">A fila está vazia.</td></tr>';
}

async function recarregar() {
  try {
    await Promise.all([carregarStatus(), carregarConsentimentos(), carregarFila()]);
    avisar("Dados atualizados.");
  } catch (erro) { avisar(erro.message, "error"); }
}

const formConsentimento = document.querySelector("#wa-form-consentimento");
formConsentimento.elements.finalidade.addEventListener("change", () => {
  // Legítimo interesse só existe na prospecção; vigilância e andamento exigem consentimento.
  const prospeccao = formConsentimento.elements.finalidade.value === "prospeccao";
  document.querySelector("#wa-base-legal-rotulo").hidden = !prospeccao;
  if (!prospeccao) formConsentimento.elements.base_legal.value = "consentimento";
});
formConsentimento.addEventListener("submit", async evento => {
  evento.preventDefault();
  const saida = document.querySelector("#wa-form-msg");
  const f = formConsentimento.elements;
  const evidencia = f.evidencia.value.trim();
  saida.textContent = "Registrando…";
  try {
    await api("/v1/admin/mensageria/consentimentos", {
      method: "POST",
      body: JSON.stringify({
        finalidade: f.finalidade.value, telefone: f.telefone.value, origem: f.origem.value, base_legal: f.base_legal.value,
        evidencia: evidencia ? { descricao: evidencia } : {},
      }),
    });
    saida.textContent = "Consentimento registrado.";
    formConsentimento.reset();
    document.querySelector("#wa-base-legal-rotulo").hidden = true;
    await recarregar();
  } catch (erro) { saida.textContent = erro.message; }
});

document.querySelector("#wa-form-revogar").addEventListener("submit", async evento => {
  evento.preventDefault();
  const saida = document.querySelector("#wa-revogar-msg");
  const f = evento.currentTarget.elements;
  if (!confirm("Revogar este telefone? As mensagens pendentes dele serão canceladas.")) return;
  saida.textContent = "Salvando…";
  try {
    await api("/v1/admin/mensageria/consentimentos/revogar", {
      method: "POST", body: JSON.stringify({ finalidade: f.finalidade.value, telefone: f.telefone.value, origem: "atendimento" }),
    });
    saida.textContent = "Telefone revogado.";
    evento.currentTarget.reset();
    await recarregar();
  } catch (erro) { saida.textContent = erro.message; }
});

document.querySelector("#wa-consentimentos").addEventListener("click", async evento => {
  const botao = evento.target.closest("[data-revogar-id]");
  if (!botao) return;
  if (!confirm("Revogar este consentimento? As mensagens pendentes serão canceladas.")) return;
  try {
    // Pelo id do registro: quem não vê contatos completos só recebe o telefone mascarado.
    await api(`/v1/admin/mensageria/consentimentos/${encodeURIComponent(botao.dataset.revogarId)}/revogar`, { method: "POST" });
    await recarregar();
  } catch (erro) { avisar(erro.message, "error"); }
});

document.querySelector("#wa-fila").addEventListener("click", async evento => {
  const botao = evento.target.closest("[data-cancelar]");
  if (!botao) return;
  try {
    await api(`/v1/admin/mensageria/fila/${encodeURIComponent(botao.dataset.cancelar)}/cancelar`, { method: "POST" });
    await recarregar();
  } catch (erro) { avisar(erro.message, "error"); }
});

for (const id of ["#wa-filtro-finalidade", "#wa-filtro-status", "#wa-filtro-fila"]) {
  document.querySelector(id).addEventListener("change", () => recarregar());
}

// Quem só pode ler não vê os formulários nem os botões de alterar (os endpoints exigem leads.manage).
fetch("/v1/auth/me")
  .then(resposta => (resposta.ok ? resposta.json() : {}))
  .catch(() => ({}))
  .then(usuario => {
    const permissoes = usuario.permissoes || [];
    podeGerir = Boolean(usuario.superadmin || usuario.perfil === "administrador" || permissoes.includes("leads.manage"));
    document.querySelector("#wa-secao-registrar").hidden = !podeGerir;
    document.querySelector("#wa-secao-revogar").hidden = !podeGerir;
    return recarregar();
  });

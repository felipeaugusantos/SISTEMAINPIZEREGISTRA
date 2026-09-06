let regrasCanManage = false;
function regrasEsc(v) { const s = document.createElement("span"); s.textContent = v ?? ""; return s.innerHTML; }
function regrasMsg(text, kind = "success") { const m = document.querySelector("#regras-message"); m.hidden = false; m.textContent = text; m.className = `status-message ${kind}`; }

function regrasRender(itens) {
  document.querySelector("#regras-list").innerHTML = (itens || []).map(a => `
    <div class="crm-automacao" data-chave="${regrasEsc(a.chave)}">
      <label class="crm-auto-toggle"><input type="checkbox" class="auto-ativo" ${a.ativo ? "checked" : ""} ${regrasCanManage ? "" : "disabled"}><span><strong>${regrasEsc(a.label)}</strong><small>Cria a tarefa "${regrasEsc(a.titulo)}"</small></span></label>
      <label class="crm-auto-dias">Prazo <input type="number" class="auto-dias" min="0" max="180" value="${regrasEsc(String(a.dias))}" ${regrasCanManage ? "" : "disabled"}> dia(s)</label>
    </div>`).join("");
}

async function regrasLoad() {
  const [me, data] = await Promise.all([
    fetch("/v1/auth/me").then(r => r.json()),
    fetch("/v1/admin/crm/automacoes").then(r => r.json()),
  ]);
  regrasCanManage = me.superadmin || me.perfil === "administrador" || (me.permissoes || []).includes("leads.manage");
  regrasRender(data.itens || []);
}

document.querySelector("#regras-list").addEventListener("change", async event => {
  const row = event.target.closest(".crm-automacao");
  if (!row || !regrasCanManage) return;
  const payload = { ativo: row.querySelector(".auto-ativo").checked, dias: Number(row.querySelector(".auto-dias").value) || 0 };
  const r = await fetch(`/v1/admin/crm/automacoes/${row.dataset.chave}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  regrasMsg(r.ok ? "Automação atualizada." : "Erro ao salvar.", r.ok ? "success" : "error");
});

const politicaForm = document.querySelector("#politica-form");
function politicaMsg(text, kind = "success") { const m = document.querySelector("#politica-message"); m.hidden = false; m.textContent = text; m.className = `status-message ${kind}`; }
function politicaFill(politica) {
  politicaForm.elements.exigir_responsavel.checked = !!politica.exigir_responsavel;
  politicaForm.elements.atribuir_ao_operador.checked = !!politica.atribuir_ao_operador;
  politicaForm.elements.distribuicao_automatica_ativa.checked = !!politica.distribuicao_automatica_ativa;
  politicaForm.elements.exigir_proxima_acao.checked = !!politica.exigir_proxima_acao;
  politicaForm.elements.dias_proxima_acao_padrao.value = politica.dias_proxima_acao_padrao ?? "";
}
async function politicaLoad() {
  const politica = await fetch("/v1/admin/crm/politica").then(r => r.json());
  politicaFill(politica);
  for (const campo of politicaForm.elements) campo.disabled = !regrasCanManage;
  politicaForm.querySelector("button[type=submit]").hidden = !regrasCanManage;
}
politicaForm?.addEventListener("submit", async event => {
  event.preventDefault();
  const dias = politicaForm.elements.dias_proxima_acao_padrao.value;
  const payload = {
    exigir_responsavel: politicaForm.elements.exigir_responsavel.checked,
    atribuir_ao_operador: politicaForm.elements.atribuir_ao_operador.checked,
    distribuicao_automatica_ativa: politicaForm.elements.distribuicao_automatica_ativa.checked,
    exigir_proxima_acao: politicaForm.elements.exigir_proxima_acao.checked,
    dias_proxima_acao_padrao: dias === "" ? null : Number(dias),
  };
  const r = await fetch("/v1/admin/crm/politica", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (r.ok) politicaFill(await r.json());
  politicaMsg(r.ok ? "Política salva." : "Erro ao salvar.", r.ok ? "success" : "error");
});

regrasLoad().then(politicaLoad).catch(() => regrasMsg("Não foi possível carregar as regras.", "error"));

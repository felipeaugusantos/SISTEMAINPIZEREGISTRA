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

regrasLoad().catch(() => regrasMsg("Não foi possível carregar as regras.", "error"));

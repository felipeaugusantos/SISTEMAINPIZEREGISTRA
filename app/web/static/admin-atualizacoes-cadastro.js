const moduleLabels = {
  plataforma: "Plataforma", infraestrutura: "Infraestrutura", seguranca: "Segurança",
  consulta: "Consulta", leads: "Leads", crm: "CRM", prospeccao: "Prospecção",
  processos_monitorados: "Processos monitorados", operacao_juridica: "Operação jurídica",
  financeiro: "Financeiro", validacao: "Validação", risco: "Risco", ia: "Inteligência",
  aprendizado: "Aprendizado", usuarios: "Usuários", rpi: "RPI", producao: "Produção",
  portal_cliente: "Portal do cliente", privacidade: "Privacidade",
};

const RESULTADO_EVIDENCIA_LABEL = { aprovado: "Aprovado", falhou: "Falhou", ignorado: "Ignorado" };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Não foi possível concluir a operação.");
  }
  return response.status === 204 ? null : response.json();
}

const createForm = document.querySelector("#updates-create-form");
const createMessage = document.querySelector("#updates-create-message");

function novaLinhaEvidencia() {
  const linha = element("div", "updates-create-evidencia-row");
  linha.innerHTML = `
    <label><span>Nome do teste</span><input class="evidencia-nome" placeholder="ex.: pytest (suíte completa)" maxlength="150" required></label>
    <label><span>Resultado</span>
      <select class="evidencia-resultado">
        ${Object.entries(RESULTADO_EVIDENCIA_LABEL).map(([valor, rotulo]) => `<option value="${valor}">${rotulo}</option>`).join("")}
      </select>
    </label>
    <button class="secondary-button evidencia-remover" type="button" aria-label="Remover evidência">×</button>
    <textarea class="evidencia-resumo" rows="1" minlength="5" maxlength="500" placeholder="Resumo do resultado" required></textarea>
  `;
  linha.querySelector(".evidencia-remover").addEventListener("click", () => {
    if (document.querySelectorAll(".updates-create-evidencia-row").length > 1) linha.remove();
  });
  return linha;
}

document.querySelector("#updates-create-evidencia-add").addEventListener("click", () => {
  document.querySelector("#updates-create-evidencias-lista").append(novaLinhaEvidencia());
});

document.querySelector("#updates-create-modulos").innerHTML = Object.entries(moduleLabels)
  .map(([valor, rotulo]) => `<label><input type="checkbox" name="modulos_afetados" value="${valor}"> ${rotulo}</label>`)
  .join("");
document.querySelector("#updates-create-evidencias-lista").append(novaLinhaEvidencia());

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const publicar = event.submitter?.dataset.publicar === "true";
  createMessage.textContent = publicar ? "Salvando e publicando…" : "Salvando rascunho…";
  createMessage.className = "status-message loading";
  try {
    const dados = new FormData(createForm);
    const modulosSelecionados = Array.from(document.querySelectorAll('input[name="modulos_afetados"]:checked')).map((input) => input.value);
    if (!modulosSelecionados.length) throw new Error("Selecione ao menos um módulo afetado.");
    const evidencias = Array.from(document.querySelectorAll(".updates-create-evidencia-row")).map((linha) => ({
      nome: linha.querySelector(".evidencia-nome").value.trim(),
      resultado: linha.querySelector(".evidencia-resultado").value,
      resumo: linha.querySelector(".evidencia-resumo").value.trim(),
    }));
    if (evidencias.some((item) => !item.nome || !item.resumo)) throw new Error("Preencha nome e resumo de cada evidência de teste.");
    const riscos = String(dados.get("riscos_conhecidos") || "").split("\n").map((linha) => linha.trim()).filter(Boolean);
    const payload = {
      versao: dados.get("versao").trim(),
      titulo: dados.get("titulo").trim(),
      tipo_atualizacao: dados.get("tipo_atualizacao"),
      modulos_afetados: modulosSelecionados,
      problema_identificado: dados.get("problema_identificado").trim(),
      solucao_aplicada: dados.get("solucao_aplicada").trim(),
      impacto_usuario: dados.get("impacto_usuario").trim(),
      instrucoes: dados.get("instrucoes").trim(),
      plano_rollback: dados.get("plano_rollback").trim(),
      riscos_conhecidos: riscos,
      commit_sha: dados.get("commit_sha").trim().toLowerCase(),
      migration_revision: dados.get("migration_revision").trim() || null,
      documentacao_url: dados.get("documentacao_url").trim() || null,
      implantada_em: dados.get("implantada_em") || null,
      permite_adiar: dados.get("permite_adiar") === "on",
      evidencias_testes: evidencias,
    };
    const criada = await api("/v1/admin/versoes-sistema", { method: "POST", body: JSON.stringify(payload) });
    if (publicar) {
      await api(`/v1/admin/versoes-sistema/${criada.id}/publicar`, { method: "POST", body: JSON.stringify({ confirmar_publicacao: true }) });
    }
    location.href = "/admin/atualizacoes";
  } catch (error) {
    createMessage.textContent = error.message;
    createMessage.className = "status-message error";
  }
});

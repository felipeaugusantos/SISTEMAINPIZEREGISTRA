const form = document.querySelector("#consulta-form");
const statusEl = document.querySelector("#consulta-status");
const resultado = document.querySelector("#resultado");

function setStatus(texto, tipo) {
  statusEl.hidden = false;
  statusEl.textContent = texto;
  statusEl.className = `status-message ${tipo}`;
}

async function api(url, options = {}) {
  options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detalhe = Array.isArray(payload.detail)
      ? payload.detail.map(item => item.msg).join("; ")
      : payload.detail || `Falha na operação (${response.status})`;
    throw new Error(detalhe);
  }
  return payload;
}

function card(valor, rotulo) {
  const article = document.createElement("article");
  const strong = document.createElement("strong");
  strong.textContent = valor;
  const span = document.createElement("span");
  span.textContent = rotulo;
  article.append(strong, span);
  return article;
}

function paragrafo(texto, className) {
  const p = document.createElement("p");
  p.textContent = texto;
  if (className) p.className = className;
  return p;
}

function renderPrognostico(prog) {
  const box = document.querySelector("#res-prognostico");
  box.replaceChildren();
  if (!prog) return;
  const rotulos = { favoravel: "Favorável", atencao: "Atenção", desfavoravel: "Desfavorável" };
  const sec = document.createElement("section");
  sec.className = "report-classification prognostico";
  sec.dataset.veredito = prog.veredito;

  const cabecalho = document.createElement("div");
  const eyebrow = paragrafo("Prognóstico de registrabilidade", "eyebrow");
  const titulo = document.createElement("h2");
  titulo.textContent = prog.titulo;
  const tag = paragrafo(`Tendência: ${rotulos[prog.veredito] || prog.veredito}`, "prognostico-tag");
  tag.dataset.veredito = prog.veredito;
  cabecalho.append(eyebrow, titulo, tag);
  sec.append(cabecalho, paragrafo(prog.resumo));

  if (prog.motivos && prog.motivos.length) {
    sec.append(paragrafo("Motivos identificados:"));
    const ul = document.createElement("ul");
    ul.className = "prognostico-motivos";
    for (const motivo of prog.motivos) {
      const li = document.createElement("li");
      const forte = document.createElement("strong");
      forte.textContent = `${motivo.criterio}: `;
      li.append(forte, document.createTextNode(`${motivo.conclusao} (${motivo.referencia})`));
      ul.append(li);
    }
    sec.append(ul);
  }
  if (prog.pendencias && prog.pendencias.length) {
    sec.append(paragrafo(`Ainda dependem de avaliação: ${prog.pendencias.join(" · ")}.`));
  }
  sec.append(paragrafo(prog.ressalva, "matrix-status"));
  box.append(sec);
}

function renderRelatorio(data) {
  document.querySelector("#res-marca").textContent = data.marca;
  document.querySelector("#res-metrics").replaceChildren(
    card(data.total, "Ocorrências"),
    card(data.risco_nivel || "—", "Nível de risco"),
    card(data.ultima_rpi ? `RPI ${data.ultima_rpi}` : "—", "Base até"),
    card((data.classes_atividade || []).length, "Classes sugeridas"),
  );
  renderPrognostico(data.prognostico_registrabilidade);

  const conclusao = document.querySelector("#res-conclusao");
  conclusao.replaceChildren();
  if (data.conclusao) {
    conclusao.append(paragrafo("Conclusão indicativa", "eyebrow"));
    const h = document.createElement("h2");
    h.textContent = data.conclusao.titulo;
    conclusao.append(h, paragrafo(data.conclusao.resumo));
    if (data.conclusao.revisao_humana_recomendada) {
      conclusao.append(paragrafo("Revisão humana recomendada antes de qualquer decisão.", "matrix-status"));
    }
  }
  resultado.hidden = false;
  resultado.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function carregarRelatorio(id) {
  setStatus("Gerando relatório…", "loading");
  try {
    const data = await api(`/v1/admin/consulta/${encodeURIComponent(id)}/relatorio`);
    renderRelatorio(data);
    setStatus("Consulta concluída.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const dados = Object.fromEntries(new FormData(form));
  setStatus("Registrando consulta…", "loading");
  try {
    const criada = await api("/v1/admin/consulta", { method: "POST", body: JSON.stringify(dados) });
    history.pushState(null, "", criada.relatorio_url);
    await carregarRelatorio(criada.id);
  } catch (error) {
    setStatus(error.message, "error");
  }
});

const deepLink = location.pathname.match(/\/admin\/consulta\/(.+)$/);
if (deepLink) {
  carregarRelatorio(decodeURIComponent(deepLink[1]));
}

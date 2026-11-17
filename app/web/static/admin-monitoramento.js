// Monitoramento gerencial de erro e uso (16/11/2026): consome
// /v1/admin/monitoramento/* e renderiza resumo, série diária, uso por
// organização, top erros e os proxies de dificuldade de uso. Mesmo padrão
// da tela de Observabilidade (fetch direto, classes dash-*, SVG puro de
// observabilidade-graficos.js). Todo texto dinâmico passa por escape.
(function () {
  "use strict";

  const mensagem = document.querySelector("#monitoramento-message");
  const atualizadoAlvo = document.querySelector("#monitoramento-updated");
  const periodoAlvo = document.querySelector("#monitoramento-periodo");
  const autoAlvo = document.querySelector("#monitoramento-auto");

  function esc(valor) {
    return String(valor ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function numero(valor) {
    const n = Number(valor);
    return Number.isFinite(n) ? n : 0;
  }

  function percentual(valor) {
    return `${(numero(valor) * 100).toFixed(1).replace(".", ",")}%`;
  }

  function dataHora(valor) {
    return valor ? new Date(valor).toLocaleString("pt-BR") : "—";
  }

  function metric(valor, rotulo, detalhe = "") {
    return `<article><strong>${esc(valor)}</strong><span>${esc(rotulo)}</span>${detalhe ? `<small>${esc(detalhe)}</small>` : ""}</article>`;
  }

  function linhaVazia(colunas, texto) {
    return `<tr><td colspan="${colunas}">${esc(texto)}</td></tr>`;
  }

  async function api(url) {
    const resposta = await fetch(url);
    const corpo = await resposta.json().catch(() => ({}));
    if (!resposta.ok) throw new Error(corpo.detail || `Falha ao carregar (${resposta.status})`);
    return corpo;
  }

  function renderResumo(dados) {
    document.querySelector("#monitoramento-metrics").innerHTML = [
      metric(dados.acoes, "Ações", `${dados.usuarios_ativos} usuários ativos`),
      metric(dados.organizacoes_ativas, "Organizações ativas", `de ${dados.total_organizacoes_ativas} ativas no total`),
      metric(dados.organizacoes_sem_uso, "Organizações sem uso", "ativas sem ação no período"),
      metric(dados.erros, "Erros", `${percentual(dados.taxa_erros)} de ${dados.requisicoes} requisições`),
      metric(dados.acoes_com_falha, "Tentativas frustradas", `${percentual(dados.taxa_falha_acoes)} das ações`),
    ].join("");
  }

  function renderSerie(dados) {
    const G = window.ObsGraficos;
    if (!G) return;
    const rotulos = dados.pontos.map((ponto) => new Date(`${ponto.dia}T12:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }));
    document.querySelector("#monitoramento-grafico-uso").innerHTML = G.graficoBarras({
      rotulos,
      valores: dados.pontos.map((ponto) => ponto.acoes),
      erros: dados.pontos.map((ponto) => ponto.erros),
      titulo: `Ações dos usuários e erros por dia nos últimos ${dados.dias} dias`,
      descricao: "ações",
      descricaoErros: "erros",
    });
    document.querySelector("#monitoramento-grafico-frustradas").innerHTML = G.graficoBarras({
      rotulos,
      valores: dados.pontos.map((ponto) => ponto.frustradas),
      titulo: `Ações com falha por dia nos últimos ${dados.dias} dias`,
      descricao: "ações com falha",
    });
  }

  function renderOrganizacoes(dados) {
    const alvo = document.querySelector("#monitoramento-orgs-rows");
    alvo.innerHTML = dados.organizacoes.length
      ? dados.organizacoes.map((org) => `<tr>
          <td>${esc(org.nome)}</td>
          <td>${esc(org.acoes)}</td>
          <td>${esc(org.usuarios_ativos)}</td>
          <td>${esc(org.falhas)}</td>
          <td>${esc(percentual(org.taxa_falha))}</td>
          <td>${esc(dataHora(org.ultima_atividade))}</td>
        </tr>`).join("")
      : linhaVazia(6, "Nenhuma ação registrada no período.");
    const semUso = document.querySelector("#monitoramento-sem-uso-rows");
    semUso.innerHTML = dados.sem_uso.length
      ? dados.sem_uso.map((org) => `<tr><td>${esc(org.nome)}</td></tr>`).join("")
      : linhaVazia(1, "Todas as organizações ativas tiveram uso no período.");
  }

  function renderErros(dados) {
    const alvo = document.querySelector("#monitoramento-erros-rows");
    alvo.innerHTML = dados.erros.length
      ? dados.erros.map((erro) => `<tr>
          <td>${esc(erro.codigo_erro || "—")}</td>
          <td>${esc(erro.operacao)}</td>
          <td>${esc(erro.status_http)}</td>
          <td>${esc(erro.quantidade)}</td>
          <td>${esc(erro.duracao_media_ms)} ms</td>
          <td>${esc(dataHora(erro.ultima_ocorrencia))}</td>
        </tr>`).join("")
      : linhaVazia(6, "Nenhum erro no período.");
  }

  function renderDificuldade(dados) {
    const frustradas = document.querySelector("#monitoramento-frustradas-rows");
    frustradas.innerHTML = dados.tentativas_frustradas.length
      ? dados.tentativas_frustradas.map((item) => `<tr>
          <td>${esc(item.operacao)}</td>
          <td>${esc(item.status_http)}</td>
          <td>${esc(item.quantidade)}</td>
          <td>${esc(dataHora(item.ultima_ocorrencia))}</td>
        </tr>`).join("")
      : linhaVazia(4, "Nenhuma tentativa frustrada no período.");

    const usuarios = document.querySelector("#monitoramento-usuarios-rows");
    usuarios.innerHTML = dados.usuarios_com_falhas.length
      ? dados.usuarios_com_falhas.map((item) => `<tr>
          <td>${esc(item.ator)}</td>
          <td>${esc(item.organizacao || "—")}</td>
          <td>${esc(item.falhas)}</td>
          <td>${esc(item.recursos_distintos)}</td>
          <td>${esc(dataHora(item.ultima_ocorrencia))}</td>
        </tr>`).join("")
      : linhaVazia(5, "Nenhuma falha de usuário no período.");

    const travados = document.querySelector("#monitoramento-travados-rows");
    travados.innerHTML = dados.recursos_travados.length
      ? dados.recursos_travados.map((item) => `<tr>
          <td>${esc(item.ator)}</td>
          <td>${esc(item.recurso)}</td>
          <td>${esc(item.falhas)}</td>
          <td>${esc(dataHora(item.ultima_ocorrencia))}</td>
        </tr>`).join("")
      : linhaVazia(4, "Nenhum recurso com falhas repetidas pelo mesmo usuário.");

    const lentidao = document.querySelector("#monitoramento-lentidao-rows");
    lentidao.innerHTML = dados.lentidao.length
      ? dados.lentidao.map((item) => `<tr>
          <td>${esc(item.componente)}</td>
          <td>${esc(item.operacao)}</td>
          <td>${esc(item.requisicoes)}</td>
          <td>${esc(item.media_ms)}</td>
          <td>${esc(item.p95_ms)}</td>
          <td>${esc(item.max_ms)}</td>
        </tr>`).join("")
      : linhaVazia(6, "Sem dados de latência no período.");
  }

  async function carregar(silencioso = false) {
    if (!silencioso) {
      mensagem.hidden = false;
      mensagem.className = "status-message loading";
      mensagem.textContent = "Atualizando monitoramento…";
    }
    const dias = periodoAlvo.value;
    try {
      const [resumo, serie, organizacoes, erros, dificuldade] = await Promise.all([
        api(`/v1/admin/monitoramento/resumo?dias=${dias}`),
        api(`/v1/admin/monitoramento/serie?dias=${dias}`),
        api(`/v1/admin/monitoramento/uso-organizacoes?dias=${dias}`),
        api(`/v1/admin/monitoramento/top-erros?dias=${dias}`),
        api(`/v1/admin/monitoramento/dificuldade?dias=${dias}`),
      ]);
      renderResumo(resumo);
      renderSerie(serie);
      renderOrganizacoes(organizacoes);
      renderErros(erros);
      renderDificuldade(dificuldade);
      atualizadoAlvo.textContent = `Atualizado ${new Date(resumo.gerado_em).toLocaleTimeString("pt-BR")}`;
      mensagem.hidden = true;
    } catch (error) {
      mensagem.hidden = false;
      mensagem.className = "status-message error";
      mensagem.textContent = error.message;
    }
  }

  document.querySelector("#monitoramento-refresh").addEventListener("click", () => carregar(false));
  periodoAlvo.addEventListener("change", () => carregar(false));
  setInterval(() => { if (autoAlvo.checked) carregar(true); }, 60000);
  carregar(false);
})();

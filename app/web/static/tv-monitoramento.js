// Painel de TV (17/11/2026): Zé Registra e EZRA ERP lado a lado, só números agregados.
// Sem login: a chave de leitura vem no fragmento da URL (#t=...), que não vai para log do servidor,
// fica guardada neste navegador e some da barra de endereço. Atualiza sozinho; se algo falha, avisa na tela.
(function () {
  "use strict";

  const ATUALIZAR_MS = 30000;
  const VELHO_MS = 180000;
  const RECARREGAR_MS = 3600000;
  const CHAVE_LOCAL = "tv_monitoramento_chave";

  const alvoHora = document.querySelector("#tv-hora");
  const alvoAtualizado = document.querySelector("#tv-atualizado");
  const alvoAviso = document.querySelector("#tv-aviso");
  const alvoCentral = document.querySelector("#tv-zeregistra");
  const alvoErp = document.querySelector("#tv-erp");

  let ultimoSucesso = 0;
  let ultimoDado = null;
  let parado = false;

  function lerChave() {
    const m = /(?:^#|&)t=([^&]+)/.exec(window.location.hash || "");
    if (m) {
      try { window.localStorage.setItem(CHAVE_LOCAL, decodeURIComponent(m[1])); } catch (e) { /* sem armazenamento */ }
      window.history.replaceState(null, "", window.location.pathname);
    }
    try { return window.localStorage.getItem(CHAVE_LOCAL) || ""; } catch (e) { return ""; }
  }

  function el(tag, classe, texto) {
    const n = document.createElement(tag);
    if (classe) n.className = classe;
    if (texto !== undefined && texto !== null) n.textContent = String(texto);
    return n;
  }

  function num(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString("pt-BR") : "0";
  }

  function pct(v) {
    return `${(Number(v || 0) * 100).toFixed(1).replace(".", ",")}%`;
  }

  function hora(valor) {
    if (!valor) return "--:--";
    const d = new Date(valor);
    return Number.isNaN(d.getTime()) ? "--:--" : d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }

  function numero(valor, rotulo, nivel) {
    const caixa = el("div", `tv-numero${nivel ? ` ${nivel}` : ""}`);
    caixa.append(el("strong", "", valor), el("span", "", rotulo));
    return caixa;
  }

  function selo(saude, textoOverride) {
    const rotulos = { verde: "Saudável", amarelo: "Atenção", vermelho: "Crítico", cinza: "Sem dados" };
    return el("span", `tv-selo selo-${saude}`, textoOverride || rotulos[saude] || "");
  }

  function grafico(serie) {
    const NS = "http://www.w3.org/2000/svg";
    const W = 700, H = 200, base = 175, topo = 10;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");
    const pontos = Array.isArray(serie) ? serie : [];
    if (!pontos.length) return svg;
    const max = Math.max(1, ...pontos.map((p) => Number(p.acoes) || 0));
    const maxErros = Math.max(1, ...pontos.map((p) => Number(p.erros) || 0));
    const passo = W / pontos.length;
    pontos.forEach((p, i) => {
      const x = i * passo;
      const alturaAcoes = ((Number(p.acoes) || 0) / max) * (base - topo);
      const alturaErros = (Number(p.erros) || 0) > 0 ? Math.max(6, ((Number(p.erros) || 0) / maxErros) * (base - topo) * 0.6) : 0;
      const barra = document.createElementNS(NS, "rect");
      barra.setAttribute("class", "tv-barra-acao");
      barra.setAttribute("x", x + passo * 0.12);
      barra.setAttribute("width", passo * 0.76);
      barra.setAttribute("y", base - alturaAcoes);
      barra.setAttribute("height", alturaAcoes);
      svg.append(barra);
      if (alturaErros > 0) {
        const erro = document.createElementNS(NS, "rect");
        erro.setAttribute("class", "tv-barra-erro");
        erro.setAttribute("x", x + passo * 0.3);
        erro.setAttribute("width", passo * 0.4);
        erro.setAttribute("y", base - alturaErros);
        erro.setAttribute("height", alturaErros);
        svg.append(erro);
      }
      const rotulo = document.createElementNS(NS, "text");
      rotulo.setAttribute("class", "tv-eixo");
      rotulo.setAttribute("x", x + passo / 2);
      rotulo.setAttribute("y", 197);
      rotulo.setAttribute("text-anchor", "middle");
      const dia = String(p.dia || "");
      rotulo.textContent = dia.length >= 10 ? `${dia.slice(8, 10)}/${dia.slice(5, 7)}` : "";
      svg.append(rotulo);
    });
    return svg;
  }

  function topErros(lista, tipo) {
    const ul = el("ul", "tv-top");
    const itens = Array.isArray(lista) ? lista.slice(0, 3) : [];
    if (!itens.length) {
      ul.append(el("li", "", "Nenhum erro nas últimas 24 h"));
      return ul;
    }
    itens.forEach((item) => {
      const li = el("li");
      const nome = tipo === "central" ? `${item.status_http} ${item.operacao}` : `${item.rota || "(sem rota)"} · ${item.origem}`;
      li.append(el("span", "", nome), el("b", "", `${num(item.quantidade)}x`));
      ul.append(li);
    });
    return ul;
  }

  function montarCartao(alvo, titulo, saude, seloTexto, filhos, velho) {
    alvo.className = `tv-cartao saude-${saude}${velho ? " velho" : ""}`;
    alvo.replaceChildren();
    const cab = el("div", "tv-titulo");
    cab.append(el("h2", "", titulo), selo(saude, seloTexto));
    alvo.append(cab, ...filhos);
  }

  function cartaoCentral(c) {
    const taxa = Number(c.taxa_erros_24h || 0);
    montarCartao(alvoCentral, "Zé Registra", c.saude, "", [
      (() => {
        const g = el("div", "tv-numeros");
        g.append(
          numero(num(c.erros_5xx_1h), "erros de servidor (1 h)", c.erros_5xx_1h >= 10 ? "critico" : c.erros_5xx_1h >= 1 ? "alerta" : ""),
          numero(num(c.erros_24h), `erros em 24 h (${pct(taxa)})`, ""),
          numero(num(c.usuarios_ativos_24h), "usuários ativos (24 h)", ""),
          numero(num(c.acoes_24h), "ações (24 h)", ""),
        );
        return g;
      })(),
      el("p", "tv-sub", `${num(c.organizacoes_ativas_24h)} organizações com uso em 24 h · 7 dias: azul = ações, vermelho = erros (escala própria)`),
      Object.assign(el("div", "tv-grafico"), {}),
      topErros(c.top_erros, "central"),
    ], false);
    alvoCentral.querySelector(".tv-grafico").append(grafico(c.serie));
  }

  function cartaoErp(e) {
    if (e.status === "nao_configurado") {
      montarCartao(alvoErp, "EZRA ERP", "cinza", "Não configurado", [el("p", "tv-vazio", "Origem do ERP ainda não configurada")], true);
      return;
    }
    const d = e.dados;
    const fora = e.status !== "ok";
    if (!d) {
      montarCartao(alvoErp, "EZRA ERP", "cinza", "Indisponível", [
        el("p", "tv-vazio", `ERP indisponível${e.indisponivel_desde ? ` desde ${hora(e.indisponivel_desde)}` : ""}`),
      ], true);
      return;
    }
    const g = el("div", "tv-numeros");
    g.append(
      numero(num(d.erros_novos_24h), "erros novos (24 h)", d.erros_novos_1h >= 10 ? "critico" : d.erros_novos_1h >= 1 ? "alerta" : ""),
      numero(num(d.erros_abertos), "erros abertos", ""),
      numero(num(d.usuarios_ativos_24h), "usuários ativos (24 h)", ""),
      numero(num(d.acoes_24h), "ações (24 h)", ""),
    );
    const aviso = fora
      ? `ERP indisponível${e.indisponivel_desde ? ` desde ${hora(e.indisponivel_desde)}` : ""} · último dado ${hora(e.atualizado_em)}`
      : `${num(d.clientes_ativos_24h)} clientes com uso em 24 h · ${num(d.clientes_sem_uso_7d)} sem uso em 7 dias · azul = ações, vermelho = erros`;
    const graf = el("div", "tv-grafico");
    graf.append(grafico((d.serie || []).map((p) => ({ dia: p.dia, acoes: p.acoes, erros: p.erros }))));
    montarCartao(alvoErp, "EZRA ERP", fora ? "cinza" : d.saude, fora ? "Indisponível" : "", [g, el("p", "tv-sub", aviso), graf, topErros(d.top_erros, "erp")], fora);
  }

  function mostrarAviso(texto) {
    if (texto) { alvoAviso.textContent = texto; alvoAviso.hidden = false; } else { alvoAviso.hidden = true; }
  }

  function envelhecer() {
    const velho = ultimoSucesso && Date.now() - ultimoSucesso > VELHO_MS;
    alvoCentral.classList.toggle("velho", Boolean(velho));
    if (velho) alvoErp.classList.add("velho");
    alvoAtualizado.textContent = ultimoSucesso ? `atualizado às ${hora(ultimoSucesso)}` : "aguardando dados…";
  }

  async function atualizar() {
    if (parado) return;
    const chave = lerChave();
    if (!chave) {
      mostrarAviso("Sem chave de acesso. Abra o endereço completo do painel (com a chave) uma vez nesta TV.");
      parado = true;
      return;
    }
    try {
      const resposta = await fetch("/v1/monitoramento/tv", { headers: { Authorization: `Bearer ${chave}` }, cache: "no-store" });
      if (resposta.status === 401 || resposta.status === 404) {
        mostrarAviso("Chave do painel inválida ou revogada. Gere um novo endereço.");
        parado = true;
        return;
      }
      if (!resposta.ok) throw new Error(`status ${resposta.status}`);
      const dados = await resposta.json();
      ultimoDado = dados;
      ultimoSucesso = Date.now();
      cartaoCentral(dados.zeregistra);
      cartaoErp(dados.erp);
      mostrarAviso("");
    } catch (e) {
      mostrarAviso(ultimoSucesso
        ? `Sem conexão com a central desde ${hora(ultimoSucesso)}. Mostrando o último dado.`
        : "Sem conexão com a central.");
    }
    envelhecer();
  }

  function tiquetaque() {
    alvoHora.textContent = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    envelhecer();
  }

  tiquetaque();
  window.setInterval(tiquetaque, 1000);
  atualizar();
  window.setInterval(atualizar, ATUALIZAR_MS);
  window.setTimeout(() => window.location.reload(), RECARREGAR_MS);
})();

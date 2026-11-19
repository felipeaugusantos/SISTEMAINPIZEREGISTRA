// Painel de TV (17/11/2026): Zé Registra e EZRA ERP lado a lado, números agregados; a lista de
// clientes do ERP sem uso em 7 dias mostra nome e dias (aprovado pelo dono em 17/11/2026).
// Na TV, a chave de leitura vem no fragmento da URL (#t=...), que não vai para log do servidor,
// fica guardada neste navegador e some da barra de endereço. No computador, entra também pela
// sessão normal do Zé Registra (superadmin); sem as duas, vai para /login e volta após entrar.
// Atualiza sozinho; se algo falha, avisa na tela.
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

  function heroi(grande, rotuloGrande, numeros) {
    const h = el("div", "tv-heroi");
    const g = el("div", "tv-grande");
    g.append(el("strong", "", grande), el("span", "", rotuloGrande));
    const n = el("div", "tv-numeros");
    n.append(...numeros);
    h.append(g, n);
    return h;
  }

  function rotuloDia(p) {
    const dia = String((p && p.dia) || "");
    return dia.length >= 10 ? `${dia.slice(8, 10)}/${dia.slice(5, 7)}` : "";
  }

  function grafico(serie) {
    const NS = "http://www.w3.org/2000/svg";
    const W = 700, H = 200, topo = 8;
    const caixa = el("div", "tv-grafico");
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");
    const pontos = Array.isArray(serie) ? serie : [];
    for (let k = 0; k < 4; k += 1) {
      const linha = document.createElementNS(NS, "line");
      linha.setAttribute("class", "tv-grade-linha");
      linha.setAttribute("x1", 0);
      linha.setAttribute("x2", W);
      linha.setAttribute("y1", topo + ((H - topo) * k) / 3 - (k === 3 ? 1 : 0));
      linha.setAttribute("y2", topo + ((H - topo) * k) / 3 - (k === 3 ? 1 : 0));
      linha.setAttribute("vector-effect", "non-scaling-stroke");
      svg.append(linha);
    }
    caixa.append(svg);
    if (!pontos.length) return caixa;
    const max = Math.max(1, ...pontos.map((p) => Number(p.acoes) || 0)) * 1.08;
    const maxErros = Math.max(1, ...pontos.map((p) => Number(p.erros) || 0));
    const passo = W / pontos.length;
    const altura = H - topo;
    pontos.forEach((p, i) => {
      const hoje = i === pontos.length - 1;
      const x = i * passo;
      const alturaAcoes = Math.max(3, ((Number(p.acoes) || 0) / max) * altura);
      const barra = document.createElementNS(NS, "rect");
      barra.setAttribute("class", `tv-barra-acao${hoje ? " hoje" : ""}`);
      barra.setAttribute("x", x + passo * 0.22);
      barra.setAttribute("width", passo * 0.56);
      barra.setAttribute("y", H - alturaAcoes);
      barra.setAttribute("height", alturaAcoes);
      barra.setAttribute("rx", 6);
      svg.append(barra);
      const erros = Number(p.erros) || 0;
      if (erros > 0) {
        const alturaErros = Math.max(5, (erros / maxErros) * altura * 0.3);
        const erro = document.createElementNS(NS, "rect");
        erro.setAttribute("class", "tv-barra-erro");
        erro.setAttribute("x", x + passo * 0.38);
        erro.setAttribute("width", passo * 0.24);
        erro.setAttribute("y", H - alturaErros);
        erro.setAttribute("height", alturaErros);
        erro.setAttribute("rx", 4);
        svg.append(erro);
      }
    });
    const dias = el("div", "tv-dias");
    pontos.forEach((p, i) => dias.append(el("span", i === pontos.length - 1 ? "hoje" : "", rotuloDia(p))));
    caixa.append(dias);
    return caixa;
  }

  function topErros(lista, tipo) {
    const ul = el("ul", "tv-top");
    const itens = Array.isArray(lista) ? lista.slice(0, 3) : [];
    if (!itens.length) {
      ul.append(el("li", "tv-ok", "✓ Nenhum erro nas últimas 24 h"));
      return ul;
    }
    itens.forEach((item) => {
      const li = el("li");
      if (tipo === "central") {
        li.append(el("i", "", item.status_http), el("span", "", item.operacao));
      } else {
        li.append(el("span", "", `${item.rota || "(sem rota)"} · ${item.origem}`));
      }
      li.append(el("b", "", `${num(item.quantidade !== undefined ? item.quantidade : item.ocorrencias)}x`));
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

  function medidor(rotulo, leitura, detalhe) {
    const valido = leitura && typeof leitura.percentual === "number";
    const saude = valido ? leitura.saude : "cinza";
    const item = el("div", `tv-medidor saude-${saude}`);
    const topo = el("div", "tv-medidor-topo");
    topo.append(el("span", "", valido && detalhe ? `${rotulo} · ${detalhe}` : rotulo), el("strong", "", valido ? `${leitura.percentual}%` : "--"));
    const trilho = el("div", "tv-medidor-trilho");
    const barra = el("i", `tv-medidor-barra p${valido ? Math.min(100, Math.max(0, Math.round(leitura.percentual / 5) * 5)) : 0}`);
    trilho.append(barra);
    item.append(topo, trilho);
    return item;
  }

  function faixaSistema(s) {
    const d = s && typeof s === "object" ? s : {};
    const caixa = el("div", "tv-sistema");
    caixa.append(
      el("h3", "", "Servidor"),
      medidor("CPU", d.cpu, "5 min"),
      medidor("Memória", d.memoria, ""),
      medidor("Disco", d.disco, d.disco && typeof d.disco.livre_gb === "number" ? `${d.disco.livre_gb} GB livre` : ""),
    );
    return caixa;
  }

  function cartaoCentral(c, sistema) {
    const taxa = Number(c.taxa_erros_24h || 0);
    const nivel5xx = c.erros_5xx_1h >= 10 ? "critico" : c.erros_5xx_1h >= 1 ? "alerta" : "bom";
    montarCartao(alvoCentral, "Zé Registra", c.saude, "", [
      heroi(num(c.usuarios_ativos_24h), "usuários ativos (24 h)", [
        numero(num(c.erros_5xx_1h), "erros de servidor (1 h)", nivel5xx),
        numero(num(c.acoes_24h), "ações (24 h)", ""),
        numero(pct(taxa), `taxa de erros (${num(c.erros_24h)})`, ""),
      ]),
      el("p", "tv-legenda", `7 dias: ações (verde) e erros (vermelho) · ${num(c.organizacoes_ativas_24h)} organizações com uso hoje`),
      grafico(c.serie),
      topErros(c.top_erros, "central"),
      faixaSistema(sistema),
    ], false);
  }

  function listaSemUso(lista) {
    const itens = (Array.isArray(lista) ? lista : []).filter((i) => i && typeof i.nome === "string" && i.nome.trim());
    if (!itens.length) return null;
    const LIMITE = 6;
    const caixa = el("div", "tv-sem-uso");
    caixa.append(el("h3", "", `Sem uso em 7 dias (${num(itens.length)})`));
    const ul = el("ul", "");
    itens.slice(0, LIMITE).forEach((i) => {
      const dias = i.dias_sem_uso === null || i.dias_sem_uso === undefined ? null : Number(i.dias_sem_uso);
      const valido = dias !== null && Number.isFinite(dias) && dias >= 0;
      const li = el("li");
      li.append(
        el("span", "", i.nome.trim()),
        el("b", "", valido ? `há ${Math.floor(dias)} ${Math.floor(dias) === 1 ? "dia" : "dias"}` : "nunca usou"),
      );
      ul.append(li);
    });
    if (itens.length > LIMITE) ul.append(el("li", "tv-sem-uso-mais", `+ ${num(itens.length - LIMITE)} outros sem uso`));
    caixa.append(ul);
    return caixa;
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
    const nivelNovos = d.erros_novos_1h >= 10 ? "critico" : d.erros_novos_1h >= 1 ? "alerta" : "";
    const nivelAbertos = nivelNovos || (d.erros_abertos > 0 ? "alerta" : "bom");
    const legenda = fora
      ? `ERP indisponível${e.indisponivel_desde ? ` desde ${hora(e.indisponivel_desde)}` : ""} · último dado ${hora(e.atualizado_em)}`
      : `7 dias: ações (verde) e erros (vermelho) · ${num(d.clientes_sem_uso_7d)} clientes sem uso em 7 dias`;
    const filhos = [];
    if (!fora && typeof d.motivo === "string" && d.motivo.trim()) {
      filhos.push(el("p", "tv-motivo", d.motivo.trim()));
    }
    filhos.push(
      heroi(num(d.clientes_ativos_24h), "clientes com uso (24 h)", [
        numero(num(d.erros_abertos), "erros abertos", nivelAbertos),
        numero(num(d.usuarios_ativos_24h), "usuários ativos (24 h)", ""),
        numero(num(d.acoes_24h), "ações (24 h)", ""),
      ]),
      el("p", "tv-legenda", legenda),
    );
    if (!fora) {
      const parados = listaSemUso(d.clientes_sem_uso);
      if (parados) filhos.push(parados);
    }
    filhos.push(grafico((d.serie || []).map((p) => ({ dia: p.dia, acoes: p.acoes, erros: p.erros }))));
    if (!fora) filhos.push(topErros(d.top_erros, "erp"));
    montarCartao(alvoErp, "EZRA ERP", fora ? "cinza" : d.saude, fora ? "Indisponível" : "", filhos, fora);
  }

  function idade(valor) {
    const horas = (Date.now() - new Date(valor).getTime()) / 3600000;
    if (horas < 1) return "há menos de 1 h";
    if (horas < 48) return `há ${Math.floor(horas)} h`;
    return `há ${Math.floor(horas / 24)} dias`;
  }

  function itemBackup(rotulo, leitura) {
    const saude = leitura && leitura.ultima_em ? leitura.saude : "cinza";
    const item = el("div", `tv-backup-item saude-${saude}`);
    const quando = leitura && leitura.ultima_em ? new Date(leitura.ultima_em) : null;
    const texto = quando && !Number.isNaN(quando.getTime())
      ? `${quando.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} às ${hora(quando)}`
      : "sem leitura";
    const estado = { verde: "Em dia", amarelo: "Atrasado", vermelho: "Crítico", cinza: "Sem leitura" }[saude];
    const corpo = el("div", "tv-backup-texto");
    corpo.append(el("span", "", rotulo), el("strong", "", texto), el("small", "", quando ? idade(leitura.ultima_em) : "sem dado"));
    item.append(corpo, el("em", "tv-backup-selo", estado));
    return item;
  }

  function itemBackupClientes(erp) {
    const b = erp && erp.status === "ok" && erp.dados && erp.dados.backup ? erp.dados.backup : null;
    const ok = b && b.disponivel && b.clientes_ativos > 0;
    const saude = ok ? b.saude : "cinza";
    const item = el("div", `tv-backup-item saude-${saude}`);
    const corpo = el("div", "tv-backup-texto");
    const ultimo = ok && b.ultimo_em ? new Date(b.ultimo_em) : null;
    const sub = !ok ? "sem leitura do ERP"
      : ultimo && !Number.isNaN(ultimo.getTime())
        ? `último ${ultimo.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} às ${hora(ultimo)}`
        : "nenhum backup concluído";
    const detalhe = ok && b.com_erro > 0 ? ` · ${b.com_erro} com erro` : ok && b.atrasados + b.sem_backup > 0 ? ` · ${b.atrasados + b.sem_backup} atrasado(s)` : "";
    corpo.append(
      el("span", "", "Backup dos clientes (EZRA)"),
      el("strong", "", ok ? `${b.em_dia} de ${b.clientes_ativos} em dia` : "sem leitura"),
      el("small", "", sub + detalhe),
    );
    const estado = { verde: "Em dia", amarelo: "Atrasado", vermelho: "Crítico", cinza: "Sem leitura" }[saude];
    item.append(corpo, el("em", "tv-backup-selo", estado));
    return item;
  }

  function itemTesteRestauracao(leitura) {
    const l = leitura && typeof leitura === "object" ? leitura : {};
    const quando = l.ultima_em ? new Date(l.ultima_em) : null;
    const dataValida = quando && !Number.isNaN(quando.getTime());
    const saude = dataValida ? l.saude || "cinza" : "cinza";
    const item = el("div", `tv-backup-item saude-${saude}`);
    const corpo = el("div", "tv-backup-texto");
    const texto = dataValida
      ? `${quando.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} às ${hora(quando)}`
      : "nenhum ainda";
    const resultado = !dataValida ? "o teste semanal ainda não rodou" : l.sucesso === false ? "o último teste falhou" : idade(l.ultima_em);
    corpo.append(el("span", "", "Teste de restauração"), el("strong", "", texto), el("small", "", resultado));
    const estado = saude === "vermelho"
      ? (l.sucesso === false ? "Falhou" : "Crítico")
      : { verde: "Funcionou", amarelo: "Antigo", cinza: "Sem teste" }[saude];
    item.append(corpo, el("em", "tv-backup-selo", estado));
    return item;
  }

  function cartaoBackup(b, erp) {
    const alvo = document.getElementById("tv-backup");
    if (!alvo) return;
    const dados = b && typeof b === "object" ? b : {};
    alvo.replaceChildren(
      el("h2", "", "Backup"),
      itemBackup("Backup local", dados.banco_local),
      itemBackup("Envio ao Drive", dados.copia_externa),
      itemBackupClientes(erp),
      itemTesteRestauracao(dados.teste_restauracao),
    );
  }

  function duracaoTexto(min) {
    const total = Math.max(0, Math.floor(Number(min) || 0));
    if (total < 60) return `${total} min`;
    const h = Math.floor(total / 60);
    const m = total % 60;
    if (h < 48) return m ? `${h} h ${String(m).padStart(2, "0")}` : `${h} h`;
    const d = Math.floor(h / 24);
    return `${d} d ${h % 24} h`;
  }

  function dataHoraCurta(valor) {
    const d = new Date(valor);
    if (Number.isNaN(d.getTime())) return "";
    return `${d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} ${hora(d)}`;
  }

  function itemDisponibilidade(rotulo, leitura) {
    const l = leitura && typeof leitura === "object" ? leitura : {};
    const ok = l.disponivel === true && typeof l.disponibilidade_mes === "number";
    const saude = ok ? l.saude || "cinza" : "cinza";
    const item = el("div", `tv-backup-item saude-${saude}`);
    const corpo = el("div", "tv-backup-texto");
    // coletando_desde é data pura (AAAA-MM-DD): formatar da string, sem fuso
    const dd = typeof l.coletando_desde === "string" ? l.coletando_desde : "";
    const desde = dd.length >= 10 ? `${dd.slice(8, 10)}/${dd.slice(5, 7)}` : "";
    corpo.append(
      el("span", "", `${rotulo} · mês${desde ? ` (desde ${desde})` : ""}`),
      el("strong", "", ok ? `${(l.disponibilidade_mes * 100).toFixed(2).replace(".", ",")}%` : "coletando…"),
      el("small", "", ok
        ? l.minutos_fora_mes > 0 ? `${duracaoTexto(l.minutos_fora_mes)} fora no mês` : "nenhum minuto fora no mês"
        : "o histórico começou a ser guardado agora"),
    );
    const estado = saude === "vermelho" && l.incidente_aberto ? "Fora agora"
      : { verde: "No ar", amarelo: "Instável", vermelho: "Quedas", cinza: "Sem dados" }[saude];
    item.append(corpo, el("em", "tv-backup-selo", estado));
    return item;
  }

  function itemIncidentes(lista) {
    const itens = (Array.isArray(lista) ? lista : []).filter((i) => i && i.inicio);
    const aberto = itens.some((i) => !i.fim);
    const item = el("div", `tv-backup-item tv-incidentes saude-${aberto ? "vermelho" : "cinza"}`);
    const corpo = el("div", "tv-backup-texto");
    corpo.append(el("span", "", "Incidentes"), el("strong", "", itens.length ? `${itens.length} recente(s)` : "nenhum"));
    const nomes = { zeregistra: "Zé Registra", erp: "EZRA" };
    const tipos = { falha: "queda", sem_dados: "sem dados (VPS fora)" };
    itens.slice(0, 2).forEach((i) => {
      const fim = i.fim ? duracaoTexto(i.minutos) : "em andamento";
      corpo.append(el("small", "", `${dataHoraCurta(i.inicio)} · ${fim} · ${nomes[i.alvo] || ""} ${tipos[i.tipo] || ""}`));
    });
    item.append(corpo, el("em", "tv-backup-selo", aberto ? "Em queda" : itens.length ? "Histórico" : "Limpo"));
    return item;
  }

  function cartaoDisponibilidade(disp) {
    const alvo = document.getElementById("tv-disponibilidade");
    if (!alvo) return;
    const d = disp && typeof disp === "object" ? disp : {};
    const alvos = d.alvos && typeof d.alvos === "object" ? d.alvos : {};
    alvo.replaceChildren(
      el("h2", "", "Disponibilidade"),
      itemDisponibilidade("Zé Registra", alvos.zeregistra),
      itemDisponibilidade("EZRA ERP", alvos.erp),
      itemIncidentes(d.incidentes),
    );
  }

  function mostrarAviso(texto) {
    if (texto) { alvoAviso.textContent = texto; alvoAviso.hidden = false; } else { alvoAviso.hidden = true; }
  }

  function envelhecer() {
    const velho = ultimoSucesso && Date.now() - ultimoSucesso > VELHO_MS;
    alvoCentral.classList.toggle("velho", Boolean(velho));
    if (velho) alvoErp.classList.add("velho");
    if (ultimoDado && ultimoDado.backup) cartaoBackup(ultimoDado.backup, ultimoDado.erp);
    if (ultimoDado) cartaoDisponibilidade(ultimoDado.disponibilidade);
    alvoAtualizado.textContent = ultimoSucesso ? `atualizado às ${hora(ultimoSucesso)}` : "aguardando dados…";
  }

  async function atualizar() {
    if (parado) return;
    const chave = lerChave();
    try {
      // Com chave (TV): vai no Bearer. Sem chave (computador): o servidor aceita
      // o cookie da sessão de superadmin no lugar da chave.
      const opcoes = { cache: "no-store", credentials: "same-origin" };
      if (chave) opcoes.headers = { Authorization: `Bearer ${chave}` };
      let resposta = await fetch("/v1/monitoramento/tv", opcoes);
      if (resposta.status === 401 && chave) {
        // A chave salva não foi aceita: tenta de novo sem ela, pela sessão.
        resposta = await fetch("/v1/monitoramento/tv", { cache: "no-store", credentials: "same-origin" });
      }
      if (resposta.status === 401) {
        // Sem chave válida e sem sessão: login normal do Zé Registra, voltando
        // ao painel depois de entrar. A chave guardada neste navegador não é apagada.
        parado = true;
        window.location.assign("/login?next=/tv/monitoramento");
        return;
      }
      if (resposta.status === 404) {
        mostrarAviso("Painel de monitoramento desligado no servidor.");
        parado = true;
        return;
      }
      if (!resposta.ok) throw new Error(`status ${resposta.status}`);
      const dados = await resposta.json();
      ultimoDado = dados;
      ultimoSucesso = Date.now();
      cartaoCentral(dados.zeregistra, dados.sistema);
      cartaoErp(dados.erp);
      cartaoBackup(dados.backup, dados.erp);
      cartaoDisponibilidade(dados.disponibilidade);
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

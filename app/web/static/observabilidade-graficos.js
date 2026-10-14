// Gráficos do dashboard de Observabilidade em SVG puro (sem biblioteca externa):
// a CSP do site só permite scripts do próprio domínio e bloqueia `style` inline,
// então as cores vêm de classes CSS (styles.css, prefixo `dash-`) e as formas
// usam apenas atributos de apresentação SVG. Cada função devolve uma string
// SVG pronta para innerHTML; todo texto dinâmico passa por esc().
(function () {
  "use strict";

  const LARGURA = 640;
  const ALTURA = 220;
  const M = { esquerda: 46, direita: 12, topo: 12, base: 28 };

  function esc(valor) {
    return String(valor ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function numero(valor) {
    const n = Number(valor);
    return Number.isFinite(n) ? n : 0;
  }

  // Topo "redondo" do eixo Y (1, 2, 5 × 10^k) para a grade não ficar com valores estranhos.
  function topoEscala(maximo) {
    const max = numero(maximo);
    if (!(max > 0)) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(max)));
    const f = max / exp;
    const bonito = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return bonito * exp;
  }

  function formatoCompacto(valor) {
    const n = numero(valor);
    if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")} mi`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, "")} mil`;
    return String(Math.round(n * 10) / 10).replace(/\.0$/, "");
  }

  function formatoMs(valor) {
    return `${formatoCompacto(valor)} ms`;
  }

  function areaUtil() {
    return { largura: LARGURA - M.esquerda - M.direita, altura: ALTURA - M.topo - M.base };
  }

  function eixos(topo, rotulos, formatoY) {
    const { largura, altura } = areaUtil();
    let svg = "";
    for (let i = 0; i <= 4; i += 1) {
      const y = M.topo + altura * (1 - i / 4);
      svg += `<line class="dash-grade" x1="${M.esquerda}" y1="${y}" x2="${LARGURA - M.direita}" y2="${y}"/>`;
      svg += `<text class="dash-eixo" x="${M.esquerda - 6}" y="${y + 4}" text-anchor="end">${esc(formatoY((topo * i) / 4))}</text>`;
    }
    const passo = Math.max(1, Math.ceil(rotulos.length / 6));
    const faixa = largura / Math.max(1, rotulos.length);
    rotulos.forEach((rotulo, i) => {
      if (i % passo !== 0) return;
      svg += `<text class="dash-eixo" x="${M.esquerda + faixa * (i + 0.5)}" y="${ALTURA - 8}" text-anchor="middle">${esc(rotulo)}</text>`;
    });
    return svg;
  }

  function envolver(titulo, conteudo) {
    return `<svg class="dash-svg" viewBox="0 0 ${LARGURA} ${ALTURA}" role="img" aria-label="${esc(titulo)}" preserveAspectRatio="xMidYMid meet"><title>${esc(titulo)}</title>${conteudo}</svg>`;
  }

  // Barras (ex.: requisições por hora) com os erros sobrepostos em vermelho.
  function graficoBarras({ rotulos, valores, erros = [], titulo, descricao = "requisições", descricaoErros = "erros", formatoY = formatoCompacto }) {
    const dados = valores.map(numero);
    const falhas = dados.map((_, i) => numero(erros[i]));
    const topo = topoEscala(Math.max(0, ...dados));
    const { largura, altura } = areaUtil();
    const faixa = largura / Math.max(1, dados.length);
    const larguraBarra = Math.max(2, faixa * 0.7);
    let barras = "";
    dados.forEach((valor, i) => {
      const x = M.esquerda + faixa * i + (faixa - larguraBarra) / 2;
      const h = (valor / topo) * altura;
      const rotulo = esc(rotulos[i] ?? "");
      barras += `<rect class="dash-barra" x="${x}" y="${M.topo + altura - h}" width="${larguraBarra}" height="${h}"><title>${rotulo}: ${esc(formatoCompacto(valor))} ${esc(descricao)}</title></rect>`;
      if (falhas[i] > 0) {
        const he = Math.max(2, (falhas[i] / topo) * altura);
        barras += `<rect class="dash-barra-erro" x="${x}" y="${M.topo + altura - he}" width="${larguraBarra}" height="${he}"><title>${rotulo}: ${esc(formatoCompacto(falhas[i]))} ${esc(descricaoErros)}</title></rect>`;
      }
    });
    return envolver(titulo, eixos(topo, rotulos, formatoY) + barras);
  }

  // Linhas (ex.: latência média e p95 por hora). series: [{ nome, valores, classe }]
  function graficoLinhas({ rotulos, series, titulo, formatoY = formatoMs }) {
    const todos = series.flatMap((serie) => serie.valores.map(numero));
    const topo = topoEscala(Math.max(0, ...todos));
    const { largura, altura } = areaUtil();
    const faixa = largura / Math.max(1, rotulos.length);
    let linhas = "";
    series.forEach((serie) => {
      const pontos = serie.valores.map((valor, i) => [M.esquerda + faixa * (i + 0.5), M.topo + altura - (numero(valor) / topo) * altura]);
      if (!pontos.length) return;
      linhas += `<polyline class="dash-linha ${esc(serie.classe)}" fill="none" points="${pontos.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}"/>`;
      if (pontos.length <= 48) {
        pontos.forEach(([x, y], i) => {
          linhas += `<circle class="dash-ponto ${esc(serie.classe)}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3"><title>${esc(rotulos[i] ?? "")} — ${esc(serie.nome)}: ${esc(formatoY(serie.valores[i]))}</title></circle>`;
        });
      }
    });
    return envolver(titulo, eixos(topo, rotulos, formatoY) + linhas);
  }

  function legenda(itens) {
    return `<ul class="dash-legenda">${itens.map((item) => `<li><span class="dash-chave ${esc(item.classe)}"></span>${esc(item.nome)}</li>`).join("")}</ul>`;
  }

  // Medidor circular de 0 a 100% (disco, memória, CPU). Verde < 80, âmbar < 90, vermelho acima.
  function gauge({ percentual, rotulo, detalhe = "" }) {
    const valor = percentual == null ? null : Math.min(100, Math.max(0, numero(percentual)));
    const raio = 42;
    const circunferencia = 2 * Math.PI * raio;
    const faixa = valor == null ? "dash-gauge-vazio" : valor >= 90 ? "dash-gauge-critico" : valor >= 80 ? "dash-gauge-atencao" : "dash-gauge-ok";
    const preenchido = valor == null ? 0 : (circunferencia * valor) / 100;
    const texto = valor == null ? "—" : `${Math.round(valor)}%`;
    return `<figure class="dash-gauge"><svg viewBox="0 0 120 120" role="img" aria-label="${esc(rotulo)}: ${esc(texto)}"><title>${esc(rotulo)}: ${esc(texto)}</title>`
      + `<circle class="dash-gauge-fundo" cx="60" cy="60" r="${raio}" fill="none" stroke-width="12"/>`
      + `<circle class="dash-gauge-valor ${faixa}" cx="60" cy="60" r="${raio}" fill="none" stroke-width="12" stroke-linecap="round" stroke-dasharray="${preenchido.toFixed(2)} ${circunferencia.toFixed(2)}" transform="rotate(-90 60 60)"/>`
      + `<text class="dash-gauge-texto" x="60" y="66" text-anchor="middle">${esc(texto)}</text></svg>`
      + `<figcaption><strong>${esc(rotulo)}</strong>${detalhe ? `<small>${esc(detalhe)}</small>` : ""}</figcaption></figure>`;
  }

  const api = { graficoBarras, graficoLinhas, legenda, gauge, topoEscala, formatoCompacto, formatoMs, esc };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.ObsGraficos = api;
})();

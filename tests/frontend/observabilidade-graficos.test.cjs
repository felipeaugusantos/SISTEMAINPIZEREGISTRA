const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const web = path.resolve(__dirname, "../../app/web");
const G = require(path.join(web, "static/observabilidade-graficos.js"));

const contar = (texto, padrao) => (texto.match(padrao) || []).length;

test("topoEscala arredonda o eixo para 1, 2 ou 5 x 10^k", () => {
  assert.equal(G.topoEscala(0), 1);
  assert.equal(G.topoEscala(-5), 1);
  assert.equal(G.topoEscala(7), 10);
  assert.equal(G.topoEscala(130), 200);
  assert.equal(G.topoEscala(430), 500);
  assert.equal(G.topoEscala(1000), 1000);
});

test("graficoBarras: uma barra por hora e erro sobreposto só onde houve erro", () => {
  const svg = G.graficoBarras({
    rotulos: ["10:00", "11:00", "12:00"],
    valores: [10, 0, 40],
    erros: [0, 0, 5],
    titulo: "Tráfego",
  });
  assert.equal(contar(svg, /class="dash-barra"/g), 3);
  assert.equal(contar(svg, /class="dash-barra-erro"/g), 1);
  assert.match(svg, /role="img"/);
  assert.match(svg, /<title>Tráfego<\/title>/);
  assert.match(svg, /12:00: 5 erros/);
});

test("graficoBarras escapa texto dinâmico (sem injeção de HTML)", () => {
  const svg = G.graficoBarras({ rotulos: ['<img src=x onerror=alert(1)>'], valores: [1], titulo: 'a"b<c' });
  assert.ok(!svg.includes("<img"));
  assert.ok(!svg.includes('aria-label="a"b'));
  assert.match(svg, /&lt;img/);
});

test("graficoBarras com dados vazios ou zerados não quebra", () => {
  assert.doesNotThrow(() => G.graficoBarras({ rotulos: [], valores: [], titulo: "vazio" }));
  const svg = G.graficoBarras({ rotulos: ["a"], valores: [0], erros: [0], titulo: "zero" });
  assert.ok(!svg.includes("NaN"));
});

test("graficoLinhas: uma polilinha por série e um ponto por valor", () => {
  const series = [
    { nome: "Média", classe: "dash-linha-media", valores: [100, 200, 150] },
    { nome: "p95", classe: "dash-linha-p95", valores: [400, 900, 500] },
  ];
  const svg = G.graficoLinhas({ rotulos: ["1", "2", "3"], series, titulo: "Latência" });
  assert.equal(contar(svg, /<polyline/g), 2);
  assert.equal(contar(svg, /<circle/g), 6);
  assert.match(svg, /p95: 900 ms/);
  assert.ok(!svg.includes("NaN"));
});

test("gauge escolhe a cor pela faixa e limita o valor a 0-100", () => {
  assert.match(G.gauge({ percentual: 50, rotulo: "Disco" }), /dash-gauge-ok/);
  assert.match(G.gauge({ percentual: 85, rotulo: "Disco" }), /dash-gauge-atencao/);
  assert.match(G.gauge({ percentual: 95, rotulo: "Disco" }), /dash-gauge-critico/);
  assert.match(G.gauge({ percentual: null, rotulo: "Disco" }), /dash-gauge-vazio/);
  assert.match(G.gauge({ percentual: 250, rotulo: "Disco" }), />100%</);
  assert.match(G.gauge({ percentual: -3, rotulo: "Disco" }), />0%</);
});

test("página de observabilidade: IDs usados pelo JS existem e a CSP é respeitada", () => {
  const html = fs.readFileSync(path.join(web, "admin-observabilidade.html"), "utf8");
  const js = fs.readFileSync(path.join(web, "static/admin-observabilidade.js"), "utf8");

  const ids = new Set([...js.matchAll(/querySelector\("#([a-z0-9-]+)"\)/g)].map((m) => m[1]));
  for (const id of ids) {
    assert.ok(html.includes(`id="${id}"`), `id ausente no HTML: ${id}`);
  }

  // CSP: script-src 'self' e style-src 'self' -- nada inline.
  assert.equal(contar(html, /<style[\s>]/g), 0, "bloco <style> inline é bloqueado pela CSP");
  assert.equal(contar(html, /\sstyle="/g), 0, "atributo style inline é bloqueado pela CSP");
  assert.equal(contar(html, /<script(?![^>]*\ssrc=)[^>]*>/g), 0, "script inline é bloqueado pela CSP");
  assert.equal(contar(html, /\son[a-z]+="/g), 0, "handler inline (onclick etc.) é bloqueado pela CSP");

  // Cada aba aponta para um painel existente.
  for (const aba of ["geral", "aplicacao", "infra", "backups", "eventos"]) {
    assert.ok(html.includes(`data-aba="${aba}"`), `aba ausente: ${aba}`);
    assert.ok(html.includes(`id="painel-${aba}"`), `painel ausente: ${aba}`);
  }
  assert.ok(html.indexOf("observabilidade-graficos.js") < html.indexOf("admin-observabilidade.js"));
});

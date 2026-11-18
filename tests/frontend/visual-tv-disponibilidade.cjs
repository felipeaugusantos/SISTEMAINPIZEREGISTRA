// Visual check do painel TV com a seção nova de Disponibilidade (fixture local, sem VPS).
const { chromium } = require("@playwright/test");
const http = require("http");
const fs = require("fs");
const path = require("path");

const RAIZ = path.join(__dirname, "..", "..", "app", "web");
const TIPOS = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".png": "image/png" };

const agora = Date.now();
const iso = (minAtras) => new Date(agora - minAtras * 60000).toISOString();
const fixture = {
  gerado_em: new Date(agora).toISOString(),
  zeregistra: {
    agregado: true, gerado_em: new Date(agora).toISOString(), saude: "verde", erros_5xx_1h: 0,
    requisicoes_24h: 5231, erros_24h: 41, taxa_erros_24h: 0.0078, acoes_24h: 812,
    usuarios_ativos_24h: 9, organizacoes_ativas_24h: 4,
    serie: Array.from({ length: 7 }, (_, i) => ({ dia: `2026-11-${11 + i}`, acoes: 500 + i * 60, erros: i % 3 * 12 })),
    top_erros: [{ operacao: "PATCH /v1/admin/crm/lembretes/{id}", status_http: 429, quantidade: 30 }],
  },
  erp: {
    status: "ok", atualizado_em: new Date(agora).toISOString(), indisponivel_desde: null,
    dados: {
      agregado: true, saude: "amarelo", motivo: "2 erro(s) aberto(s) para revisar",
      erros_abertos: 2, erros_novos_1h: 0, erros_novos_24h: 5, usuarios_ativos_24h: 14, acoes_24h: 1330,
      clientes_ativos_24h: 6, clientes_sem_uso_7d: 3, gerado_em: new Date(agora).toISOString(),
      serie: Array.from({ length: 7 }, (_, i) => ({ dia: `2026-11-${11 + i}`, acoes: 900 + i * 40, erros: i % 4 })),
      top_erros: [{ rota: "/api/login", origem: "app", quantidade: 4 }],
      clientes_sem_uso: [{ nome: "Clínica Exemplo", dias_sem_uso: 9 }, { nome: "Escritório Modelo", dias_sem_uso: null }, { nome: "Padaria Teste", dias_sem_uso: 12 }],
      backup: { disponivel: true, clientes_ativos: 6, em_dia: 6, atrasados: 0, sem_backup: 0, com_erro: 0, ultimo_em: iso(700), mais_antigo_em: iso(700), saude: "verde" },
    },
  },
  backup: {
    disponivel: true,
    banco_local: { ultima_em: iso(1200), saude: "verde" },
    copia_externa: { ultima_em: iso(1100), saude: "verde" },
    teste_restauracao: { ultima_em: iso(60), sucesso: true, saude: "verde" },
  },
  disponibilidade: {
    disponivel: true, mes: "2026-11",
    alvos: {
      zeregistra: { disponivel: true, saude: "amarelo", disponibilidade_mes: 0.9987, minutos_fora_mes: 6, coletando_desde: "2026-11-17", incidente_aberto: false },
      erp: { disponivel: true, saude: "verde", disponibilidade_mes: 1.0, minutos_fora_mes: 0, coletando_desde: "2026-11-17", incidente_aberto: false },
    },
    incidentes: [
      { alvo: "zeregistra", inicio: iso(190), fim: iso(187), minutos: 3, tipo: "falha" },
      { alvo: "erp", inicio: iso(1500), fim: iso(1492), minutos: 8, tipo: "sem_dados" },
    ],
  },
  sistema: {
    cpu: { percentual: 31, saude: "verde" },
    memoria: { percentual: 62, saude: "verde" },
    disco: { percentual: 58, livre_gb: 80, saude: "verde" },
  },
};

const server = http.createServer((req, res) => {
  const caminho = path.join(RAIZ, req.url === "/" ? "tv-monitoramento.html" : decodeURIComponent(req.url.split("?")[0]));
  fs.readFile(caminho, (erro, dados) => {
    if (erro) { res.writeHead(404); res.end("nf"); return; }
    res.writeHead(200, { "Content-Type": TIPOS[path.extname(caminho)] || "application/octet-stream" });
    res.end(dados);
  });
});

(async () => {
  await new Promise((r) => server.listen(0, r));
  const porta = server.address().port;
  const browser = await chromium.launch();
  const variantes = [
    [1920, 1080, "tv-1920", fixture],
    [1536, 751, "tv-1536", fixture],
    [1536, 751, "tv-1536-vazio", { ...fixture, disponibilidade: { disponivel: false, mes: "2026-11", alvos: { zeregistra: { disponivel: false, saude: "cinza" }, erp: { disponivel: false, saude: "cinza" } }, incidentes: [] } }],
  ];
  for (const [largura, altura, nome, dado] of variantes) {
    const page = await browser.newPage({ viewport: { width: largura, height: altura } });
    await page.route("**/v1/monitoramento/tv", (rota) =>
      rota.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(dado) }));
    await page.goto(`http://127.0.0.1:${porta}/tv-monitoramento.html#t=chave-teste-visual`);
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `/downloads/${nome}.png`, fullPage: false });
    const medidas = await page.evaluate(() => {
      const r = (s) => { const n = document.querySelector(s); if (!n) return null; const b = n.getBoundingClientRect(); return { topo: Math.round(b.top), base: Math.round(b.bottom), altura: Math.round(b.height) }; };
      const cartao = document.querySelector("#tv-zeregistra");
      const filhos = cartao ? Math.round([...cartao.children].reduce((a, f) => a + f.getBoundingClientRect().height, 0)) : 0;
      return { janela: window.innerHeight, zr: r("#tv-zeregistra"), erp: r("#tv-erp"), backup: r("#tv-backup"), disp: r("#tv-disponibilidade"), conteudoZr: filhos };
    });
    console.log(nome, JSON.stringify(medidas));
    await page.close();
  }
  await browser.close();
  server.close();
  console.log("screenshots ok");
})().catch((e) => { console.error(e); process.exit(1); });

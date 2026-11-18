// Painel de TV, item 3 (17/11/2026): a faixa de Disponibilidade mostra a % do mês,
// os minutos fora e os incidentes; sem dados da sonda, fica cinza "coletando".
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../app/web/static/tv-monitoramento.js'), 'utf8');

function element(tag = 'div') {
  return {
    tag, classe: '', texto: '', filhos: [], hidden: false,
    set className(v) { this.classe = v; },
    get className() { return this.classe; },
    set textContent(v) { this.texto = String(v); },
    get textContent() { return this.texto; },
    append(...filhos) { this.filhos.push(...filhos); },
    replaceChildren(...filhos) { this.filhos = [...filhos]; },
    setAttribute() {},
    classList: { add() {}, remove() {}, toggle() {} },
  };
}

function textoDe(no) {
  return [no.texto || '', ...(no.filhos || []).map(textoDe)].join(' ');
}

const RESPOSTA = {
  gerado_em: '2026-11-17T22:00:00Z',
  zeregistra: { agregado: true, saude: 'verde', erros_5xx_1h: 0, requisicoes_24h: 10, erros_24h: 0, taxa_erros_24h: 0, acoes_24h: 5, usuarios_ativos_24h: 2, organizacoes_ativas_24h: 1, serie: [], top_erros: [] },
  erp: { status: 'nao_configurado', dados: null, atualizado_em: null, indisponivel_desde: null },
  backup: { disponivel: false, banco_local: { ultima_em: null, saude: 'cinza' }, copia_externa: { ultima_em: null, saude: 'cinza' }, teste_restauracao: { ultima_em: null, sucesso: null, saude: 'cinza' } },
  sistema: { cpu: { percentual: 10, saude: 'verde' }, memoria: { percentual: 20, saude: 'verde' }, disco: { percentual: 30, livre_gb: 90, saude: 'verde' } },
  disponibilidade: {
    disponivel: true,
    mes: '2026-11',
    alvos: {
      zeregistra: { disponivel: true, saude: 'amarelo', disponibilidade_mes: 0.9987, minutos_fora_mes: 6, coletando_desde: '2026-11-17', incidente_aberto: false },
      erp: { disponivel: true, saude: 'verde', disponibilidade_mes: 1, minutos_fora_mes: 0, coletando_desde: '2026-11-17', incidente_aberto: false },
    },
    incidentes: [
      { alvo: 'zeregistra', inicio: '2026-11-17T21:36:00Z', fim: '2026-11-17T21:41:00Z', minutos: 5, tipo: 'falha' },
      { alvo: 'erp', inicio: '2026-11-16T23:00:00Z', fim: null, minutos: 8, tipo: 'sem_dados' },
    ],
  },
};

async function fixture(resposta = RESPOSTA) {
  const elementos = new Map();
  const get = (id) => { if (!elementos.has(id)) elementos.set(id, element()); return elementos.get(id); };
  const contexto = vm.createContext({
    document: {
      querySelector: get,
      querySelectorAll: () => [],
      createElement: (tag) => element(tag),
      createElementNS: (_ns, tag) => element(tag),
      getElementById: (id) => get(`#${id}`),
    },
    window: {
      location: { hash: '', pathname: '/tv/monitoramento', reload() {} },
      history: { replaceState() {} },
      localStorage: { getItem: () => 'chave-teste', setItem() {} },
      setInterval: () => 0,
      setTimeout: () => 0,
    },
    fetch: async () => ({ ok: true, status: 200, json: async () => resposta }),
    Date, Number, console, Math,
  });
  vm.runInContext(source, contexto);
  await new Promise((r) => setTimeout(r, 10));
  return { get };
}

test('Disponibilidade: mostra % do mês, minutos fora e incidentes', async () => {
  const f = await fixture();
  const faixa = f.get('#tv-disponibilidade');
  assert.equal(faixa.filhos[0].texto, 'Disponibilidade');
  const texto = textoDe(faixa);
  assert.ok(texto.includes('99,87%'), texto);
  assert.ok(texto.includes('6 min fora no mês'), texto);
  assert.ok(texto.includes('100,00%'), texto);
  assert.ok(texto.includes('nenhum minuto fora no mês'), texto);
  assert.ok(texto.includes('desde 17/11'), texto);
  assert.ok(texto.includes('5 min'), texto);
  assert.ok(texto.includes('queda'), texto);
  assert.ok(texto.includes('em andamento'), texto);
  assert.ok(texto.includes('sem dados (VPS fora)'), texto);
});

test('Disponibilidade: sem dados da sonda fica cinza "coletando"', async () => {
  const f = await fixture({
    ...RESPOSTA,
    disponibilidade: { disponivel: false, mes: '', alvos: { zeregistra: { disponivel: false, saude: 'cinza' }, erp: { disponivel: false, saude: 'cinza' } }, incidentes: [] },
  });
  const texto = textoDe(f.get('#tv-disponibilidade'));
  assert.ok(texto.includes('coletando'), texto);
  assert.ok(texto.includes('nenhum'), texto);
  assert.ok(!texto.includes('99'), texto);
});

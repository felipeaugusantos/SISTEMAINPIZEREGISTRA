const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('app/web/static/admin-leads.js', 'utf8');
const handler = source.slice(source.indexOf('async function applyListPosition()'), source.indexOf('\nfunction quickResearchHistory'));
for (const dialog of [null, { lead: 60, tab: 'atendimento', scroll: 320 }]) test(`retorno restaura rolagem ${dialog ? 'e contexto do diálogo' : 'da lista sem rolagem por foco'}`, async () => {
  const calls = [], classes = new Set(), listeners = {};
  const row = { dataset: { leadId: '60', researchId: 'r60' }, classList: { add: x => classes.add(x), remove: x => classes.delete(x) }, focus: opts => calls.push(['focus', opts.preventScroll]) };
  const context = vm.createContext({
    restoredPosition: { lead: '60', research: 'r60', scroll: 125, dialog },
    leadsList: { querySelectorAll: selector => selector.includes('researches-row') ? [] : [row] },
    document: { fonts: { ready: Promise.resolve() }, addEventListener: (name, fn) => { listeners[name] = fn; } },
    window: { scrollTo: opts => calls.push(['scroll', opts.top, opts.behavior]) },
    leadDialog: { restoreNavigationContext: async data => calls.push(['dialog', data.lead, data.scroll]) },
    requestAnimationFrame: fn => fn(), setTimeout: () => {},
  });
  vm.runInContext(handler, context); await context.applyListPosition();
  if (dialog) assert.deepEqual(calls, [['scroll', 125, 'instant'], ['dialog', 60, 320]]);
  else {
    assert.deepEqual(calls, [['focus', true], ['scroll', 125, 'instant']]);
    assert.equal(classes.has('lead-returned'), true);
    listeners.keydown(); assert.equal(classes.has('lead-returned'), false);
  }
  assert.equal(context.restoredPosition, null);
});

// Uso: ver tests/local_real/README.md
const { chromium } = require(process.env.PW_MODULE || 'playwright');
const B = 'http://127.0.0.1:8123'; const D=(process.env.SHOTS||'/tmp/shots')+'/';
const out=[]; const ok=(n,c)=>{out.push((c?'OK   ':'FALHA ')+n);console.log(out[out.length-1]);};
(async () => {
  const b = await chromium.launch({ executablePath: process.env.PW_CHROME });
  async function ctxFor(user,w,h){ const ctx=await b.newContext({viewport:{width:w,height:h}}); await ctx.addCookies([{name:'zr_test_user',value:user,url:B},{name:'zr_csrf',value:'csrf-sintetico',url:B}]); const p=await ctx.newPage(); p.setDefaultTimeout(8000); p.on('pageerror',e=>ok('erro JS '+e.message,false)); await p.goto(B+'/admin/leads'); await p.locator('#leads-list tr[data-lead-id]').first().waitFor(); await p.waitForTimeout(400); return p; }
  const api = async (p, path) => p.evaluate(async u => (await fetch(u)).json(), path);
  // operador, desktop
  let p = await ctxFor('op',1280,800);
  const row = p.locator('tr[data-lead-id="5"]').first();
  if (await row.locator('.lead-actions-toggle').isVisible()) await row.locator('.lead-actions-toggle').click();
  ok('operador ve "Solicitar exclusão do contato"', await row.locator('.request-delete-lead').isVisible());
  ok('operador NAO ve Arquivar', (await p.locator('.archive-lead').count())===0);
  await row.locator('.request-delete-lead').click();
  await p.locator('#research-delete-dialog[open]').waitFor(); await p.waitForTimeout(300);
  ok('titulo do dialogo', (await p.locator('#research-delete-title').textContent())==='Solicitar exclusão do contato?');
  await p.screenshot({path:D+'lead-desktop-dialogo.png'});
  await p.locator('#research-delete-form textarea[name="motivo"]').fill('Contato duplicado do lead 3');
  await p.locator('#research-delete-form button[type="submit"]').click();
  await p.locator('#admin-message',{hasText:'Pedido enviado'}).waitFor(); await p.waitForTimeout(300);
  ok('banner de sucesso', true);
  ok('apos o pedido: botao vermelho some e aparece selo pendente', (await row.locator('.request-delete-lead').count())===0 && (await row.locator('.lead-delete-pending').count())===1);
  const geo = await p.evaluate(()=>{const r=document.querySelector('tr[data-lead-id="5"]');const sel=r.querySelector('.lead-status').getBoundingClientRect();const td=r.querySelector('.lead-status').closest('td').getBoundingClientRect();const a=r.querySelector('.lead-row-actions').getBoundingClientRect();return sel.right<=td.right+1 && sel.right<=a.left+1;});
  ok('seletor de status nao invade a coluna de acoes (desktop)', geo);
  const outro = p.locator('tr[data-lead-id="3"]').first();
  ok('outro lead sem pedido continua com o botao', (await outro.locator('.request-delete-lead').count())===1);
  await p.screenshot({path:D+'lead-desktop-sucesso.png'});
  ok('lead 5 segue ativo (nao arquivado, nao apagado)', (await p.locator('tr[data-lead-id="5"]').count())>0);
  ok('operador sem fila', (await p.locator('#deletion-requests').isHidden()));
  await p.context().close();
  // mobile operador
  p = await ctxFor('op',390,844);
  const r2 = p.locator('tr[data-lead-id="4"]').first();
  await r2.locator('.lead-actions-toggle').scrollIntoViewIfNeeded(); if (await r2.locator('.lead-actions-toggle').isVisible()) await r2.locator('.lead-actions-toggle').click(); await r2.locator('.request-delete-lead').click();
  await p.locator('#research-delete-dialog[open]').waitFor(); await p.waitForTimeout(300);
  await p.screenshot({path:D+'lead-mobile-dialogo.png'});
  await p.screenshot({path:D+'lead-mobile-lista.png'});
  await p.locator('#research-delete-form textarea[name="motivo"]').fill('Contato sem interesse'); await p.locator('#research-delete-form button[type="submit"]').click(); await p.locator('#admin-message',{hasText:'Pedido enviado'}).waitFor(); await p.waitForTimeout(300);
  ok('mobile: selo pendente no lead 4', (await p.locator('tr[data-lead-id="4"] .lead-delete-pending').count())===1);
  await p.screenshot({path:D+'lead-mobile-sucesso.png'});
  await p.context().close();
  // admin
  p = await ctxFor('adm',1280,900);
  await p.locator('#deletion-requests-toggle').click(); await p.waitForTimeout(500);
  const card = p.locator('.deletion-request-card[data-tipo="lead"][data-marca="Lead 5"]');
  ok('admin ve cartao de LEAD na fila', await card.isVisible());
  ok('rotulo Contato + botao Aprovar e arquivar contato', (await card.textContent()).includes('Contato') && (await card.locator('[data-decision="approve"]').textContent()).includes('arquivar contato'));
  await card.scrollIntoViewIfNeeded(); await p.waitForTimeout(200);
  await p.screenshot({path:D+'lead-desktop-fila-admin.png'});
  await card.locator('[data-decision="approve"]').click();
  await p.locator('#research-delete-dialog[open]').waitFor();
  ok('dialogo de aprovacao fala em arquivar', (await p.locator('#research-delete-title').textContent())==='Aprovar e arquivar o contato?');
  await p.locator('#research-delete-form input[name="senha"]').fill('errada');
  await p.locator('#research-delete-form textarea[name="motivo"]').fill('Aprovado pelo admin');
  await p.locator('#research-delete-form button[type="submit"]').click();
  await p.locator('#research-delete-message.error').waitFor();
  ok('senha errada: erro e nada arquivado', (await p.locator('tr[data-lead-id="5"]').count())>0);
  await p.locator('#research-delete-form input[name="senha"]').fill('Senha-Sintetica-1!');
  await p.locator('#research-delete-form button[type="submit"]').click();
  await p.locator('#admin-message',{hasText:'o contato foi arquivado'}).waitFor(); await p.waitForTimeout(500);
  ok('aprovado: lead 5 saiu da lista ativa', (await p.locator('tr[data-lead-id="5"]').count())===0);
  await p.screenshot({path:D+'lead-desktop-aprovado.png'});
  await p.locator('[data-view="archived"]').click(); await p.waitForTimeout(700);
  ok('lead 5 aparece em arquivados (restauravel)', (await p.locator('tr[data-lead-id="5"] .restore-lead').count())>0 || (await p.locator('tr[data-lead-id="5"]').count())>0);
  // rejeitar o lead 4 pedido pelo operador (mobile)
  await p.locator('[data-view="researches"]').click(); await p.waitForTimeout(700);
  const c4 = p.locator('.deletion-request-card[data-tipo="lead"][data-marca="Lead 4"]');
  if (await c4.count()) { await c4.locator('[data-decision="reject"]').click(); await p.locator('#research-delete-form textarea[name="motivo"]').fill('Manter contato'); await p.locator('#research-delete-form button[type="submit"]').click(); await p.locator('#admin-message',{hasText:'rejeitada'}).waitFor(); ok('rejeitar pedido de lead: banner e lead 4 segue ativo', (await p.locator('tr[data-lead-id="4"]').count())>0); } else ok('pedido do lead 4 na fila', false);
  await b.close();
  require('fs').writeFileSync('/tmp/it/e2e-result.txt', out.join('\n'));
})().catch(e => { console.log('ERRO', e.message.split('\n').slice(0,4).join(' | ')); process.exit(1); });

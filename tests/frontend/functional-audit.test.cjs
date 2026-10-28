const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname,'../../app/web');
const pages = { '/admin':'admin.html', '/admin/crm':'admin-crm.html', '/admin/financeiro/nfse':'admin-financeiro-nfse.html', '/admin/financeiro/conciliacao':'admin-financeiro-conciliacao.html', '/admin/atualizacoes':'admin-atualizacoes.html', '/admin/notificacoes':'admin-notificacoes.html', '/admin/configuracao/whatsapp':'admin-whatsapp.html', '/admin/operacao-juridica':'admin-juridico.html' };
const release = { id:1,versao:'1.2.32',titulo:'Correção de interface',classificacao:'correcao',estado:{confirmada_em:null,adiada_ate:null},implantada_em:'2026-10-27T12:00:00Z',modulos_afetados:['plataforma'],evidencias:{aprovadas:1,falharam:0,ignoradas:0},pode_adiar:true,impacto_usuario:'Interface corrigida',problema:'Problema reproduzido',correcao:'Correção aplicada' };
async function fixture(page, module, options={}) {
  const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname;
    requests.push({path:p,method:request.method(),search:url.search,body:request.postData()});
    if(options.handle && await options.handle(route,url))return;
    if(p.startsWith('/static/'))return route.fulfill({path:path.join(web,p)});
    if(p.endsWith('.css'))return route.fulfill({contentType:'text/css',body:''});
    if(p.startsWith('/admin')){
      const html=fs.readFileSync(path.join(web,pages[p]),'utf8').replace(/<script\b[^>]*src="([^"]+)"[^>]*><\/script>/g,(tag,src)=>{
        const keep=src.includes('/'+module+'.js') || (module==='admin-crm'&&src.includes('/lead-dialog.js')) || (options.shell&&src.includes('/admin-shell.js'));
        return keep?tag:'';
      });return route.fulfill({contentType:'text/html',body:html});
    }
    let data={itens:[],total:0,tem_mais:false};
    if(p==='/v1/auth/me')data={nome:'Equipe de teste',perfil:'administrador',superadmin:true,permissoes:[],organizacao:{nome:'Zé Registra',slug:'ze-registra',marca:{propria:true,nome:'Zé Registra',logo_url:'/static/assets/logo-zeregistra.png'}}};
    if(p==='/v1/admin/atualizacoes')data={versao_implantada:'1.2.32',atualizacao_implantada_id:1,novidades:[release]};
    if(p==='/v1/admin/crm/referencias')data={canais:[],operadores:[],status_clientes:[],tipos_lembrete:[{id:'retorno',nome:'Retorno'}],prioridades:[{id:'media',nome:'Média'}]};
    if(p==='/v1/admin/crm/historico')data={itens:[],total:0,tem_mais:false,por_canal:{}};
    if(p==='/v1/admin/crm/lembretes')data={itens:[],total:0,tem_mais:false,acoes:{gerenciar:true},metricas:{},cadastros_para_atualizar:[]};
    if(p==='/v1/admin/leads-kanban')data={etapas:[],cards:[],acoes:{gerenciar:true}};
    if(p==='/v1/admin/crm/clientes/pesquisar'||p==='/v1/admin/financeiro/empresas/pesquisar')data={itens:[{id:7,nome:'Empresa de teste',empresa:'Empresa'}],total:1,tem_mais:false};
    if(p==='/v1/admin/financeiro/conciliacao')data={itens:[],total:0,proximo_offset:null};
    if(p==='/v1/admin/financeiro/conciliacao/importar')data={importadas:1,conciliadas_automaticamente:0,duplicadas:0};
    if(p==='/v1/admin/notificacoes')data={itens:[{id:1,fonte:'sistema',lida:false,titulo:'Aviso de teste',mensagem:'Aviso',criado_em:'2026-10-27T12:00:00Z'}],total:1,total_itens:1};
    if(p==='/v1/admin/mensageria/fila'||p==='/v1/admin/mensageria/consentimentos')data=[];
    await route.fulfill({json:data});
  });return {errors,requests};
}
for(const [title,url,module,open,input,result] of [
 ['CRM','/admin/crm','admin-crm','#new-reminder','#crm-client-search','#crm-client-results [data-lead-id="7"]'],
 ['NFS-e','/admin/financeiro/nfse','admin-financeiro-nfse','#new-nota','#nfse-client-search','#nfse-client-results [data-client-id="7"]'],
])test(title+': busca digitada encontra e seleciona o cliente após o atraso',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage();const {errors,requests}=await fixture(page,module);await page.goto('http://functional.test'+url);await page.locator(open).click();await page.locator(input).fill('Empresa');await page.locator(result).waitFor({timeout:2000});await page.locator(result).click();assert.ok(requests.some(r=>r.search.includes('busca=Empresa')));assert.deepEqual(errors,[]);}finally{await browser.close();}
});
test('Atualizações: relato enviado fecha, limpa o formulário e mostra sucesso',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage();const {errors,requests}=await fixture(page,'admin-atualizacoes');await page.goto('http://functional.test/admin/atualizacoes');await page.locator('[data-action="report"]').click();await page.locator('#updates-report-description').fill('O botão não respondeu durante a consulta de teste.');await page.locator('#updates-report-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#updates-report-dialog').open);assert.equal(await page.locator('#updates-report-description').inputValue(),'');assert.match(await page.locator('#updates-message').innerText(),/Problema reportado/);assert.equal(requests.filter(r=>r.path.endsWith('/1/problemas')&&r.method==='POST').length,1);assert.deepEqual(errors,[]);}finally{await browser.close();}
});
test('Conciliação: importação limpa arquivo e atualiza a listagem sem falso erro',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage();const {errors,requests}=await fixture(page,'admin-financeiro-conciliacao');await page.goto('http://functional.test/admin/financeiro/conciliacao');await page.waitForFunction(()=>document.querySelector('#transacoes-count').textContent==='0 registros');await page.locator('#upload-form input').setInputFiles({name:'teste.ofx',mimeType:'application/x-ofx',buffer:Buffer.from('<OFX></OFX>')});await page.locator('#upload-form button').click();await page.waitForFunction(()=>!document.querySelector('#conc-message').hidden);assert.match(await page.locator('#conc-message').innerText(),/1 transação/);assert.equal(await page.locator('#upload-form input').evaluate(el=>el.files.length),0);assert.ok(requests.filter(r=>r.path==='/v1/admin/financeiro/conciliacao').length>=2);assert.deepEqual(errors,[]);}finally{await browser.close();}
});
test('menu recolhido no desktop permite acessar submenus no celular e mantém preferência',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage({viewport:{width:1440,height:900}});const {errors}=await fixture(page,'admin-shell',{shell:true});await page.goto('https://app.zeregistra.com.br/admin');await page.locator('#admin-sidebar-toggle').click();await page.setViewportSize({width:390,height:900});await page.locator('.admin-menu-toggle').click();assert.equal(await page.locator('.admin-sidebar-nav a[data-submenu-parent="comercial"][href="/admin/consulta"]').isVisible(),true);assert.equal(await page.locator('.admin-sidebar-brand').evaluate(el=>Math.round(el.parentElement.getBoundingClientRect().width)),252);assert.equal(await page.locator('.admin-sidebar-brand img').getAttribute('src'),'/static/assets/personagem.png?v=1');await page.setViewportSize({width:1440,height:900});assert.equal(await page.locator('body').evaluate(el=>el.classList.contains('admin-sidebar-collapsed')),true);assert.equal(await page.locator('.admin-sidebar-overlay').isVisible(),false);assert.deepEqual(errors,[]);}finally{await browser.close();}
});
test('Notificações: erro do servidor fica visível e permite tentar novamente sem fingir sucesso',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage();const {errors,requests}=await fixture(page,'admin-notificacoes',{handle:async(route,url)=>{if(route.request().method()!=='POST')return false;await route.fulfill({status:503,json:{detail:'Serviço indisponível. Tente novamente.'}});return true;}});await page.goto('http://functional.test/admin/notificacoes');await page.locator('.notif-toggle').click();await page.waitForTimeout(200);assert.match(await page.locator('#notif-page-message').innerText(),/Serviço indisponível/);assert.equal(await page.locator('.notif-toggle').isEnabled(),true);assert.equal(requests.filter(r=>r.path==='/v1/admin/notificacoes').length,1);await page.locator('#notif-marcar-todas-lidas').click();await page.waitForTimeout(100);assert.match(await page.locator('#notif-page-message').innerText(),/Serviço indisponível/);assert.deepEqual(errors,[]);}finally{await browser.close();}
});
test('WhatsApp: revogação confirmada limpa o formulário e atualiza dados',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage();const {errors,requests}=await fixture(page,'admin-whatsapp');page.on('dialog',d=>d.accept());await page.goto('http://functional.test/admin/configuracao/whatsapp');await page.locator('#wa-form-revogar [name=telefone]').fill('11999999999');await page.locator('#wa-form-revogar button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#wa-revogar-msg').textContent!=='Salvando…');assert.match(await page.locator('#wa-revogar-msg').innerText(),/Telefone revogado/);assert.equal(await page.locator('#wa-form-revogar [name=telefone]').inputValue(),'');assert.ok(requests.filter(r=>r.path==='/v1/admin/mensageria/status').length>=2);assert.deepEqual(errors,[]);}finally{await browser.close();}
});
test('Jurídico: reprocessamento concluído encerra a confirmação e atualiza o painel',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage();let posted=0;await fixture(page,'admin-juridico',{handle:async(route,url)=>{if(url.pathname!=='/v1/admin/juridico/motor/reprocessamento'||route.request().method()!=='POST')return false;posted++;await route.fulfill({json:{avaliacoes_liberadas:1,resultado_motor:{prazos_sugeridos:1}}});return true;}});await page.goto('http://functional.test/admin/operacao-juridica');await page.evaluate(()=>{legalState.reprocessPreview={quantidade:1,regra:{id:1},limite:5};loadDashboard=async()=>{};loadLegalPolicy=async()=>{};loadGovernance=async()=>{};const f=document.querySelector('#legal-reprocess-confirm');f.hidden=false;f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));});await page.waitForTimeout(200);assert.equal(posted,1);assert.equal(await page.locator('#legal-reprocess-confirm').evaluate(el=>el.hidden),true);assert.equal(await page.evaluate(()=>legalState.reprocessPreview),null);}finally{await browser.close();}
});

test('Notificações: clique repetido não duplica a ação e os dois botões em lote atualizam a lista',async()=>{
 const browser=await chromium.launch();let release;const pending=new Promise(resolve=>{release=resolve;});try{const page=await browser.newPage();let lida=false,calls=0;
 await fixture(page,'admin-notificacoes',{handle:async(route,url)=>{if(!url.pathname.startsWith('/v1/admin/notificacoes'))return false;if(route.request().method()==='POST'){calls++;if(calls===1)await pending;lida=url.searchParams.has('lida')?url.searchParams.get('lida')==='true':url.pathname.endsWith('/lida');await route.fulfill({json:{}});return true;}await route.fulfill({json:{itens:[{id:1,fonte:'sistema',lida,titulo:'Aviso',mensagem:'Teste',criado_em:'2026-10-27T12:00:00Z'}],total:lida?0:1,total_itens:1}});return true;}});
 await page.goto('http://functional.test/admin/notificacoes');const toggle=page.locator('.notif-toggle');await toggle.click();assert.equal(await toggle.isDisabled(),true);assert.equal(await page.locator('#notif-marcar-todas-lidas').isDisabled(),true);await toggle.dispatchEvent('click');await page.locator('#notif-marcar-todas-lidas').dispatchEvent('click');release();await page.waitForFunction(()=>document.querySelector('.notif-toggle').textContent==='Marcar como não lida');assert.equal(calls,1);
 await page.locator('#notif-marcar-todas-nao-lidas').click();await page.waitForFunction(()=>document.querySelector('.notif-toggle').textContent==='Marcar como lida');
 await page.locator('#notif-marcar-todas-lidas').click();await page.waitForFunction(()=>document.querySelector('.notif-toggle').textContent==='Marcar como não lida');assert.equal(calls,3);
 }finally{release();await browser.close();}
});
test('Notificações: falha de rede reabilita os controles e informa o erro',async()=>{
 const browser=await chromium.launch();try{const page=await browser.newPage();const {errors}=await fixture(page,'admin-notificacoes',{handle:async(route)=>{if(route.request().method()!=='POST')return false;await route.abort('failed');return true;}});await page.goto('http://functional.test/admin/notificacoes');await page.locator('#notif-marcar-todas-lidas').click();await page.waitForFunction(()=>document.querySelector('#notif-page-message').classList.contains('error'));assert.equal(await page.locator('#notif-marcar-todas-lidas').isEnabled(),true);assert.equal(await page.locator('#notif-filter-todas').isEnabled(),true);assert.deepEqual(errors,[]);}finally{await browser.close();}
});

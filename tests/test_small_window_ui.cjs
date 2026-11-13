// Source UI and real local read endpoints; game/restore replies are explicit fixtures.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,cid,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch(process.env.SOLO_UI_BROWSER?{executablePath:process.env.SOLO_UI_BROWSER,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']}:{channel:'msedge',headless:true}),checks=[],errors=[],posts=[];
 try{for(const [width,height] of [[1280,800],[960,640]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
  page.on('pageerror',error=>errors.push(String(error)));
  await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  const real=await (await context.request.get(base+'/api/campaigns/'+cid)).json();
  const screen=await (await context.request.get(base+'/api/campaigns/'+cid+'/screen')).json();
  const campaign=structuredClone(real);campaign.backend='nat20';campaign.conversation=[];campaign.gm_history=[];
  campaign.history=Array.from({length:14},(_,i)=>({id:'ui-event-'+i,seq:i+1,created:i+1,original:'Проверка '+(i+1),narrative:'Подтверждённая запись '+(i+1)+'. '+('Текст истории для проверки обычного чтения. '.repeat(4)),results:[],undone:false}));
  const saveName='Контрольная точка · ход 12 · Перед старым фонарём и закрытой калиткой';
  campaign.saves=[{id:'ui-checkpoint',name:saveName}];campaign.state.turn=14;
  const packet=structuredClone(screen);packet.snapshot.backend='nat20';packet.snapshot.command_turn=true;packet.snapshot.version=14;packet.snapshot.combat=null;packet.snapshot.terminal=null;
  packet.snapshot.scene={title:'Письмо у старого фонаря',description:'Следы осмотрены. Можно войти во двор и начать столкновение.'};
  packet.snapshot.hero.resources=[{id:'second_wind_spent',label:'Второе дыхание · потрачено',current:0,maximum:null}];
  packet.events=campaign.history.map(h=>({id:h.id,sequence:h.seq,text:h.narrative,roll:null}));
  packet.commands=[{id:'check',kind:'engine',label:'Проверить Силу',parameters:{kind:'check',check_id:'strength'},expected_version:14},{id:'rest',kind:'engine',label:'Короткий отдых · потратить кость хитов',parameters:{kind:'short_rest'},expected_version:14}];
  let restoreCount=0,turnCount=0,legacyCount=0,buttonCount=0;
  await page.route(base+'/api/campaigns/'+cid,route=>route.fulfill({json:campaign}));
  await page.route(base+'/api/campaigns/'+cid+'/screen',route=>route.fulfill({json:packet}));
  await page.route(base+'/api/campaigns/'+cid+'/restore/*',route=>{
   restoreCount++;posts.push({width,path:'restore',body:route.request().postDataJSON()});
   campaign.history.slice(12).forEach(h=>h.undone=true);campaign.state.turn=12;
   packet.snapshot.version++;packet.snapshot.hero.hp=12;packet.snapshot.hero.conditions=[];
   packet.commands.forEach(command=>command.expected_version=packet.snapshot.version);
   packet.snapshot.terminal=null;packet.events=packet.events.filter(e=>e.sequence<=12);
   packet.snapshot.last_roll={label:'Проверка',dice:[8],total:8,success:false};
   return route.fulfill({json:campaign});
  });
  await page.route(base+'/api/campaigns/'+cid+'/command-turn',route=>{
   turnCount++;posts.push({width,path:'command-turn',body:route.request().postDataJSON()});
   return route.fulfill({json:{status:'resolved',narrative:'Подтверждено тестовой фикстурой.'}});
  });
  await page.route(base+'/api/campaigns/'+cid+'/play',route=>{legacyCount++;return route.fulfill({json:{kind:'reply',reply:'Solo fixture'}});});
  await page.route(base+'/api/campaigns/'+cid+'/commands',route=>{buttonCount++;posts.push({width,path:'commands',body:route.request().postDataJSON()});return route.fulfill({json:{}});});
  await page.route(base+'/api/events/ui-event-13/narrate',route=>route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'token',text:'Потоковое описание тестовой фикстуры. '.repeat(8)})+'\n\n'}));
  await page.addInitScript(id=>localStorage.setItem('solo-campaign',id),cid);
  await page.goto(base);await page.waitForFunction(()=>window.SoloGameScreen&&!document.getElementById('send').disabled);
  await page.evaluate(()=>{view('play');document.body.classList.remove('effects');});
  const input=page.locator('#player-input'),chat=page.locator('#chat');
  await input.fill('Черновик сохраняется при восстановлении');
  // Reading an older message must survive both campaign refresh and packet reload.
  const before=await chat.evaluate(node=>{node.scrollTop=250;const anchor=[...node.querySelectorAll('[data-message-key]')].find(e=>e.getBoundingClientRect().bottom>node.getBoundingClientRect().top);return {key:anchor.dataset.messageKey,offset:anchor.getBoundingClientRect().top-node.getBoundingClientRect().top};});
  await page.evaluate(async()=>{await refresh();await SoloGameScreen.reload();});
  const after=await chat.evaluate((node,key)=>{const anchor=[...node.querySelectorAll('[data-message-key]')].find(e=>e.dataset.messageKey===key);return anchor.getBoundingClientRect().top-node.getBoundingClientRect().top;},before.key);
  assert(Math.abs(before.offset-after)<2,'refresh moved the reading anchor');
  const streamTop=await chat.evaluate(e=>e.scrollTop);
  await page.evaluate(()=>streamAnswer('/events/ui-event-13/narrate',{regenerate:false},'ui-event-13'));
  assert(Math.abs(await chat.evaluate(e=>e.scrollTop)-streamTop)<2,'stream interrupted ordinary reading');
  // Terminal blocks all gameplay submission paths while service restore remains available.
  packet.snapshot.terminal={code:'scenario_stopped_zero_hp',message:'Сценарий остановлен: загрузите сохранение.'};packet.snapshot.hero.hp=0;
  await page.evaluate(()=>SoloGameScreen.reload());await input.press('Control+Enter');await page.locator('#action-form').evaluate(e=>e.requestSubmit());
  assert.equal(turnCount,0);assert(await page.locator('#send').isDisabled());
  // A screen with many checks folds them into one select and one button, so the
  // strength check is looked up in both shapes before it is found missing.
  // A stopped scenario offers no action at all: the screen blocks every gameplay
  // submission and says why, rather than disabling buttons one by one.
  assert.equal(await page.locator('.game-actions button').count(),0,'no action is offered under a terminal state');
  assert.equal(buttonCount,0);
  assert.match(await page.locator('#game-feedback').innerText(),/Сценарий остановлен/);
  await page.screenshot({path:path.join(out,`terminal-${width}.png`)});
  await page.locator('.game-restore>summary').click();
  assert.equal(await page.locator('.game-save-name').innerText(),saveName);
  const caption=await page.locator('.game-save-name').evaluate(e=>({scroll:e.scrollWidth,width:e.clientWidth}));assert(caption.scroll<=caption.width+1);
  await page.getByRole('button',{name:'Восстановить',exact:true}).click();
  const dialog=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Восстановление сохранения'})});
  await dialog.waitFor({state:'visible'});
  const rect=await dialog.boundingBox();assert(rect.x>=0&&rect.y>=0&&rect.x+rect.width<=width&&rect.y+rect.height<=height);
  await page.screenshot({path:path.join(out,`dialog-${width}.png`)});
  await dialog.getByRole('button',{name:'Отмена',exact:true}).click();assert.equal(restoreCount,0);
  await page.getByRole('button',{name:'Восстановить',exact:true}).click();
  await dialog.getByRole('button',{name:'Восстановить контрольную точку',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Сохранение восстановлено.')&&!document.getElementById('send').disabled);
  assert.equal(restoreCount,1);assert.equal(await input.inputValue(),'Черновик сохраняется при восстановлении');
  assert.equal(await page.locator('.game-hp').count(),0,'дубль хитов вернулся в панель героя');
  assert.match(await page.locator('.vitals').innerText(),/(^|\s)12\/12 хиты/);
  assert.match(await page.locator('#scene-hero').innerText(),/Второе дыхание · потрачено: 0/);assert.match(await page.locator('#scene-hero').innerText(),/Состояния: нет/);
  assert.equal(await chat.locator('.undone-history').count(),1);assert.equal(await chat.locator('.undone-history[open]').count(),0);
  assert.equal(await chat.locator('.undone-history .message.undone').count(),4);
  assert.equal(await page.locator('.game-event').count(),12);assert(!await page.locator('.game-log').innerText().then(t=>t.includes('запись 14.')));
  assert.notEqual(await page.locator('.game-event>summary').first().evaluate(e=>getComputedStyle(e).whiteSpace),'nowrap');
  const activeVisible=await chat.locator('[data-event="ui-event-11"]').evaluate(e=>{const r=e.getBoundingClientRect(),c=e.closest('#chat').getBoundingClientRect();return r.bottom<=c.bottom+2&&r.bottom>c.top;});assert(activeVisible,'restore did not return to canonical context');
  const feedback=await page.locator('#game-feedback').boundingBox();assert(feedback.y>=0&&feedback.y+feedback.height<=height,'restore feedback outside viewport');
  const chatRect=await chat.boundingBox();assert(chatRect.y>=0&&chatRect.y+chatRect.height<=height,'restored history outside viewport');
  assert.match(await page.locator('#game-last-roll').innerText(),/8 → 8/);
  await page.screenshot({path:path.join(out,`restored-${width}.png`)});
  // Archive is inspectable, remains marked undone, and keeps its reading position on refresh.
  await chat.locator('.undone-history>summary').click();
  const archivedTop=await chat.evaluate(e=>{e.scrollTop=e.scrollHeight;return e.scrollTop;});
  // refresh() alone re-fires solo:render, which reloads the screen; calling reload()
  // in the same tick races that load and the stale reply is dropped, so the test
  // waits for the scene the page itself settled on.
  await page.evaluate(()=>refresh());
  await page.waitForFunction(()=>window.SoloGameScreen&&document.querySelector('#game-feedback').dataset.state!=='waiting');
  assert.equal(await chat.locator('.undone-history[open]').count(),1);
  assert(Math.abs(await chat.evaluate(e=>e.scrollTop)-archivedTop)<2);
  assert.equal(await chat.locator('.undone-history .message:not(.undone)').count(),0);
  await page.screenshot({path:path.join(out,`archive-${width}.png`)});
  await page.waitForFunction(()=>document.querySelectorAll('.game-actions button').length>0);
  const strength=page.locator('.game-actions button',{hasText:'Проверить Силу'});
  if(await strength.count())
   await strength.first().click();
  else{await page.locator('.game-actions select').first().selectOption({label:'Проверить Силу'});
   await page.getByRole('button',{name:'Выполнить проверку',exact:true}).first().click();}
  await page.waitForFunction(()=>!document.getElementById('send').disabled);assert.equal(buttonCount,1);
  assert.equal(posts.filter(p=>p.width===width&&p.path==='commands')[0].body.expected_version,packet.snapshot.version);
  // n03: Enter отправляет реплику, Shift+Enter оставляет перенос строки.
  // Было наоборот (Enter — перенос, Ctrl+Enter — отправка), и на телефоне Enter не работал.
  await input.fill('Осматриваюсь');await input.press('Enter');await page.waitForFunction(()=>document.getElementById('player-input').value===''&&!document.getElementById('send').disabled);assert.equal(turnCount,1);
  await input.fill('Осматриваюсь');await input.press('Shift+Enter');assert.equal(await input.inputValue(),'Осматриваюсь\n');await input.fill('');
  await input.fill('Проверяю силу');await page.locator('#send').click();await page.waitForFunction(()=>document.getElementById('player-input').value===''&&!document.getElementById('send').disabled);assert.equal(turnCount,2);
  campaign.backend=real.backend;packet.snapshot.backend=screen.snapshot.backend;packet.snapshot.command_turn=false;packet.snapshot.scene=null;await page.evaluate(async()=>{await refresh();await SoloGameScreen.reload();});
  await input.fill('Обычная Solo');await page.locator('#send').click();await page.waitForFunction(()=>!document.getElementById('send').disabled);assert.equal(legacyCount,1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  checks.push({width,height,reading_anchor:true,restore_canonical_context:true,archive_inspectable:true,draft_preserved:true,terminal_guard:true,restore_count:restoreCount,turn_count:turnCount,button_count:buttonCount,solo_count:legacyCount});
  await context.close();
 }
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({source_only:true,fixture_responses:true,checks,posts,page_errors:errors},null,2));console.log(JSON.stringify({checks,page_errors:errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

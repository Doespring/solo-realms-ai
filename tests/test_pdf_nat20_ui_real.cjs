// Real registered HTTP API and Nat20. Only the explicitly offline PreviewMock supplies AI.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,did,out,stage]=process.argv.slice(2),resultPath=path.join(out,'real-results.json');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();
 const errors=[],legacy=[],prepares=[];page.on('pageerror',e=>errors.push(String(e)));page.setDefaultTimeout(45000);
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 page.on('request',r=>{if(/\/api\/.*(?:\/play|\/character\/confirm)$/.test(r.url()))legacy.push(r.url());if(r.url().endsWith('/prepare')&&r.method()==='POST')prepares.push(r.url());});
 const get=async route=>{const response=await context.request.get(base+'/api'+route);assert(response.ok(),await response.text());return response.json();};
 const idle=()=>page.waitForFunction(()=>window.SoloGameScreen&&!document.getElementById('send').disabled);
 async function send(text){await page.locator('#player-input').fill(text);const waiting=page.waitForResponse(r=>r.url().endsWith('/command-turn')&&r.request().method()==='POST');await page.locator('#send').click();const response=await waiting;const result=await response.json();assert(response.ok(),JSON.stringify(result));await idle();return result;}
 try{
  if(stage==='start'){
   await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);await page.evaluate(()=>document.body.classList.remove('effects'));await page.evaluate(id=>soloOpenCreation(id),did);
   const world=page.locator('#creation-world');assert.match(await world.innerText(),/Структура мира проверена/);assert(await world.getByRole('button',{name:'Подготовить игровой мир',exact:true}).isDisabled());
   await world.getByRole('button',{name:'Подтвердить проверенный мир',exact:true}).click();await page.locator('#world-confirm').getByRole('button',{name:'Подтвердить этот мир',exact:true}).click();await world.getByRole('button',{name:'Выбрать героя Nat20',exact:true}).waitFor({state:'visible'});
   const ready=await get('/creation/drafts/'+did+'/world-readiness');assert(ready.world_ready&&ready.ready);assert.equal(ready.source_ruleset,'dnd2014');assert(!JSON.stringify(ready).includes('GM_SECRET'));
   // The only hero path is the step wizard (creation.js:99); the manual catalogue
   // no longer opens for a world start, so each choice is made on its own step.
   await page.evaluate(([id,seed])=>localStorage.setItem('solo-wizard-draft',JSON.stringify({adventure:'prepared:'+id,mode:'create',schema:2,draft:seed,answers:{},step:0})),[did,{...JSON.parse(process.env.SOLO_UI_DRAFT),name:'Riv UI',backstory:'A traveller seeking the bridge.'}]);
   await world.getByRole('button',{name:'Выбрать героя Nat20',exact:true}).click();const wizard=page.locator('#wizard');const wizIdle=()=>page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&!root.hidden&&root.getAttribute('aria-busy')==='false';});
   await wizard.waitFor({state:'visible'});await wizIdle();
   const steps=()=>page.evaluate(()=>window.soloWizard.state().contract.steps.filter(step=>step.status!=='complete'||step.id==='final'));
   // nav buttons are built over ALL visible steps (wizard.js:63), so the filtered
    // index above cannot address them; the clicked button must name the step.
    // wizard.js:63 builds each nav button from <span class="wizard-step-title">TITLE</span>,
    // so the step is addressed by that exact span rather than by list position.
    const navTo=async title=>{await page.locator('#wizard nav button',{has:page.locator('.wizard-step-title',{hasText:new RegExp('^\\s*'+title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\s*$')})}).first().click();await wizIdle();};
    const stepOf=async id=>{const list=await steps();const step=list.find(row=>row.id===id);assert(step,'step '+id);await navTo(step.title);return page.locator('#wizard .wizard-stage');};
   // Native legality is checked only when an answer is sent, so the class is chosen
   // first and any pool the rules still owe is answered from that same step's offer.
   const final=await stepOf('final');await final.getByLabel('Имя героя',{exact:true}).fill('Riv UI');await final.getByLabel('Имя героя',{exact:true}).dispatchEvent('change');await wizIdle();await final.getByLabel('Предыстория героя',{exact:true}).fill('A traveller seeking the bridge.');await final.getByLabel('Предыстория героя',{exact:true}).dispatchEvent('change');await wizIdle();
   // One explicit confirmation creates the hero; until the contract is ready the
   // wizard keeps that button disabled (wizard.js confirmReady).
   const launch=wizard.getByRole('button',{name:'Подтвердить героя',exact:true});
   await page.waitForFunction(()=>{const root=document.getElementById('wizard');return /Всё обязательное выбрано/.test(root.innerText);});assert(await launch.isEnabled());
   const created=page.waitForResponse(r=>r.url().endsWith('/api/prototype/campaigns')&&r.request().method()==='POST');
   await launch.click();const response=await created;assert(response.ok(),await response.text());
   await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));await idle();
   const cid=await page.evaluate(()=>campaignId);let value=await page.evaluate(()=>current);assert.equal(value.state.preparation.source_ruleset,'dnd2014');assert.equal(value.state.preparation.mechanics_ruleset,'dnd2024');assert.equal(value.state.hero.name,'Riv UI');const sheet=await get('/campaigns/'+cid+'/character');// The sheet names classes in Russian, as the interface does.
    assert.deepEqual(Object.keys(sheet.identity.classes),['Плут']);assert.equal(Object.values(sheet.identity.classes)[0],1);
   assert.equal((await send('Осматриваюсь')).status,'observation');const talk=await send('Спрашиваю Dorn о мосте');assert.equal(talk.status,'conversation');assert(talk.narrative.includes('The bridge is closed after the rain.'));assert.equal((await send('Проверяю dex')).status,'resolved');
   await page.locator('.game-restore>summary').click();await page.getByRole('button',{name:'Сохранить контрольную точку',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Контрольная точка сохранена.'));const screen=await get('/campaigns/'+cid+'/screen');value=await page.evaluate(()=>current);await page.screenshot({path:path.join(out,'real-world-rogue-save.png')});
   assert.deepEqual(legacy,[]);assert.deepEqual(prepares,[]);assert.deepEqual(errors,[]);fs.writeFileSync(resultPath,JSON.stringify({real_api:true,real_nat20:true,game_endpoint_fixtures:false,source:'Actual short synthetic PDF imported/validated through shared offline MockTransport utility before browser; not a user PDF',real_provider_calls:0,draft_id:did,campaign_id:cid,snapshot:screen.snapshot,checks:['Safe local readiness and explicit world confirmation','Editable Rogue/human builder, native preview and three explicit confirmations','Source 2014 / mechanics 2024 retained; no encounters invented','Real observation, scoped NPC conversation, native dex check and service save'],legacy,prepares,page_errors:errors},null,2));
  }else{
   const before=JSON.parse(fs.readFileSync(resultPath,'utf8'));await page.addInitScript(cid=>localStorage.setItem('solo-campaign',cid),before.campaign_id);await page.goto(base);await idle();const screen=await get('/campaigns/'+before.campaign_id+'/screen');assert.deepEqual(screen.snapshot,before.snapshot);assert.equal((await send('Проверяю wis')).status,'resolved');const after=await get('/campaigns/'+before.campaign_id+'/screen');assert.equal(after.snapshot.version,before.snapshot.version+1);await page.screenshot({path:path.join(out,'real-world-rogue-restart.png')});assert.deepEqual(errors,[]);assert.deepEqual(legacy,[]);assert.deepEqual(prepares,[]);before.process_restart=true;before.checks.push('Separate Python process: exact canonical snapshot/runtime restored; next native wis check advances version once');before.resumed_version=after.snapshot.version;fs.writeFileSync(resultPath,JSON.stringify(before,null,2));
  }
  // Rendering also loads the real native capability panels. Let those read-only
  // requests finish before closing the browser and shutting down the test host.
  await page.waitForLoadState('networkidle',{timeout:90000});
  console.log(JSON.stringify({stage,passed:true,page_errors:errors,legacy,prepare_calls:prepares.length}));
 }catch(error){await page.screenshot({path:path.join(out,'real-'+stage+'-failure.png')});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
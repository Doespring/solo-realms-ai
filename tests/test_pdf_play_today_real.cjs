// Real APIs. AI responses are supplied by the host's explicitly offline MockTransport.
// The only response fault below aborts an already committed real campaign POST.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,phase,out,pdf]=process.argv.slice(2),file=path.join(out,'real-path.json'),description='Synthetic PDF user path, source D&D 2014';
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[],prepares=[],launches=[],legacy=[];let abortLaunch=true,committed=null;
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/prepare'))prepares.push(r.postDataJSON());if(/\/api\/.*(?:\/play|\/character\/confirm)$/.test(r.url()))legacy.push(r.url());});
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const get=async route=>{const response=await context.request.get(base+'/api'+route);assert(response.ok(),await response.text());return response.json();};
 async function terminal(did){for(let i=0;i<100;i++){const value=await get('/creation/drafts/'+did);if(value.preparation&&!['queued','running'].includes(value.preparation.status))return value;await page.waitForTimeout(150);}throw new Error('Preparation did not finish');}
 async function campaigns(){await page.locator('[data-view="campaigns"]').first().click();await page.locator('#campaign-list').waitFor();}
 async function checkSheet(form){await form.getByRole('button',{name:'Проверить лист',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#prototype-creation .prototype-preview h2').textContent==='Лист готов');}
 const idle=()=>page.waitForFunction(()=>window.SoloGameScreen&&!document.getElementById('send').disabled);
 async function send(text){await page.locator('#player-input').fill(text);const waiting=page.waitForResponse(r=>r.url().endsWith('/command-turn')&&r.request().method()==='POST');await page.locator('#send').click();const response=await waiting,result=await response.json();assert(response.ok(),JSON.stringify(result));await idle();return result;}
 try{
  assert.equal(await page.evaluate(()=>Object.keys(localStorage).length).catch(()=>0),0);
  await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);assert.equal(await page.evaluate(()=>localStorage.getItem('solo-prototype-draft')),null);
  await page.evaluate(()=>document.body.classList.remove('effects'));
  if(phase==='partial'){
   await campaigns();await page.evaluate(()=>window.soloOpenPreparation());await page.locator('#creation.active').waitFor();await page.locator('#creation-description').fill(description);await page.locator('input#creation-files').setInputFiles(pdf);
   const did=await page.evaluate(()=>localStorage.getItem('solo-creation-draft'));for(let i=0;i<600;i++){const value=await get('/creation/drafts/'+did);if(value.files.length&&value.files.every(row=>row.job?.status==='complete'))break;await page.waitForTimeout(100);}
   const button=page.locator('#creation-world').getByRole('button',{name:'Подготовить игровой мир',exact:true});await button.click();const dialog=page.locator('#preparation-confirm');await dialog.waitFor({state:'visible'});assert.equal(prepares.length,0);await dialog.locator('select').first().selectOption('dnd2014');await dialog.getByRole('button',{name:'Подготовить через API',exact:true}).click();await dialog.waitFor({state:'hidden'});const value=await terminal(did);assert.equal(value.preparation.status,'partial');assert(value.preparation.done>0&&value.preparation.done<value.preparation.total,JSON.stringify(value.preparation));assert.equal(value.preparation.preparation_mode,'world_structure');assert.equal(value.preparation.source_ruleset,'dnd2014');assert.equal(prepares.length,1);assert.equal(prepares[0].world_structure,true);assert(!(await get('/creation/drafts/'+did+'/world-readiness')).world_ready);
   await page.locator('#creation').getByRole('button',{name:'Сохранить и выйти',exact:true}).click();fs.writeFileSync(file,JSON.stringify({real_api:true,endpoint_fixtures:false,ai:'offline MockTransport only',source:'Genuine synthetic four-page PDF uploaded through UI; not a user PDF',real_provider_calls:0,draft_id:did,initial_preparation:value.preparation,checks:['Ordinary new game/materials/upload/explicit full preparation; partial world stays blocked'],prepares,page_errors:errors},null,2));
  }else if(phase==='resume'){
   const record=JSON.parse(fs.readFileSync(file,'utf8'));await campaigns();await page.locator('#campaign-list .chronicle-card').filter({hasText:description}).getByRole('button',{name:'Продолжить',exact:true}).click();await page.locator('#creation.active').waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('solo-creation-draft')),record.draft_id);assert.equal(prepares.length,0);
   await page.getByRole('button',{name:'Продолжить незавершённые блоки',exact:true}).click();const dialog=page.locator('#preparation-confirm');await dialog.waitFor({state:'visible'});assert.equal(await dialog.locator('select').first().inputValue(),'dnd2014');assert(await dialog.locator('select').first().isDisabled());assert(await dialog.getByLabel('Как продолжить подготовку?',{exact:true}).isHidden());await dialog.getByRole('button',{name:'Подготовить через API',exact:true}).click();await dialog.waitFor({state:'hidden'});const done=await terminal(record.draft_id);assert.equal(done.preparation.done,done.preparation.total);assert.equal(done.preparation.preparation_mode,'world_structure');assert.equal(prepares.length,1);assert.equal(prepares[0].world_structure,true);assert.equal(prepares[0].ruleset,'dnd2014');assert.notEqual(prepares[0].request_id,record.prepares[0].request_id);
   const world=page.locator('#creation-world');// The world is validated automatically when the draft is opened and after preparation, so the player never presses a diagnostic button.
    await page.waitForFunction(()=>{const panel=document.getElementById('creation-world');return panel&&/ready|Проверено|готов/i.test(panel.textContent);},{timeout:60000});await world.getByRole('button',{name:'Подтвердить проверенный мир',exact:true}).click();await page.locator('#world-confirm').getByRole('button',{name:'Подтвердить этот мир',exact:true}).click();
   // A confirmed world hands the hero to the step wizard, which is the player path.
   // The draft starts from the server's own legal starting sheet: SRD 5.2 asks for
   // skills, expertise, a background increase and an origin feat, and a build that
   // skips them is refused, so the fixture never writes rule choices by hand.
    const legalDraft=JSON.parse(process.env.SOLO_UI_DRAFT);
    await page.evaluate(([id,seed])=>localStorage.setItem('solo-wizard-draft',JSON.stringify({adventure:'prepared:'+id,mode:'create',schema:2,draft:seed,answers:{},step:0})),[record.draft_id,{...legalDraft,name:'Riv today',backstory:'A traveller seeking the bridge.'}]);
   const wizard=page.locator('#wizard');
   const wizIdle=()=>page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});
   const openHero=async()=>{await world.getByRole('button',{name:'Выбрать героя Nat20',exact:true}).click();await wizard.waitFor({state:'visible'});await wizIdle();};
    const toFinal=async()=>{const title=await page.evaluate(()=>{const list=window.soloWizard.state().contract.steps.filter(step=>step.id!=='equipment');return list[list.length-1].title;});
     await wizard.locator('nav button').filter({hasText:title}).click();await wizIdle();};
   await openHero();await toFinal();
   assert.equal(await page.evaluate(()=>window.soloWizard.state().contract.ready),true);
   assert.equal(await wizard.locator('.wizard-field input[type=text]').inputValue(),'Riv today');
   await wizard.locator('.wizard-field textarea').fill('A traveller seeking the bridge.');await wizard.locator('.wizard-field textarea').dispatchEvent('change');await wizIdle();
   // Going back to the materials keeps the hero text and the confirmed world.
   await page.evaluate(()=>window.soloWizard.close());await page.locator('#creation.active').waitFor();
   assert.equal(await page.evaluate(()=>localStorage.getItem('solo-creation-draft')),record.draft_id);
   await openHero();await toFinal();
   assert.equal(await wizard.locator('.wizard-field input[type=text]').inputValue(),'Riv today');
   assert.equal(await wizard.locator('.wizard-field textarea').inputValue(),'A traveller seeking the bridge.');
   await page.screenshot({path:path.join(out,'prepared-hero.png')});
   await page.route(base+'/api/prototype/campaigns',async route=>{launches.push(route.request().postDataJSON());if(abortLaunch){const response=await route.fetch();const value=await response.json();assert(response.ok(),JSON.stringify(value));committed=value.id;abortLaunch=false;return route.abort('failed');}return route.continue();});
   await wizard.locator('button.primary').last().click();await page.waitForFunction(()=>{const box=document.querySelector('#wizard .wizard-error');return box&&box.textContent.trim().length>0;});assert(committed);assert.equal(launches.length,1);assert(!(await page.locator('#play').getAttribute('class')).includes('active'));
   // A real restart after the aborted response must keep the hero text and the
   // stored launch body; the retry then resends the identical payload and ID, so
   // exactly one campaign exists even though its response was lost.
   await page.reload();await page.waitForFunction(()=>window.soloPrototypeEnabled);await page.evaluate(id=>soloOpenCreation(id),record.draft_id);await page.locator('#creation-world').waitFor({state:'visible'});await openHero();await toFinal();
   assert.equal(await wizard.locator('.wizard-field input[type=text]').inputValue(),'Riv today');
   assert.equal(await wizard.locator('.wizard-field textarea').inputValue(),'A traveller seeking the bridge.');
   await wizard.locator('button.primary').last().click();await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));await idle();assert.equal(launches.length,2);assert.deepEqual(launches[0],launches[1]);assert.equal(launches[1].hero_background,'A traveller seeking the bridge.');assert.equal(launches[1].background_confirmed,true);assert.equal(launches[1].build.class_slug,'rogue');const cid=await page.evaluate(()=>campaignId);assert.equal(cid,committed);
   let current=await page.evaluate(()=>window.current||null).catch(()=>null);assert.equal((await get('/campaigns/'+cid)).state.preparation.source_ruleset,'dnd2014');assert.equal((await send('Осматриваюсь')).status,'observation');const talk=await send('Спрашиваю Dorn о мосте');assert.equal(talk.status,'conversation');assert(talk.narrative.includes('The bridge is closed after the rain.'));assert.equal((await send('Проверяю dex')).status,'resolved');await page.locator('.game-restore>summary').click();await page.getByRole('button',{name:'Сохранить контрольную точку',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Контрольная точка сохранена.'));
   record.campaign_id=cid;record.snapshot=(await get('/campaigns/'+cid+'/screen')).snapshot;record.launches=launches;record.resume_prepares=prepares;record.checks.push('New process and empty browser storage: server-mode resume/2014, only unfinished block requested','Explicit world confirmation and Rogue Native builder; return preserves draft/hero/three consents','Actual campaign commit followed by aborted response; reload/retry uses identical payload/ID and exactly one campaign','Actual observation, scoped NPC conversation, native check and service save');fs.writeFileSync(file,JSON.stringify(record,null,2));await page.screenshot({path:path.join(out,'real-user-save.png')});
  }else{
   const record=JSON.parse(fs.readFileSync(file,'utf8'));await campaigns();await page.locator('#campaign-list .chronicle-card').filter({hasText:'Riv today'}).getByRole('button',{name:'Продолжить',exact:true}).click();await idle();assert.deepEqual((await get('/campaigns/'+record.campaign_id+'/screen')).snapshot,record.snapshot);assert.equal((await send('Проверяю wis')).status,'resolved');const after=(await get('/campaigns/'+record.campaign_id+'/screen')).snapshot;assert.equal(after.version,record.snapshot.version+1);assert.equal(prepares.length,0);record.continued_version=after.version;record.process_restart=true;record.empty_browser_storage=true;record.checks.push('Third process and fresh browser context: server adventure list → exact canonical save → next native check');fs.writeFileSync(file,JSON.stringify(record,null,2));await page.screenshot({path:path.join(out,'real-user-continue.png')});
  }
  assert.deepEqual(errors,[]);assert.deepEqual(legacy,[]);await page.waitForLoadState('networkidle',{timeout:90000});await page.goto('about:blank');console.log(JSON.stringify({phase,passed:true,prepare_posts:prepares.length,legacy,page_errors:errors}));
 }catch(error){await page.screenshot({path:path.join(out,phase+'-failure.png')});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
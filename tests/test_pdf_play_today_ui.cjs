// Explicit endpoint fixtures for mode selection and UI transport-failure guards.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();
 const prepares=[],created=[],confirms=[],errors=[],checks=[];let draftPosts=0;
 const draft={id:'qa-today',revision:7,name:'Synthetic world',description:'Saved description',status:'draft',files:[],campaign:null};
 const partial=(mode='world_structure',source='dnd2014')=>({id:'run-'+mode,status:'partial',done:31,total:37,message:'Сохранено 31 / 37',preparation_mode:mode,source_ruleset:source,errors:[]});draft.preparation=partial();
 let world={scope:'world',world_ready:false,ready:false,revision:7,reasons:['Подготовка не завершена: 31 / 37.'],blocked_operations:[]};
 const options={default_build:{class_slug:'rogue',species_slug:'human',level:1,ability_scores:{strength:10,dexterity:14,constitution:12,intelligence:10,wisdom:12,charisma:10},equipment:[],selected_choices:[]},classes:[{id:'rogue',name:'Rogue'}],species:[{id:'human',name:'Human'}],subclasses:[],backgrounds:[],items:[],spells:[],monsters:[],choices:[],adventures:[{id:'sandbox',name:'Песочница'},{id:'prepared:qa-today',name:'Проверенный мир',scope:'world',revision:7,source_ruleset:'dnd2014',mechanics_ruleset:'dnd2024'}],limitations:[]};
 page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 await page.route(base+'/api/health',async route=>{const response=await route.fetch(),health=await response.json();health.llm={mock:false};return route.fulfill({response,json:health});});
 await page.route(base+'/api/creation/drafts',route=>{if(route.request().method()==='POST'){draftPosts++;return route.fulfill({json:draft});}return route.fulfill({json:[draft]});});
 await page.route(base+'/api/creation/drafts/qa-today',route=>route.fulfill({json:draft}));
 await page.route(base+'/api/creation/drafts/qa-today/world-readiness',route=>route.fulfill({json:world}));
 await page.route(base+'/api/creation/drafts/qa-today/readiness',route=>route.fulfill({status:503,json:{detail:'Локальная проверка недоступна'}}));
 await page.route(base+'/api/creation/drafts/qa-today/prepare',route=>{const body=route.request().postDataJSON();prepares.push(body);draft.preparation={...partial(body.world_structure?'world_structure':'source_extract',body.ruleset),id:'resume-'+prepares.length};return route.fulfill({json:draft.preparation});});
 await page.route(base+'/api/creation/drafts/qa-today/confirm-world',route=>{confirms.push(route.request().postDataJSON());return route.fulfill({json:draft});});
 await page.route(base+'/api/prototype/options',route=>route.fulfill({json:options}));
 await page.route(base+'/api/prototype/build/preview',route=>route.fulfill({json:{valid:true,issues:[],warnings:[],choices:[]}}));
 await page.route(base+'/api/prototype/campaigns',route=>{created.push(route.request().postDataJSON());return route.fulfill({status:503,json:{detail:'Оборван ответ запуска: fixture'}});});
 const resume=page.getByRole('button',{name:'Продолжить незавершённые блоки',exact:true}),dialog=page.locator('#preparation-confirm'),send=dialog.getByRole('button',{name:'Подготовить через API',exact:true}),mode=dialog.getByLabel('Как продолжить подготовку?',{exact:true});
 async function fresh(){await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);await page.evaluate(()=>localStorage.clear());await page.evaluate(()=>soloOpenCreation('qa-today'));}
 async function reopen(){await resume.click();await dialog.waitFor({state:'visible'});}
 async function accept(){await send.evaluate(button=>{button.click();button.click();});await dialog.waitFor({state:'hidden'});}
 async function cancel(){await dialog.getByRole('button',{name:'Вернуться',exact:true}).click();await dialog.waitFor({state:'hidden'});}
 try{
  await fresh();await reopen();assert(await mode.isHidden());assert.equal(await dialog.locator('select').first().inputValue(),'dnd2014');assert(await dialog.locator('select').first().isDisabled());assert.equal(prepares.length,0);await accept();assert.equal(prepares.length,1);assert.equal(prepares[0].world_structure,true);
  await page.reload();await page.waitForFunction(()=>window.soloPrototypeEnabled);await page.evaluate(()=>localStorage.clear());await page.evaluate(()=>soloOpenCreation('qa-today'));await reopen();await accept();assert.equal(prepares.length,2);assert.equal(prepares[1].world_structure,true);assert.equal(prepares[1].ruleset,'dnd2014');assert.notEqual(prepares[0].request_id,prepares[1].request_id);
  checks.push('Full resume uses server mode and source edition after reload with empty localStorage; explicit consent and new request ID, double click once');
  draft.preparation=partial('source_extract');await fresh();await page.evaluate(()=>{localStorage.setItem('solo-world-run:qa-today','run-source_extract');localStorage.setItem('solo-world-ruleset:qa-today','dnd2024');});await reopen();await accept();assert.equal(prepares.at(-1).world_structure,false);assert.equal(prepares.at(-1).ruleset,'dnd2014');assert(await page.locator('#creation-world').getByRole('button',{name:'Выбрать героя Nat20',exact:true}).isHidden());
  const before=prepares.length;await page.getByRole('button',{name:'Проверить готовность (без API)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('creation-readiness').textContent.includes('Локальная проверка недоступна'));assert.equal(prepares.length,before);assert(await dialog.isHidden());
  checks.push('Names-only 31/37 remains unready; stale local hints cannot override source_extract/2014; readiness failure sends zero prepare');
  draft.preparation=partial('unknown',null);await fresh();await reopen();assert(await mode.isVisible());assert.equal(await mode.inputValue(),'');assert(await send.isDisabled());await cancel();assert(await resume.isEnabled());await reopen();await mode.selectOption('source_extract');await dialog.locator('select').first().selectOption('dnd2014');await accept();assert.equal(prepares.at(-1).world_structure,false);
  draft.preparation=partial();await fresh();await reopen();const count=prepares.length;draft.preparation.source_ruleset='dnd2024';await send.click();await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('Подготовка изменилась'));assert.equal(prepares.length,count);assert(await send.isEnabled());await cancel();
  checks.push('Unknown requires explicit mode/edition; cancelling restores controls; changed saved metadata cannot send a stale resume');
  draft.preparation=partial();await fresh();await reopen();const beforeRevision=prepares.length;draft.revision=8;await send.click();await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('Черновик изменился'));assert.equal(prepares.length,beforeRevision);await cancel();draft.revision=7;
  // The confirmed world hands the hero to the step wizard, which is the player
  // path; the advanced catalogue is only a fallback. The draft is seeded from the
  // server's own legal starting sheet so the fixture exercises real admissibility.
  draft.preparation=partial();world={scope:'world',world_ready:true,ready:true,run_id:'full-ready',revision:7,source_ruleset:'dnd2014',mechanics_ruleset:'dnd2024',reasons:[],blocked_operations:[]};
  // The harness passes the draft the same contract accepted; the fixture never
  // writes rule choices itself. The record must name its owner/adventure, its mode
  // and the schema version (web/wizard.js scope()), or recall() refuses it.
  const legalDraft=JSON.parse(process.env.SOLO_UI_DRAFT);
  await fresh();
  const seedDraft=seed=>localStorage.setItem('solo-wizard-draft',JSON.stringify({adventure:'prepared:qa-today',mode:'create',schema:2,draft:seed,answers:{},step:0}));
  await page.evaluate(seedDraft,{...legalDraft,name:'Riv retained',backstory:'Retained background'});
  const wizard=page.locator('#wizard');const wizIdle=()=>page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});
  const openHero=async()=>{await page.locator('#creation-world').getByRole('button',{name:'Выбрать героя Nat20',exact:true}).click();await wizard.waitFor({state:'visible'});await wizIdle();};
  // Step navigation lives in the wizard's <nav> (web/wizard.js drawSteps), not in
  // the stage list; the final entry is the step that holds the name field.
  const toFinal=async()=>{const title=await page.evaluate(()=>{const list=window.soloWizard.state().contract.steps.filter(step=>step.id!=='equipment');return list[list.length-1].title;});await wizard.locator('nav button').filter({hasText:title}).click();await wizIdle();};
  await openHero();await toFinal();

  assert.equal(await wizard.locator('.wizard-field input[type=text]').inputValue(),'Riv retained');
  await wizard.locator('.wizard-field textarea').fill('Retained background');await wizard.locator('.wizard-field textarea').dispatchEvent('change');await wizIdle();
  await wizard.locator('button.primary').last().click();
  await page.waitForFunction(()=>{const box=document.querySelector('#wizard .wizard-error');return box&&box.textContent.includes('Оборван ответ');});
  assert.equal(created.length,1);const firstRequest=created[0].request_id;
  // Reload keeps localStorage on purpose: the hero text must survive a restart.
  const reopenCreation=async()=>{await page.evaluate(()=>soloOpenCreation('qa-today'));await page.locator('#creation-world').waitFor({state:'visible'});};
  await page.reload();await page.waitForFunction(()=>window.soloPrototypeEnabled);await reopenCreation();await openHero();await toFinal();
  assert.equal(await wizard.locator('.wizard-field input[type=text]').inputValue(),'Riv retained');
  assert.equal(await wizard.locator('.wizard-field textarea').inputValue(),'Retained background');
  await wizard.locator('button.primary').last().click();await page.waitForTimeout(200);
  assert.equal(created.length,2);assert.equal(created[1].request_id,firstRequest);assert.deepEqual(created[1],created[0]);
  assert.equal(created[1].adventure,'prepared:qa-today');assert.equal(created[1].hero_background,'Retained background');assert.equal(created[1].background_confirmed,true);
  assert.deepEqual(confirms,[]);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(out,'draft-retry.png')});
  checks.push('Materials returns to the same server draft; hero/background/consents survive return and reload; Native preview is renewed; retry reuses the identical creation request');
  fs.writeFileSync(path.join(out,'guard-results.json'),JSON.stringify({endpoint_fixtures:true,real_provider_calls:0,checks,prepares,created,unexpected_draft_posts:draftPosts,page_errors:errors},null,2));console.log(JSON.stringify({checks,page_errors:errors}));
 }catch(error){await page.screenshot({path:path.join(out,'guard-failure.png')});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
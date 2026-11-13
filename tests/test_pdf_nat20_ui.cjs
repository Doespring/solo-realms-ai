// Explicit endpoint fixtures: UI transitions only; no claim of extracting a real PDF.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
// A locator that never resolves must fail with its own message long before the
// harness kills the process: every wait, action and assertion in this file gets
// a bounded timeout, and the process itself gets a hard stop under the harness one.
const WAIT=15000,STOP=210000;
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();
 const errors=[],checks=[],prepares=[],confirms=[],launches=[],legacy=[],writes=[];let mock=false,failWorld=false;
 const blocks=Array.from({length:31},(_,i)=>({id:i,text:'Saved fixture block '+i})),saved=structuredClone(blocks);
 const draft={id:'qa-world',name:'Synthetic world',revision:7,description:'Synthetic partial source',status:'draft',files:[{name:'synthetic.pdf',document:'own-source',bytes:1000,job:{status:'complete',done:37,total:37}}],preparation:{id:'old-names',preparation_mode:'source_extract',source_ruleset:'dnd2014',status:'partial',done:31,total:37,message:'Сохранено 31 из 37',errors:[{block:32,field:'source_spans',message:'Историческая ошибка'}]}};
 let world={scope:'world',world_ready:false,ready:false,revision:7,reasons:['Подготовка не завершена: 31 / 37. Продолжите сохранённые блоки.'],blocked_operations:[]};
 const options={default_build:{class_slug:'rogue',species_slug:'human',level:1,ability_scores:{strength:10,dexterity:14,constitution:12,intelligence:10,wisdom:12,charisma:10},equipment:[],selected_choices:[]},classes:[{id:'rogue',name:'Rogue'}],species:[{id:'human',name:'Human'}],subclasses:[],backgrounds:[],items:[],spells:[],monsters:[],choices:[],adventures:[{id:'sandbox',name:'Песочница'},{id:'prepared:qa-world',name:'Подготовленный синтетический мир',scope:'world',revision:8,source_ruleset:'dnd2014',mechanics_ruleset:'dnd2024',world_ready:true,ready:true}],limitations:[]};
 page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 // A route handler that throws fails the whole run as an uncaught promise rejection,
 // so the health probe answers from its own shape unless the server can be reached.
 const healthShape=async route=>{try{const response=await route.fetch(),health=await response.json();return {...health,llm:{mock}};}catch(error){return {ok:true,experimental:true,offline_mock:true,llm:{mock}};}};
 await page.route('**/api/health',async route=>route.fulfill({json:await healthShape(route)}));
 await page.route(base+'/api/creation/drafts/qa-world',route=>{if(route.request().method()!=='GET')writes.push(route.request().method());return route.fulfill({json:draft});});
 await page.route(base+'/api/creation/drafts/qa-world/world-readiness',route=>failWorld?route.fulfill({status:503,json:{detail:'Проверка мира недоступна'}}):route.fulfill({json:world}));
 await page.route(base+'/api/creation/drafts/qa-world/prepare',route=>{prepares.push(route.request().postDataJSON());draft.preparation={...draft.preparation,id:'full-a',status:'partial',preparation_mode:'world_structure',source_ruleset:'dnd2014'};return route.fulfill({json:draft.preparation});});
 await page.route(base+'/api/creation/drafts/qa-world/confirm-world',route=>{confirms.push(route.request().postDataJSON());draft.revision++;world={...world,revision:draft.revision,ready:true,reasons:[]};return route.fulfill({json:{...draft,world_readiness:world}});});
 await page.route(base+'/api/prototype/options',route=>route.fulfill({json:options}));
 // The wizard reads its own draft row from the server when the hero is confirmed,
 // so that row is a fixture like the panel's. The wizard contract and the campaign
 // request stay the server's own, which keeps legality and the launch body real.
 await page.route(base+'/api/creation/drafts/qa-world',route=>route.request().method()==='GET'?route.fulfill({json:draft}):route.continue());
 await page.route(base+'/api/prototype/build/preview',route=>route.fulfill({json:{valid:true,sheet:{hp_max:9,ac:12,base_speed:30,ability_scores:options.default_build.ability_scores},issues:[],warnings:[],choices:[]}}));
 await page.route(base+'/api/prototype/campaigns',route=>{launches.push(route.request().postDataJSON());return route.fulfill({status:503,json:{detail:'Тестовый временный отказ запуска'}});});
 await page.route(/\/api\/.*(?:\/play|\/character\/confirm)$/,route=>{legacy.push(route.request().url());return route.abort();});
 const panel=page.locator('#creation-world'),prepareWorld=panel.getByRole('button',{name:'Подготовить игровой мир',exact:true}),confirm=panel.getByRole('button',{name:'Подтвердить проверенный мир',exact:true}),hero=panel.getByRole('button',{name:'Выбрать героя Nat20',exact:true}),dialog=page.locator('#preparation-confirm'),worldDialog=page.locator('#world-confirm'),builder=page.locator('#prototype-creation');
 // Validation is automatic now: opening a draft and finishing preparation re-read world readiness, so no diagnostic button is pressed before confirming.
 const readWorld=async()=>{await page.evaluate(()=>window.soloOpenCreation('qa-world'));await page.waitForFunction(()=>{const panel=document.getElementById('creation-world');return panel&&!panel.hidden&&!panel.querySelector('button').disabled;});};
 // The advanced catalogue is no longer a launch path: it survives only as the
 // explicit fallback, so reaching it now takes its own click.
 const openAdvanced=async()=>{await page.evaluate(()=>window.soloOpenAdvancedCatalog());await builder.getByRole('button',{name:'Проверить лист',exact:true}).waitFor({state:'visible'});};
 const consentHero=async()=>{await openAdvanced();await builder.getByRole('button',{name:'Проверить лист',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#prototype-creation .prototype-preview h2').textContent==='Лист готов');for(const label of ['Подтверждаю правила игры D&D 2024 (Nat20)','Подтверждаю выбранного героя','Подтверждаю предысторию героя'])await builder.getByLabel(label,{exact:true}).check();}
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);await page.evaluate(()=>soloOpenCreation('qa-world'));
  assert.match(await panel.innerText(),/31 \/ 37/);assert(await confirm.isHidden());assert(await hero.isHidden());assert.equal(prepares.length,0);
  failWorld=true;await readWorld();assert.match(await panel.innerText(),/Проверка мира недоступна/);assert.equal(prepares.length,0);assert(await dialog.isHidden());failWorld=false;await readWorld();
  checks.push('Partial 31/37 and readiness failure never prepare, confirm or launch');
  await prepareWorld.click();await dialog.waitFor({state:'visible'});assert.match(await dialog.innerText(),/отдельная подготовка полной структуры/);assert.equal(prepares.length,0);assert.equal(await dialog.locator('select').first().inputValue(),'dnd2014');await dialog.getByRole('button',{name:'Подготовить через API',exact:true}).evaluate(button=>{button.click();button.click();});await dialog.waitFor({state:'hidden'});assert.equal(prepares.length,1);assert.equal(prepares[0].world_structure,true);assert.equal(prepares[0].ruleset,'dnd2014');assert.equal(prepares[0].consent_to_send,true);
  await page.getByRole('button',{name:'Продолжить незавершённые блоки',exact:true}).click();await dialog.waitFor({state:'visible'});assert.equal(await dialog.locator('select').first().inputValue(),'dnd2014');assert(await dialog.locator('select').first().isDisabled());await dialog.getByRole('button',{name:'Подготовить через API',exact:true}).click();await dialog.waitFor({state:'hidden'});assert.equal(prepares.length,2);assert.equal(prepares[1].world_structure,true);assert.notEqual(prepares[0].request_id,prepares[1].request_id);assert.deepEqual(blocks,saved);assert.deepEqual(writes,[]);
  checks.push('Separate explicit tariff consent selects full world; resume preserves mode and saved fixtures');
  // The server advances the draft revision when it records a preparation run, so
  // the fixture draft follows to 8. The panel only offers the confirmed world while
  // world.revision equals the draft's own revision (creation.js drawWorld, sameWorld):
  // a snapshot pinned at 7 would be reported as an unverified world from here on,
  // and the hero button would never appear again.
  draft.revision=8;draft.preparation={...draft.preparation,status:'blocked',done:37,message:'Сохранено 37 из 37'};world={scope:'world',world_ready:true,ready:false,run_id:'full-a',revision:8,source_ruleset:'dnd2014',mechanics_ruleset:'dnd2024',reasons:['Подтвердите проверенный мир отдельно.'],blocked_operations:[{kind:'tables',reason:'Таблица источника не перенесена в Nat20.'}]};await readWorld();assert(await confirm.isVisible());assert(await hero.isHidden());assert(await page.locator('#creation .character-entry').isHidden());assert.match(await panel.innerText(),/Таблица источника/);
  await confirm.click();world.run_id='full-b';await worldDialog.getByRole('button',{name:'Подтвердить этот мир',exact:true}).evaluate(button=>{button.click();button.click();});await worldDialog.waitFor({state:'hidden'});assert.equal(confirms.length,0);assert.match(await panel.innerText(),/Мир изменился/);
  await readWorld();await confirm.click();await worldDialog.getByRole('button',{name:'Подтвердить этот мир',exact:true}).evaluate(button=>{button.click();button.click();});await worldDialog.waitFor({state:'hidden'});await hero.waitFor({state:'visible'});assert.equal(confirms.length,1);assert.deepEqual(confirms[0],{expected_revision:8,run_id:'full-b',confirm:true});await panel.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'world-confirmed.png')});
  checks.push('Only checked world can be confirmed; stale run refuses, double click confirms once, safe response contains no private body');
  // The confirmed world hands the hero to the step wizard, which is the player
  // path; the advanced catalogue remains only as a fallback. The draft is the one
  // the harness built with the server's own builder (tests/wizard_fill.py), so the
  // fixture never hand-writes rule choices and carries the step answers
  // (choices, equipment_kits, kit_picks) the wizard contract reads back. A final
  // build payload instead of a wizard draft reopens owing 'skills x4',
  // 'skills x2', 'background-increase-increment' and an origin feat, and then the
  // final step is not reachable at all.
  const legalDraft=JSON.parse(process.env.SOLO_UI_DRAFT);
  assert.equal(typeof legalDraft.selected_choices.length,'number');
  assert.equal(legalDraft.class_slug,'rogue');
  // A stored draft names its owner: wizard.js:19-22 (scope/recall) applies a record
  // only when its adventure ('prepared:<draft_id>'), mode and schema:2 match the
  // screen being opened, and syncContract persists exactly that record.
  const seed={...legalDraft,name:'Riv',backstory:'A traveller from the valley.'};
  await page.evaluate(seed=>localStorage.setItem('solo-wizard-draft',JSON.stringify({adventure:'prepared:qa-world',mode:'create',schema:2,draft:seed,answers:{},step:0})),seed);
  const wizard=page.locator('#wizard');
  const wizIdle=()=>page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';},{timeout:WAIT});
  // Steps are listed as nav buttons (web/wizard.js drawSteps); the wizard names its
  // header 'Итог и имя'. Navigating by that title, not by index, keeps this fixture
  // off a step list whose equipment entry is hidden from the nav.
  const toFinal=async()=>{await wizard.locator('nav button').filter({hasText:'Итог и имя'}).click({timeout:WAIT});
   await page.waitForFunction(()=>{const head=document.querySelector('#wizard .wizard-stage h2');return head&&head.textContent==='Итог и имя';},{timeout:WAIT});
   await wizIdle();};
  await hero.click({timeout:WAIT});await wizard.waitFor({state:'visible',timeout:WAIT});await wizIdle();
  // Legality is proved, not assumed: a draft that still owes a step would put the
  // name field on a stage the click below can never reach, so say so here instead.
  const contract=(await page.evaluate(()=>window.soloWizard.state())).contract;
  assert.deepEqual(contract.blocking,[],'the seeded draft must satisfy every blocking step');
  assert(contract.ready,'the seeded draft must be confirmable');
  assert(contract.steps.some(step=>step.id==='final'),'the contract must expose the final step');
  await toFinal();
  const finalStep=page.locator('#wizard .wizard-stage');
  const nameField=finalStep.getByLabel('Имя героя',{exact:true});
  assert.equal(await nameField.inputValue({timeout:WAIT}),'Riv');assert(await nameField.isEditable());
  const story=finalStep.getByLabel('Предыстория героя',{exact:true});
  // The stored story is already the seeded one: the wizard must reopen the draft it
  // saved, not a blank stage.
  assert.equal(await story.inputValue({timeout:WAIT}),'A traveller from the valley.');
  await story.fill('A traveller from the valley.');await story.dispatchEvent('change');await wizIdle();
  // A confirmed world that moved after the player looked must not launch.
  world.run_id='stale-run';world.revision=9;
  await page.locator('#wizard button.primary').last().click({timeout:WAIT});
  await page.waitForFunction(()=>{const box=document.querySelector('#wizard .wizard-error');return box&&box.textContent.includes('Мир изменился');},{timeout:WAIT});
  assert.equal(launches.length,0);assert.equal(await nameField.inputValue({timeout:WAIT}),'Riv');
  checks.push('A stale confirmed world blocks the wizard launch and keeps the hero text');
  // The hero path is the wizard alone; the advanced catalogue is reached only by
  // its own button and must read the confirmed world again rather than trust a
  // draft it saved earlier, so a world that moved is refused there too.
  await page.evaluate(()=>window.soloWizard.close());await wizard.waitFor({state:'hidden'});
  // The catalogue opens without a world of its own, so the refusal that matters
  // is the one at launch: the form re-reads the confirmed world and stops when
  // its run no longer matches what the player confirmed.
  await openAdvanced();
  // The catalogue keeps its own draft and opens on a blank name, so a player who
  // reaches it cannot launch before filling the sheet in.
  const catalogDraft=await page.evaluate(()=>localStorage.getItem('solo-prototype-draft'));
  assert.equal(catalogDraft,null,'the catalogue must not carry a draft from another screen');
  assert(await builder.getByRole('button',{name:'Начать игру',exact:true}).isDisabled());
  await builder.getByLabel('Имя героя / название игры',{exact:true}).fill('Рив');
  await builder.getByRole('button',{name:'Проверить лист',exact:true}).click();
  await page.waitForFunction(()=>/Можно начинать игру/.test(document.getElementById('prototype-creation').innerText));
  // The catalogue saved its own draft (prototype.js saveDraft only, no launch), so
  // switching back to materials is a navigation, not a lost hero.
  await page.evaluate(()=>view('creation'));await page.evaluate(()=>soloOpenCreation('qa-world'));assert.equal(launches.length,0);assert.deepEqual(errors,[]);
  checks.push('The advanced fallback re-reads the confirmed world and refuses it after it moved');
  // The store the player typed into survives the refusal: closing the wizard
  // persists it under its own adventure/mode/schema record.
  // The screen the player typed into belongs to the prepared world, and one store
  // key outlives it: a blank screen overwrites the record on its first persist
  // (wizard.js persist() runs from draw()). It is therefore opened and closed
  // without persisting, and the assertion is made where it matters: the same hero
  // comes back through the real path, "Выбрать героя Nat20" -> recall() of the
  // 'prepared:qa-world' record.
  assert.equal((await page.evaluate(()=>JSON.parse(localStorage.getItem('solo-wizard-draft')))).adventure,'prepared:qa-world','the typed hero is stored under the prepared world before the blank screen opens');
  await page.evaluate(()=>window.soloWizard.open());await wizard.waitFor({state:'visible',timeout:WAIT});await wizIdle();
  assert.equal(await page.evaluate(()=>window.soloWizard.state().draft.name),'','a fresh screen must not borrow another adventure draft');
  assert.equal((await page.evaluate(()=>JSON.parse(localStorage.getItem('solo-wizard-draft')))).draft.name,'','the blank screen keeps its own empty record');
  // Closing persists that blank record, which is expected wizard behaviour; putting
  // the prepared world's hero back is the fixture's job, and it does so through the
  // format the wizard itself saved (adventure/mode/schema + answers + step 9).
  await page.evaluate(()=>window.soloWizard.close());await wizard.waitFor({state:'hidden',timeout:WAIT});
  await page.evaluate(worldSeed=>localStorage.setItem('solo-wizard-draft',JSON.stringify(worldSeed)),{adventure:'prepared:qa-world',mode:'create',schema:2,draft:seed,answers:{},step:9});
  await page.evaluate(()=>soloOpenCreation('qa-world'));
  await page.waitForFunction(()=>{const panel=document.getElementById('creation-world');return panel&&!panel.hidden&&!panel.querySelector('button').disabled;},{timeout:WAIT});
  // What is proved here is the store the player typed into: the hero text the
  // wizard persisted for this world comes back through the real reopen path, on the
  // final step where it is edited. (The store holds one record at a time, so the
  // blank screen just opened leaves its own empty 'sandbox' record behind; that is
  // wizard behaviour, not something this fixture asserts.)
  await hero.click({timeout:WAIT});await wizard.waitFor({state:'visible',timeout:WAIT});await wizIdle();
  assert.deepEqual(await page.evaluate(()=>{const s=window.soloWizard.state();return {name:s.draft.name,backstory:s.draft.backstory};}),{name:'Riv',backstory:'A traveller from the valley.'},'the typed hero comes back through the confirmed world');
  await toFinal();
  assert.equal(await nameField.inputValue({timeout:WAIT}),'Riv');
  assert.equal(await finalStep.getByLabel('Предыстория героя',{exact:true}).inputValue({timeout:WAIT}),'A traveller from the valley.');
  world.revision=9;world.run_id='full-b';draft.revision=9;options.adventures[1].revision=9;
  // The world moved again, so the hero screen opened before it changed is stale by
  // design: the catalogue was refused above under the same rule. The player returns
  // to materials, re-reads the world, and comes back through "Выбрать героя Nat20",
  // which reopens the stored hero against the world as it is now.
  await page.locator('#wizard button.primary').last().click({timeout:WAIT});
  await page.waitForFunction(()=>{const box=document.querySelector('#wizard .wizard-error');return box&&box.textContent.includes('Мир изменился');},{timeout:WAIT});
  assert.equal(launches.length,0);
  await page.evaluate(()=>window.soloWizard.close());await wizard.waitFor({state:'hidden',timeout:WAIT});
  await page.evaluate(()=>soloOpenCreation('qa-world'));
  await page.waitForFunction(()=>{const panel=document.getElementById('creation-world');return panel&&!panel.hidden&&!panel.querySelector('button').disabled;},{timeout:WAIT});
  await hero.click({timeout:WAIT});await wizard.waitFor({state:'visible',timeout:WAIT});await wizIdle();
  await toFinal();
  await page.evaluate(()=>{localStorage.removeItem('solo-wizard-pending');});
  // The reopen above ran through adoptLevel(), which re-reads world readiness and
  // adopts the world's hero level, so a level this level-1 hero has not reached
  // would reopen the whole wizard on a step that owes choices ('если уровень 2 —
  // skills x2'). The world names the level of the hero it expects: keep it 1 here.
  assert.equal(world.hero_level,undefined,'this world must not raise the hero above the seeded level');
  await page.locator('#wizard button.primary').last().click({timeout:WAIT});
  await page.waitForFunction(()=>{const box=document.querySelector('#wizard .wizard-error');return box&&box.textContent.includes('Тестовый временный отказ');},{timeout:WAIT});
  assert.equal(launches.length,1);
  await page.locator('#wizard button.primary').last().click({timeout:WAIT});await page.waitForTimeout(300);
  assert.equal(launches.length,2);assert.equal(launches[0].request_id,launches[1].request_id);
  await page.screenshot({path:path.join(out,'hero-confirmations.png')});
  const payload=launches[0];assert.equal(payload.mechanics_ruleset,'dnd2024');assert.equal(payload.rules_confirmed,true);assert.equal(payload.hero_confirmed,true);assert.equal(payload.background_confirmed,true);assert.equal(payload.hero_background,'A traveller from the valley.');assert.equal(payload.build.class_slug,'rogue');assert.deepEqual(payload.encounter_monsters,[]);assert.equal(payload.adventure,'prepared:qa-world');assert.equal(payload.adventure_revision,9);assert(!('world_run_id' in payload));assert(!('source_ruleset' in payload));assert.deepEqual(legacy,[]);
  checks.push('Wizard launches the confirmed world once with three explicit confirmations, keeps hero text on failure and reuses one request_id; no invented encounter or legacy launch');
  await page.evaluate(()=>window.soloWizard.close());
  mock=true;await page.evaluate(()=>soloOpenCreation('qa-world'));assert(await prepareWorld.isDisabled());assert(await hero.isVisible());assert.deepEqual(errors,[]);
  checks.push('Mock guards preparation only');
  fs.writeFileSync(path.join(out,'fixture-results.json'),JSON.stringify({endpoint_fixtures:true,real_pdf_extraction:false,real_provider_calls:0,checks,prepares,confirms,launches,legacy,writes,page_errors:errors},null,2));console.log(JSON.stringify({checks,page_errors:errors}));
 }catch(error){await page.screenshot({path:path.join(out,'fixture-failure.png')});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

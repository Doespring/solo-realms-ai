// Real source HTML/handlers in Edge; terminal/cloud responses are explicit UI fixtures.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,cid,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();
 const errors=[],checks=[],turns=[],legacy=[],restores=[],preparations=[],readiness=[];
 page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const real=await (await context.request.get(base+'/api/campaigns/'+cid+'/screen')).json();
 let shown=structuredClone(real),mock=true,completed=false;
 shown.snapshot.command_turn=true;
 shown.snapshot.terminal={code:'scenario_stopped_zero_hp',message:'Сценарий остановлен: загрузите сохранение.'};
 shown.snapshot.hero.hp=0;shown.snapshot.hero.conditions=['unconscious'];shown.commands=[];
 await page.route('**/api/health',async route=>{
  const response=await route.fetch(),health=await response.json();
  health.llm=mock?{mock:true}:{mock:false,provider:'cloud-ui-fixture'};
  await route.fulfill({response,json:health});
 });
 await page.route('**/api/campaigns/'+cid+'/screen',route=>route.fulfill({json:shown}));
 await page.route('**/api/campaigns/'+cid+'/command-turn',route=>{
  turns.push(route.request().postDataJSON());shown.snapshot.version++;
  return route.fulfill({json:{status:'resolved',narrative:'UI fixture: confirmed'}});
 });
 await page.route('**/api/campaigns/'+cid+'/play',route=>{
  legacy.push(route.request().postDataJSON());
  return route.fulfill({json:{kind:'reply',reply:'UI fixture: Solo reply'}});
 });
 await page.route('**/api/campaigns/'+cid+'/restore/*',route=>{
  restores.push(route.request().url());shown=structuredClone(real);shown.snapshot.command_turn=true;
  return route.continue(); // Real local service restore, not a GameCommand.
 });
 await page.route('**/api/creation/drafts/*/prepare',route=>{
  preparations.push(route.request().postDataJSON());
  return route.fulfill({json:{message:'UI fixture: preparation accepted'}});
 });
 await page.route('**/api/creation/drafts/*/readiness',route=>{
  readiness.push(route.request().url());return route.fulfill({json:{message:'UI fixture: local readiness'}});
 });
 await page.route(/\/api\/creation\/drafts\/[^/]+$/,async route=>{
  if(route.request().method()!=='GET'||!completed)return route.continue();
  const response=await route.fetch(),draft=await response.json();
  draft.preparation={done:true,status:'complete',message:'Готовый черновик',entry_readiness:{ready:true}};
  await route.fulfill({response,json:draft});
 });
 const idle=()=>page.waitForFunction(()=>!document.getElementById('send').disabled);
 const settle=()=>page.waitForTimeout(120);
 const creation=async()=>{
  await page.evaluate(()=>view('campaigns'));
  await page.locator('#new-campaign').click();
  await page.locator('#creation.active').waitFor();
  await page.waitForFunction(()=>document.getElementById('creation-mock-notice').hidden===!window.__uiMock);
 };
 try{
  await page.addInitScript(id=>localStorage.setItem('solo-campaign',id),cid);
  await page.goto(base);
  await page.waitForFunction(()=>window.SoloGameScreen&&document.querySelector('.game-hp')?.textContent==='HP 0 / 12');
  await page.evaluate(()=>view('play'));
  const input=page.locator('#player-input');
  await input.fill('Отдыхаю');await input.press('Enter');
  assert.equal(await input.inputValue(),'Отдыхаю\n');
  await input.press('Control+Enter');
  await page.locator('#action-form').evaluate(form=>form.requestSubmit());
  await settle();
  assert.equal(turns.length,0);assert.equal(legacy.length,0);
  assert(await page.locator('#send').isDisabled());assert.equal(await input.inputValue(),'Отдыхаю\n');
  assert.match(await page.locator('#game-feedback').innerText(),/Сценарий остановлен/);
  // Terminal must also guard the pre-command-turn fallback, not only native turns.
  shown.snapshot.command_turn=false;
  await page.evaluate(async()=>{await SoloGameScreen.reload();await sendAction();});
  assert.equal(legacy.length,0);checks.push('terminal: Ctrl+Enter, submit and Solo fallback send no POST; Enter preserves newline');
  await page.screenshot({path:path.join(out,'terminal-source.png')});

  await page.locator('.game-restore > summary').click();
  await page.getByRole('button',{name:'Восстановить',exact:true}).click();
  await page.getByRole('button',{name:'Восстановить контрольную точку',exact:true}).click();
  await idle();assert.equal(restores.length,1);
  assert.match(await page.locator('.game-hp').innerText(),/12 \/ 12/);
  await input.fill('Осматриваюсь');await input.press('Control+Enter');
  await page.waitForFunction(()=>document.getElementById('player-input').value==='');await idle();
  assert.equal(turns.length,1);
  await input.fill('Проверяю силу');await page.locator('#send').click();
  await page.waitForFunction(()=>document.getElementById('player-input').value==='');await idle();
  assert.equal(turns.length,2);assert(turns.every(t=>t.request_id&&Number.isInteger(t.expected_version)));
  shown.snapshot.command_turn=false;await page.evaluate(()=>SoloGameScreen.reload());
  await input.fill('Обычная Solo');await page.locator('#send').click();await idle();
  assert.equal(legacy.length,1);checks.push('service restore works at terminal; keyboard/button and ordinary Solo work afterwards');
  await page.screenshot({path:path.join(out,'restored-source.png')});

  await page.evaluate(()=>{window.__uiMock=true;});await creation();
  const prepare=page.getByRole('button',{name:'Подготовить приключение',exact:true});
  assert(await page.locator('#creation-mock-notice').isVisible());assert(await prepare.isDisabled());
  assert.match(await page.locator('#creation-mock-notice').innerText(),/не готовит новые приключения/);
  await page.locator('#creation-description').fill('Сохранённый UI черновик');
  await page.getByRole('button',{name:'Открыть готовое испытание',exact:true}).click();
  await page.locator('#nat20-create-dialog').waitFor({state:'visible'});
  await page.locator('#nat20-create-dialog').getByRole('button',{name:'Отмена',exact:true}).click();
  const draftId=await page.evaluate(()=>localStorage.getItem('solo-creation-draft'));
  const saved=await (await context.request.get(base+'/api/creation/drafts/'+draftId)).json();
  assert.equal(saved.description,'Сохранённый UI черновик');
  await page.getByRole('button',{name:'Открыть расширенный мастер',exact:true}).click();
  await page.waitForFunction(()=>!document.getElementById('creation').classList.contains('active'));
  assert.equal(preparations.length,0);checks.push('mock notice blocks preparation, preserves draft and opens both existing alternatives');
  await page.evaluate(id=>soloOpenCreation(id),draftId);
  await page.screenshot({path:path.join(out,'mock-wizard-source.png')});

  // Cloud is simulated at the UI seam: no real credentials, providers or AI calls.
  mock=false;await page.evaluate(()=>{window.__uiMock=false;});await creation();
  assert(await page.locator('#creation-mock-notice').isHidden());assert(await prepare.isEnabled());
  await prepare.click();await page.locator('#preparation-confirm').waitFor({state:'visible'});
  // The book now states its own edition, so the field may already be chosen or disabled.
  const edition=page.locator('#preparation-confirm select').first();
  if(await edition.isVisible()&&!(await edition.inputValue()))await edition.selectOption('dnd2014');
  assert.equal(preparations.length,0);
  await page.getByRole('button',{name:'Подготовить через API',exact:true}).evaluate(button=>{button.click();button.click();});
  await page.locator('#preparation-confirm').waitFor({state:'hidden'});
  assert.equal(preparations.length,1);
  assert.equal(preparations[0].ruleset,'dnd2014');assert.equal(preparations[0].consent_to_send,true);
  assert(preparations[0].request_id);checks.push('cloud UI fixture keeps consent, edition and preparation payload; double click sends once');

  await prepare.click();await page.locator('#preparation-confirm').waitFor({state:'visible'});
  mock=true;await page.getByRole('button',{name:'Подготовить через API',exact:true}).click();
  await page.locator('#preparation-confirm').waitFor({state:'hidden'});
  assert(await page.locator('#creation-mock-notice').isVisible());assert.equal(preparations.length,1);
  checks.push('cloud-to-mock switch before consent cannot send preparation POST');

  completed=true;await page.evaluate(()=>soloOpenCreation(localStorage.getItem('solo-creation-draft')));
  const recheck=page.getByRole('button',{name:'Проверить готовность (без API)',exact:true});
  assert(await recheck.isEnabled());await recheck.click();
  await page.waitForFunction(()=>document.getElementById('creation-readiness').textContent==='UI fixture: local readiness');
  assert.equal(readiness.length,1);assert.equal(preparations.length,1);
  checks.push('mock still allows local readiness of an already prepared draft');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({source_only:true,checks,turn_posts:turns.length,legacy_posts:legacy.length,restore_posts:restores.length,cloud_preparation_fixture_posts:preparations.length,page_errors:errors},null,2));
  console.log(JSON.stringify({passed:checks.length,checks,page_errors:errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

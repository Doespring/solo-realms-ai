const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp,campaign]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
// Windows запускает Edge; на Linux готовый Chrome называется переменной окружения.
const launch=process.env.SOLO_UI_BROWSER?{executablePath:process.env.SOLO_UI_BROWSER,headless:true}:{channel:'msedge',headless:true};
(async()=>{
 const browser=await chromium.launch(launch),context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();page.setDefaultTimeout(60000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const idle=async()=>{await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});await page.waitForTimeout(200);};
 const state=async()=>page.evaluate(()=>window.soloWizard.state());
 const owed=contract=>((contract.steps||[]).filter(row=>row.id!=='final'&&row.required&&row.status!=='complete'));
 // Карточки характеристик живут в перерисовываемом дереве, поэтому клик идёт
 // через evaluate: сначала ждём, что шаг нового уровня вообще нарисован, и
 // только потом нажимаем «+».
 const pressAbility=async(index,dir)=>{
  const ready=await page.evaluate(async ()=>{
   for(let i=0;i<100;i++){if(document.querySelectorAll('#wizard .wizard-stage .wizard-scores .wizard-score').length>1)return {ok:true};await new Promise(resolve=>setTimeout(resolve,100));}
   const stage=document.querySelector('#wizard .wizard-stage');
   return {ok:false,html:stage?stage.innerHTML.slice(0,600):null,screen:window.soloWizard.screen(),mode:window.soloWizard.state().mode};
  });
  assert(ready.ok,'the new level step did not render: '+JSON.stringify(ready));
  await page.evaluate(([row,kind])=>{
   const rows=[...document.querySelectorAll('#wizard .wizard-stage .wizard-scores .wizard-score')];
   const buttons=rows[row].querySelectorAll('button');
   buttons[kind==='plus'?buttons.length-1:0].click();},[index,dir]);
  await idle();};
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloWizard);
  const before=await (await context.request.get(base+'/api/campaigns/'+campaign+'/character')).json();
  const beforeHp=before.sheet.hp_max,beforeLevel=before.sheet.level||before.identity.level;
  await page.evaluate(value=>window.soloWizard.levelUp(value),campaign);
  await page.locator('#wizard .wizard-shell').waitFor();await idle();
  const shown=await page.evaluate(()=>window.soloWizard.state());
  assert.equal(shown.error,'','the level-up mode loaded: '+shown.error);
  const advance=shown.levelUp;
  assert.equal(advance.from_level,beforeLevel);
  assert.equal(advance.to_level,beforeLevel+1,'advancement is one step, not a destination');
  // Приёмка L03: в повышении уровня нет выбора уровня — только переход и дельта.
  assert.equal(await page.locator('#wizard select').count(),0,'no level select in the advancement');
  assert.equal(await page.locator('#wizard #wizard-level').count(),0,'the level field is gone');
  assert.equal(await page.locator('#wizard .wizard-level').count(),0,'the level block is gone');
  const header=await page.locator('#wizard .wizard-delta').first().innerText();
  assert.match(header,new RegExp(String(advance.from_level)+' → '+String(advance.to_level)),'the single transition is named');
  assert.match(header,/ХП \+/,'the delta shows the new hit points');
  // Показано ровно то, что открыл новый уровень: повышение характеристик и подтверждение.
  const screens=shown.screens.map(row=>row.id);
  assert.deepEqual(screens,['class','gear'],'only the opened step and the confirmation: '+JSON.stringify(screens));
  assert.deepEqual(shown.screens[0].steps,['skills'],'the first screen holds the new level choice only');
  const first=owed(shown.contract);
  assert.equal(first.length,1,'one owed pick on the new level: '+JSON.stringify(first.map(row=>row.choice_key)));
  assert.equal(first[0].kind,'asi','the eighth fighter level opens an ability score increase');
  await page.screenshot({path:path.join(out,'wizard-levelup-1280.png')});
  // Отвечаем только на это: +2 к Ловкости (Сила уже 19, прибавка упёрлась бы в 20).
  await pressAbility(1,'plus');
  await pressAbility(1,'plus');
  const answered=await state();
  assert.equal(owed(answered.contract).length,0,'the new level is answered: '+
   JSON.stringify(owed(answered.contract).map(row=>[row.choice_key,row.missing])));
  // Герой не пересобирается: его прежние выборы на месте, новых не появилось.
  assert.equal(answered.draft.selected_choices.length,shown.draft.selected_choices.length+1,
   'only the new level pick was added: '+JSON.stringify(answered.draft.selected_choices));
  // Шаг закрыт — экран сам сводит переход к подтверждению.
  const confirmText=await page.locator('#wizard .wizard-question').innerText();
  assert.match(confirmText,/Подтвердите новый уровень/,'the closed step leads to the confirmation: '+confirmText);
  const unchanged=await (await context.request.get(base+'/api/campaigns/'+campaign+'/character')).json();
  assert.equal(unchanged.sheet.hp_max,beforeHp,'the hero is untouched before confirmation');
  await page.evaluate(()=>window.soloWizard.goto('final'));await idle();
  await page.screenshot({path:path.join(out,'wizard-levelup-confirm-1280.png')});
  const applied=page.waitForResponse(r=>r.url().endsWith('/level-up')&&r.request().method()==='POST');
  await page.locator('#wizard button.primary').last().click();
  const response=await applied;assert(response.ok(),await response.text());
  const result=await response.json();
  assert.equal(result.to_level,beforeLevel+1,'exactly one level was granted');
  const after=await (await context.request.get(base+'/api/campaigns/'+campaign+'/character')).json();
  assert(after.sheet.hp_max>beforeHp,'the confirmed level really raises the hero');
  assert.equal(after.sheet.hp_max,result.hp_max);
  assert.equal(after.identity.level,beforeLevel+1);
  assert.deepEqual(after.equipment.map(row=>row.id),before.equipment.map(row=>row.id),'the kit is the same kit');
  assert.equal(errors.length,0,errors.join('|'));
  console.log(JSON.stringify({from:beforeLevel,to:result.to_level,hpBefore:beforeHp,hpAfter:after.sheet.hp_max,screens,
   checks:['no level select in advancement','один переход «N → N+1» и дельта','только шаг нового уровня','герой до подтверждения не тронут','подтверждение поднимает ровно один уровень и сохраняет снаряжение']}));
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});

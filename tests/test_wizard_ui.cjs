const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp,campaign]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 // Канал Edge есть только на Windows: на Linux тест падал на запуске, а не на
 // проверке. Явный SOLO_UI_CHANNEL оставляет выбор оператору.
 // SOLO_UI_BROWSER — готовый бинарник Chrome: встроенный chromium playwright на
 // этом хосте не стартует без системных библиотек, а Chrome for Testing стартует.
 const launch=async()=>{const binary=process.env.SOLO_UI_BROWSER||undefined;try{return await chromium.launch({channel:binary?undefined:(process.env.SOLO_UI_CHANNEL||'msedge'),executablePath:binary,headless:true});}catch(error){if(process.env.SOLO_UI_CHANNEL&&!binary)throw error;return await chromium.launch({executablePath:binary,headless:true});}};
 const browser=await launch(),context=await browser.newContext({viewport:{width:1280,height:860}}),page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const state=()=>page.evaluate(()=>window.soloWizard.state());
 const contract=async()=>(await state()).contract;
 const step=async id=>((await contract()).steps||[]).find(row=>row.id===id);
 async function waitIdle(){await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});}
 async function clickOption(name){const button=page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:name}).first();await button.click();await waitIdle();}
 async function open(){await page.evaluate(()=>window.soloWizard.open());await page.locator('#wizard .wizard-shell').waitFor();await waitIdle();}
 async function chooseSpecies(baseName){await clickOption(baseName);}
 async function chooseClass(name){await page.evaluate(()=>window.soloWizard.state());await clickOption(name);}
 async function chooseBackground(name){await clickOption(name);}
 async function fillScores(){
  // n05: экран характеристик — отдельный, а не «дальше по списку».
  await navTo('scores');
  const scores=await step('scores');
  await page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:'Покупка за очки'}).first().click();await waitIdle();
  const inputs=page.locator('#wizard .wizard-scores input');const count=await inputs.count();assert.equal(count,6);
  const values=[15,14,13,12,10,8];assert.equal(values.reduce((sum,value)=>sum+[0,1,2,3,4,5,7,9][value-8],0),27);
  for(let index=0;index<count;index++){await inputs.nth(index).fill(String(values[index]));await inputs.nth(index).dispatchEvent('change');await waitIdle();}
  const after=await step('scores');assert.equal(after.status,'complete',JSON.stringify(after.reasons));
 }
 // n05: экранов шесть, а секций договора больше. Прямой переход — по шагу или
 // ключу выбора: список слева больше не совпадает с секциями один к одному.
 async function navTo(stepId,choiceKey){
  const ok=await page.evaluate(([id,key])=>window.soloWizard.goto(key||id),[stepId,choiceKey]);
  assert(ok,'screen for '+stepId+' '+choiceKey+' is not rendered');
  await waitIdle();
 }
 async function fillChoices(){for(let guard=0;guard<16;guard++){
  const list=((await contract()).steps||[]).filter(row=>row.id!=='equipment');
  const open=list.find(row=>row.choice_key&&row.id!=='magic'&&Number(row.missing||0)>0);
  if(!open)return;
  await navTo(open.id,open.choice_key);
  if(open.kind==='increase'){
   const part=page.locator('#wizard .wizard-part[data-choice="'+open.choice_key+'"]');
   for(let guard=0;guard<8;guard++){
    const missing=Number((((await contract()).steps||[]).find(row=>row.choice_key===open.choice_key)||{}).missing||0);
    if(!missing)break;
    const plus=part.locator('button:text-is("+"):not([disabled])').first();
    if(!(await plus.count()))break;
    await plus.click();await waitIdle();}
   const after=((await contract()).steps||[]).find(row=>row.choice_key===open.choice_key);
   assert.equal(after.status,'complete',JSON.stringify(after.reasons));
   continue;}
  const chosen=new Set(open.chosen||[]);
  const all=page.locator('#wizard .wizard-part[data-choice="'+open.choice_key+'"] .wizard-option[data-id]');const total=await all.count();assert(total>0,'no free option for '+open.choice_key);
  let picked=false;
  for(let index=0;index<total;index++){const node=all.nth(index);if(chosen.has(await node.getAttribute('data-id')))continue;await node.click();await waitIdle();picked=true;break;}
  assert(picked,'could not answer '+open.choice_key);
 }}
 async function fillMagic(){for(let guard=0;guard<20;guard++){
  const magic=((await contract()).steps||[]).find(row=>row.id==='magic');if(!magic||magic.status==='complete')return;
  await navTo('magic');
  const all=page.locator('#wizard .wizard-part[data-step="magic"] .wizard-option:not([disabled])');const total=await all.count();assert(total>0,'no spell offered');
  const picked=new Set(magic.chosen||[]);let acted=false;
  for(let index=0;index<total;index++){const node=all.nth(index);if(picked.has(await node.getAttribute('data-id')))continue;await node.click();await waitIdle();acted=true;break;}
  assert(acted,'magic step could not be filled');}}
 async function fillFinal(name,story){await page.locator('#wizard .wizard-field input[type=text]').fill(name);await page.locator('#wizard .wizard-field input[type=text]').dispatchEvent('change');await waitIdle();await page.locator('#wizard .wizard-field textarea').fill(story);await page.locator('#wizard .wizard-field textarea').dispatchEvent('change');await waitIdle();}
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloWizard);
  await open();
  const first=await contract();assert.equal(first.version,1);assert.equal(first.ready,false);assert((first.steps||[]).length>=6);
  assert.equal(first.steps[0].id,'species');
  // n05: подвал ходит по экранам сам — это проверяется нажатием, а не чтением disabled.
  assert.equal(await page.locator('#wizard .wizard-foot button.primary').isDisabled(),true,'пустой экран не пускает вперёд');
  assert.match(await page.locator('#wizard .wizard-need').innerText(),/Выберите вид/,'рядом с кнопкой сказано, чего не хватает');
  await chooseSpecies('Человек');
  await page.locator('#wizard .wizard-foot button:text-is("Назад")').click();await waitIdle();
  assert.equal(await page.evaluate(()=>window.soloWizard.state().screen),0,'«Назад» возвращает на расу');
  await page.locator('#wizard .wizard-foot button.primary').click();await waitIdle();
  assert.equal(await page.evaluate(()=>window.soloWizard.state().screen),1,'«Дальше» ведёт на класс');
  await chooseClass('Волшебник');
  // Класс больше не уводит сам: на его экране остаются навыки класса.
  await navTo('background');
  await chooseBackground('Мудрец');
  await fillScores();
  await fillChoices();
  await fillMagic();
  let value=await contract();
  for(let guard=0;guard<6&&!value.ready;guard++){await navTo('species');await fillChoices();await fillMagic();value=await contract();}
  const steps=(value.steps||[]).map(row=>row.id);assert(steps.includes('scores')&&steps.includes('magic'),'caster has scores and magic');
  assert.equal(value.sheet.spell_slots['1'],2,'native wizard level-1 slots are shown on the sheet');
  await page.evaluate(()=>window.soloWizard.goto('final'));await waitIdle();
  await fillFinal('Ирма Пеплосвет','Ирма ищет пропавшую сестру и не доверяет страже.');
  value=await contract();assert.equal(value.ready,true,JSON.stringify(value.issues));
  assert.equal(await page.locator('#wizard .wizard-confirm, #wizard button.primary').last().isEnabled(),true);
  // r248: ответ шага приходил на черновик ДО набора и затирал имя и числа, которые
  // игрок успел ввести, пока запрос летел. Ответ задерживается намеренно.
  await page.evaluate(()=>{window.__originalFetch=window.fetch;window.fetch=(input,init)=>String((input&&input.url)||input).includes('/wizard/contract')?new Promise(resolve=>setTimeout(()=>resolve(window.__originalFetch(input,init)),700)):window.__originalFetch(input,init);});
  await page.locator('#wizard .wizard-field input[type=text]').fill('');
  const inflight=page.locator('#wizard .wizard-field textarea').dispatchEvent('change');
  await page.locator('#wizard .wizard-field input[type=text]').fill('Ирма Пеплосвет');
  await inflight;await waitIdle();await waitIdle();
  assert.equal(await page.evaluate(()=>window.soloWizard.state().draft.name),'Ирма Пеплосвет','имя, набранное во время запроса, сохраняется');
  await page.evaluate(()=>{window.fetch=window.__originalFetch;});
  // r248: пустое имя не уходит в API — причина называется до запроса, а не «не удалось».
  await page.locator('#wizard .wizard-field input[type=text]').fill('');
  await page.locator('#wizard .wizard-field textarea').dispatchEvent('change');await waitIdle();await waitIdle();
  assert.equal(await page.locator('#wizard button.primary').last().isDisabled(),true,'без имени подтверждать нечего');
  let posted=false;const watch=request=>{if(request.url().endsWith('/prototype/campaigns')&&request.method()==='POST')posted=true;};
  page.on('request',watch);
  // Тот самый отказ владельца: клиент с устаревшим «можно подтверждать» отправляет
  // пустое имя. Кнопка включена руками — ровно это состояние было на телефоне.
  await page.evaluate(()=>{document.querySelector('#wizard button.primary').disabled=false;});
  await page.locator('#wizard button.primary').last().click();
  await page.locator('#wizard .wizard-error').waitFor({timeout:15000});
  await page.waitForTimeout(300);page.off('request',watch);
  const refusal=await page.locator('#wizard .wizard-error').innerText();
  assert.match(refusal,/[Ии]мя героя/,'отказ называет поле: '+refusal);
  assert.ok(!/Не удалось обработать запрос/.test(refusal),'общей фразы быть не должно: '+refusal);
  assert.equal(posted,false,'пустое имя не отправляется в API');
  // И ответ сервера на тот же отказ читается по-русски: 422 несёт message рядом с detail.
  const direct=await page.evaluate(async()=>{const response=await fetch('/api/prototype/campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'',request_id:'r248-e2e',adventure:'sandbox',build:{species_slug:'human',class_slug:'wizard',level:1,ability_scores:{},selected_choices:[]},prepared_spells:[],description:'',encounter_monsters:[],rules_confirmed:true,hero_confirmed:true,background_confirmed:true})});return {status:response.status,body:await response.json()};});
  assert.equal(direct.status,422,JSON.stringify(direct));
  assert.match(direct.body.message,/[Ии]мя героя/,'сервер называет поле: '+JSON.stringify(direct.body));
  await page.locator('#wizard .wizard-field input[type=text]').fill('Ирма Пеплосвет');
  await page.locator('#wizard .wizard-field input[type=text]').dispatchEvent('change');await waitIdle();await waitIdle();
  await page.screenshot({path:path.join(out,'wizard-create-1280.png'),fullPage:false});
  const created=page.waitForResponse(r=>r.url().endsWith('/prototype/campaigns')&&r.request().method()==='POST');
  await page.locator('#wizard button.primary').last().click();
  const response=await created;assert(response.ok(),await response.text());
  await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));
  const id=(await response.json()).id;
  const sheet=await (await context.request.get(base+'/api/campaigns/'+id+'/character')).json();
  assert.match(String(sheet.identity.species).toLowerCase(),/^(human|человек)$/,'лист называет вид — русским именем или ключом: '+String(sheet.identity.species));assert(Object.keys(sheet.identity.classes).some(name=>/^(wizard|волшебник)$/i.test(name)),'the sheet names the chosen class: '+JSON.stringify(sheet.identity.classes));
  assert.match(JSON.stringify(sheet),/Ирма Пеплосвет/,'hero name survives the launch');
  const play=await (await context.request.get(base+'/api/campaigns/'+id)).json();
  assert.equal(play.state.preparation&&play.state.preparation.background_confirmed,true,'backstory confirmation reaches the GM preparation gate: '+JSON.stringify(Object.keys(play.state)));
  const background=String(JSON.stringify(play.state)).includes('Ирма ищет пропавшую сестру');assert(background,'backstory text is kept in the campaign state');
  assert.equal(errors.length,0,errors.join('|'));
  await page.evaluate(value=>window.soloWizard.levelUp(value.id),{id});
  await page.locator('#wizard .wizard-shell').waitFor();await waitIdle();
  const levelError=await page.locator('#wizard .wizard-error').count();
  assert.equal(levelError,0,levelError?await page.locator('#wizard .wizard-error').innerText():'');
  console.log(JSON.stringify({created:id,checks:['wizard contract drives steps','scores filled from server budget','native slots on sheet','campaign created from wizard','background persisted']}));
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
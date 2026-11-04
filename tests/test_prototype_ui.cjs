const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[],checks=[],turns=[];page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const get=async path=>{const response=await context.request.get(base+'/api'+path);assert(response.ok(),await response.text());return response.json();};
 const idle=()=>page.waitForFunction(()=>window.SoloGameScreen&&!document.getElementById('send').disabled);
 const state=()=>page.evaluate(()=>({id:campaignId,version:current.version,state:current.state}));
 async function choice(title,id){const box=page.locator('#prototype-creation details').filter({has:page.locator('summary').filter({hasText:title})});await box.locator('summary').click();await box.locator('input[placeholder="Найти"]').fill(id);await box.locator('input[type=checkbox][value="'+id+'"]').check();}
 // The player path is the step wizard, so this seam drives the same component a
 // player does instead of the advanced catalogue form.
 const wizState=()=>page.evaluate(()=>window.soloWizard.state());
 const wizContract=async()=>(await wizState()).contract;
 async function wizIdle(){await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});}
 async function wizSteps(){const value=await wizContract();return value.steps;}
 async function wizNav(row){const list=(await wizSteps()).filter(item=>item.id!=='equipment');const index=list.findIndex(item=>item.id===row.id&&item.choice_key===row.choice_key);assert(index>=0,'step '+row.id+' not offered');await page.locator('#wizard .wizard-list button').nth(index).click();await wizIdle();}
 async function wizGo(stepId){const list=(await wizSteps()).filter(item=>item.id!=='equipment');const index=list.findIndex(item=>item.id===stepId);assert(index>=0,'step '+stepId+' not offered');await page.locator('#wizard .wizard-list button').nth(index).click();await wizIdle();}
 async function wizPick(label){const node=page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:label}).first();await node.click();await wizIdle();}
 async function wizScores(){await page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:'Покупка за очки'}).first().click();await wizIdle();const inputs=page.locator('#wizard .wizard-scores input');const values=[15,14,13,12,10,8];for(let index=0;index<6;index++){await inputs.nth(index).fill(String(values[index]));await inputs.nth(index).dispatchEvent('change');await wizIdle();}}
 async function wizChoices(){for(let guard=0;guard<18;guard++){const list=(await wizSteps()).filter(row=>row.id!=='equipment');const open=list.find(row=>row.choice_key&&row.id!=='magic'&&Number(row.missing||0)>0);if(!open)return;await wizNav(open);if(open.kind==='increase'){for(const [row,times] of [[0,2],[1,1]])for(let count=0;count<times;count++){await page.locator('#wizard .wizard-stage .wizard-scores .wizard-score').nth(row).locator('button').last().click();await wizIdle();}continue;}
  const chosen=new Set(open.chosen||[]);const all=page.locator('#wizard .wizard-stage .wizard-option[data-id]');const total=await all.count();let picked=false;for(let index=0;index<total;index++){const node=all.nth(index);if(chosen.has(await node.getAttribute('data-id')))continue;await node.click();await wizIdle();picked=true;break;}assert(picked,'could not answer '+open.choice_key);}}
 async function wizMagic(wanted){for(let guard=0;guard<22;guard++){const magic=(await wizSteps()).find(row=>row.id==='magic');if(!magic||magic.status==='complete')return;await wizNav(magic);const all=page.locator('#wizard .wizard-stage .wizard-block .wizard-option:not([disabled])');const total=await all.count();assert(total>0,'no spell offered');const picked=new Set(magic.chosen||[]);let acted=false;for(let index=0;index<total;index++){const node=all.nth(index);const id=await node.getAttribute('data-id');const text=await node.innerText();if(picked.has(id))continue;if(wanted&&!text.includes(wanted))continue;await node.click();await wizIdle();acted=true;break;}if(!acted){for(let index=0;index<total;index++){const node=all.nth(index);if(picked.has(await node.getAttribute('data-id')))continue;await node.click();await wizIdle();acted=true;break;}}assert(acted,'magic step could not be filled');}}
 let lastBuild=null,lastSpells=[];
 async function create(klass,name,{spell=false,prepared=false}={}){
  await page.evaluate(()=>window.soloWizard.open({levelUp:false}));
  await page.locator('#wizard .wizard-shell').waitFor();await wizIdle();
  await wizGo('species');await wizPick('Dwarf');
  await wizGo('class');await wizPick(klass==='wizard'?'Wizard':'Rogue');
  await wizGo('background');await wizPick('Soldier');
  await wizGo('scores');await wizScores();
  await wizChoices();
  if(klass==='wizard')await wizMagic(spell?'Magic Missile':null);
  await wizChoices();
  const list=(await wizSteps()).filter(row=>row.id!=='equipment');
  await page.locator('#wizard .wizard-list button').nth(list.findIndex(row=>row.id==='final')).click();await wizIdle();
  await page.locator('#wizard .wizard-field input[type=text]').fill(name);await page.locator('#wizard .wizard-field input[type=text]').dispatchEvent('change');await wizIdle();
  await page.locator('#wizard .wizard-field textarea').fill('Герой ищет работу и не доверяет страже.');await page.locator('#wizard .wizard-field textarea').dispatchEvent('change');await wizIdle();
  const ready=await wizContract();assert.equal(ready.ready,true,JSON.stringify(ready.issues));
  lastBuild=ready.build;lastSpells=(await wizState()).draft.prepared_spells||[];
  await page.screenshot({path:path.join(out,'wizard-'+klass+'-1280.png')});
  const created=page.waitForResponse(r=>r.url().endsWith('/prototype/campaigns')&&r.request().method()==='POST');
  await page.locator('#wizard button.primary').last().click();
  const response=await created;assert(response.ok(),await response.text());
  await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));await idle();
  const value=await state(),sheet=await get('/campaigns/'+value.id+'/character');
  // The sheet speaks the player's language: a class is named in Russian while
  // the wizard option is labelled with its English slug.
  const RU={'rogue':'плут','fighter':'воин','wizard':'волшебник','bard':'бард'};
  const wanted=[klass.toLowerCase(),(RU[klass]||'')].filter(Boolean);
  assert(Object.keys(sheet.identity.classes).some(title=>wanted.includes(title.toLowerCase())),JSON.stringify(sheet.identity));
  assert.equal(String(sheet.identity.species).toLowerCase(),'dwarf');return value.id;
 }
 async function send(text){await page.locator('#player-input').fill(text);const waiting=page.waitForResponse(r=>r.url().endsWith('/command-turn')&&r.request().method()==='POST');await page.locator('#send').click();const response=await waiting;const result=await response.json();assert(response.ok(),JSON.stringify(result));await idle();return result;}
 async function button(label){const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/commands')&&r.request().method()==='POST'),page.getByRole('button',{name:label,exact:true}).click()]);const result=await response.json();assert(response.ok(),JSON.stringify(result));await idle();return result;}
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);await page.evaluate(()=>document.body.classList.remove('effects'));
  const options=await get('/prototype/options');assert(options.classes.length>4&&options.species.length>2&&options.spells.length>20);checks.push('Real full catalogue, native schema and offline-mode notice');
  const rogue=await create('rogue','Prototype-4 Rogue');
  const observe=await send('Осматриваюсь');assert.equal(observe.status,'observation');assert.equal(await page.locator('#player-input').inputValue(),'');
  const check=await send('Проверяю dex');assert.equal(check.status,'resolved');checks.push('Ordinary new game: dwarf rogue, observe and native ability check');
  // A sandbox has no authored NPC, so the Master asks which Дорн is meant instead
  // of inventing one; the prepared-world conversation is covered by
  // tests/test_pdf_play_today_real.py.
  const talk=await send('Спрашиваю Дорна о мосте');assert.equal(talk.status,'clarification');checks.push('Sandbox: an unknown NPC name becomes a clarification, not an invented conversation');
  await page.evaluate(()=>document.querySelector('[data-view="character"]').click());await page.waitForFunction(()=>document.getElementById('character-content').textContent.includes('Rogue'));
  await page.getByRole('button',{name:'Инвентарь и торговля',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#inventory .inventory-grid')?.textContent.includes('Dagger'));assert.match(await page.locator('#inventory').innerText(),/Dagger/);
  await page.evaluate(()=>view('play'));
  await page.locator('.game-restore>summary').click();await page.getByRole('button',{name:'Сохранить контрольную точку',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Контрольная точка сохранена.'));
  const saved=await get('/campaigns/'+rogue+'/screen');await page.reload();await idle();assert.deepEqual((await get('/campaigns/'+rogue+'/screen')).snapshot,saved.snapshot);checks.push('Server-derived sheet/inventory and save → reload, no Developer Mode');
  await page.setViewportSize({width:960,height:640});await page.screenshot({path:path.join(out,'rogue-960.png')});
  await page.setViewportSize({width:1280,height:800});
  await create('wizard','Prototype-4 Wizard',{spell:true});
  // The wizard builds a hero, not a scenario. A first encounter is authored
  // separately, from the very build the wizard just produced, so the combat
  // below runs on the same native sheet the player confirmed.
  const fight=await page.evaluate(async payload=>{
   const created=await api('/prototype/campaigns',payload);campaignId=created.id;localStorage.setItem('solo-campaign',created.id);await refresh();view('play');return created.id;
  },{name:'Prototype-4 Wizard',request_id:'prototype-4-combat',adventure:'authored',description:'Пустой погреб.',
     build:lastBuild,prepared_spells:lastSpells,encounter_monsters:['rat'],encounter_attack_bonuses:{rat:0},
     encounter_attack_sources:{rat:'Authored smoke encounter: explicit +0 selected by test author.'}});
  const wizard=fight;await idle();
  await button('Начать столкновение: opening');let screen=await get('/campaigns/'+wizard+'/screen');if(screen.snapshot.combat.current_actor!=='hero')await button('Закончить ход');
  const actions=page.locator('.prototype-action-panel');await actions.locator('summary').first().click();await actions.getByLabel('Действие героя',{exact:true}).selectOption('hide');
  const rejected=page.waitForResponse(r=>r.url().endsWith('/commands')&&r.request().method()==='POST');await actions.getByRole('button',{name:'Выполнить выбранное действие'}).click();assert.equal((await rejected).status(),409);await idle();
  assert(await page.locator('#send').isEnabled());checks.push('A known unsupported operation gives a reason and leaves play available');
  await actions.getByLabel('Действие героя',{exact:true}).selectOption('cast_spell');await actions.getByLabel('Заклинание',{exact:true}).selectOption('magic-missile');await actions.getByLabel('Цели заклинания',{exact:true}).selectOption('encounter-0');for(let i=0;i<3;i++)await actions.getByRole('button',{name:'Добавить цель',exact:true}).click();
  const cast=page.waitForResponse(r=>r.url().endsWith('/commands')&&r.request().method()==='POST');await actions.getByRole('button',{name:'Выполнить выбранное действие'}).click();const castResponse=await cast,castBody=await castResponse.json();assert(castResponse.ok(),JSON.stringify(castBody));await idle();
  screen=await get('/campaigns/'+wizard+'/screen');checks.push('Wizard: chosen prepared spell and target execute through real native_action');
  assert.equal(screen.snapshot.hero.resources.find(row=>row.id==='spell_slot:1').current,1);assert.equal(screen.snapshot.combat.actors.find(row=>row.id==='encounter-0').hp,0);
  await page.screenshot({path:path.join(out,'wizard-cast-1280.png')});
  await page.locator('#scene-hero').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'wizard-state-1280.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({real_nat20:true,game_response_fixtures:false,checks,rogue,wizard,wizard_snapshot:screen.snapshot,page_errors:errors},null,2));console.log(JSON.stringify({checks,page_errors:errors}));
 }catch(error){await page.screenshot({path:path.join(out,'failure.png')});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

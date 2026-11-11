const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp,campaign]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:860}}),page=await context.newPage();page.setDefaultTimeout(45000);
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
  const scores=await step('scores');
  await page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:'Покупка за очки'}).first().click();await waitIdle();
  const inputs=page.locator('#wizard .wizard-scores input');const count=await inputs.count();assert.equal(count,6);
  const values=[15,14,13,12,10,8];assert.equal(values.reduce((sum,value)=>sum+[0,1,2,3,4,5,7,9][value-8],0),27);
  for(let index=0;index<count;index++){await inputs.nth(index).fill(String(values[index]));await inputs.nth(index).dispatchEvent('change');await waitIdle();}
  const after=await step('scores');assert.equal(after.status,'complete',JSON.stringify(after.reasons));
 }
 async function navTo(stepId,choiceKey){
  const list=((await contract()).steps||[]).filter(row=>row.id!=='equipment');
  const index=list.findIndex(row=>row.id===stepId&&(!choiceKey||row.choice_key===choiceKey));
  assert(index>=0,'step '+stepId+' '+choiceKey+' is not in the contract');
  await page.locator('#wizard .wizard-list button').nth(index).click();await waitIdle();
 }
 async function fillChoices(){for(let guard=0;guard<16;guard++){
  const list=((await contract()).steps||[]).filter(row=>row.id!=='equipment');
  const open=list.find(row=>row.choice_key&&row.id!=='magic'&&Number(row.missing||0)>0);
  if(!open)return;
  await navTo(open.id,open.choice_key);
  if(open.kind==='increase'){
   const plan=[[0,2],[1,1]];
   for(const [row,times] of plan){for(let count=0;count<times;count++){
    const cell=page.locator('#wizard .wizard-stage .wizard-scores .wizard-score').nth(row);
    await cell.locator('button').last().click();await waitIdle();}}
   const after=((await contract()).steps||[]).find(row=>row.choice_key===open.choice_key);
   assert.equal(after.status,'complete',JSON.stringify(after.reasons));
   continue;}
  const chosen=new Set(open.chosen||[]);
  const all=page.locator('#wizard .wizard-stage .wizard-option[data-id]');const total=await all.count();assert(total>0,'no free option for '+open.choice_key);
  let picked=false;
  for(let index=0;index<total;index++){const node=all.nth(index);if(chosen.has(await node.getAttribute('data-id')))continue;await node.click();await waitIdle();picked=true;break;}
  assert(picked,'could not answer '+open.choice_key);
 }}
 async function fillMagic(){for(let guard=0;guard<20;guard++){
  const magic=((await contract()).steps||[]).find(row=>row.id==='magic');if(!magic||magic.status==='complete')return;
  await navTo('magic');
  const all=page.locator('#wizard .wizard-stage .wizard-block .wizard-option:not([disabled])');const total=await all.count();assert(total>0,'no spell offered');
  const picked=new Set(magic.chosen||[]);let acted=false;
  for(let index=0;index<total;index++){const node=all.nth(index);if(picked.has(await node.getAttribute('data-id')))continue;await node.click();await waitIdle();acted=true;break;}
  assert(acted,'magic step could not be filled');}}
 async function fillFinal(name,story){await page.locator('#wizard .wizard-field input[type=text]').fill(name);await page.locator('#wizard .wizard-field input[type=text]').dispatchEvent('change');await waitIdle();await page.locator('#wizard .wizard-field textarea').fill(story);await page.locator('#wizard .wizard-field textarea').dispatchEvent('change');await waitIdle();}
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloWizard);
  await open();
  const first=await contract();assert.equal(first.version,1);assert.equal(first.ready,false);assert((first.steps||[]).length>=6);
  assert.equal(first.steps[0].id,'species');
  await chooseSpecies('Human');
  await chooseClass('Wizard');
  await chooseBackground('Sage');
  await fillScores();
  await fillChoices();
  await fillMagic();
  let value=await contract();
  for(let guard=0;guard<6&&!value.ready;guard++){await page.evaluate(()=>view('campaigns'));await page.locator('#wizard .wizard-list button').nth(0).click();await waitIdle();await fillChoices();await fillMagic();value=await contract();}
  const steps=(value.steps||[]).map(row=>row.id);assert(steps.includes('scores')&&steps.includes('magic'),'caster has scores and magic');
  assert.equal(value.sheet.spell_slots['1'],2,'native wizard level-1 slots are shown on the sheet');
  await page.locator('#wizard .wizard-list button').last().click();await waitIdle();
  await fillFinal('Ирма Пеплосвет','Ирма ищет пропавшую сестру и не доверяет страже.');
  value=await contract();assert.equal(value.ready,true,JSON.stringify(value.issues));
  assert.equal(await page.locator('#wizard .wizard-confirm, #wizard button.primary').last().isEnabled(),true);
  await page.screenshot({path:path.join(out,'wizard-create-1280.png'),fullPage:false});
  const created=page.waitForResponse(r=>r.url().endsWith('/prototype/campaigns')&&r.request().method()==='POST');
  await page.locator('#wizard button.primary').last().click();
  const response=await created;assert(response.ok(),await response.text());
  await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));
  const id=(await response.json()).id;
  const sheet=await (await context.request.get(base+'/api/campaigns/'+id+'/character')).json();
  assert.equal(String(sheet.identity.species).toLowerCase(),'human');assert(Object.keys(sheet.identity.classes).some(name=>/^(wizard|волшебник)$/i.test(name)),'the sheet names the chosen class: '+JSON.stringify(sheet.identity.classes));
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
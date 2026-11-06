const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp,campaign]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const idle=async()=>{await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});await page.waitForTimeout(150);};
 const state=async()=>page.evaluate(()=>window.soloWizard.state());
 const contract=async()=>(await state()).contract;
 const nav=async predicate=>{const list=((await contract()).steps||[]).filter(row=>row.id!=='equipment');const index=list.findIndex(predicate);assert(index>=0,'step not offered');await page.locator('#wizard .wizard-list button').nth(index).click();await idle();};
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloWizard);
  const before=await (await context.request.get(base+'/api/campaigns/'+campaign+'/character')).json();
  const beforeHp=before.sheet.hp_max,beforeLevel=before.sheet.level||before.identity.level;
  await page.evaluate(value=>window.soloWizard.levelUp(value),campaign);
  await page.locator('#wizard .wizard-shell').waitFor();await idle();
  const shown=await page.evaluate(()=>window.soloWizard.state());
  assert.equal(shown.error,'','the level-up mode loaded: '+shown.error);
  const preview=shown.levelUp;
  assert(preview.to_level>preview.from_level,'the earned level is offered');
  const header=await page.locator('#wizard .wizard-delta').innerText();
  assert.match(header,new RegExp(String(preview.from_level)+' → '+String(preview.to_level)));
  assert.match(header,/ХП \+/, 'the delta shows the new hit points');
  for(let guard=0;guard<14;guard++){
   const value=await contract();
   const list=(value.steps||[]).filter(row=>row.id!=='equipment');
   const open=list.find(row=>row.choice_key&&row.id!=='magic'&&Number(row.missing||0)>0);
   if(!open)break;
   await nav(row=>row.choice_key===open.choice_key);
   if(open.kind==='increase'){
    const plan=[[0,2],[1,1]];
    for(const [row,times] of plan){for(let count=0;count<times;count++){await page.locator('#wizard .wizard-stage .wizard-scores .wizard-score').nth(row).locator('button').last().click();await idle();}}
    continue;}
   const all=page.locator('#wizard .wizard-stage .wizard-option[data-id]');const total=await all.count();assert(total>0,'no options for '+open.choice_key);
   const chosen=new Set(open.chosen||[]);let picked=false;
   for(let index=0;index<total;index++){const node=all.nth(index);if(chosen.has(await node.getAttribute('data-id')))continue;await node.click();await idle();picked=true;break;}
   assert(picked,'could not answer '+open.choice_key);
  }
  for(let guard=0;guard<14;guard++){
   const magic=((await contract()).steps||[]).find(row=>row.id==='magic');
   if(!magic||magic.status==='complete')break;
   await nav(row=>row.id==='magic');
   const all=page.locator('#wizard .wizard-stage .wizard-block .wizard-option:not([disabled])');const total=await all.count();assert(total>0,'no spell offered');
   const picked=new Set(magic.chosen||[]);let acted=false;
   for(let index=0;index<total;index++){const node=all.nth(index);if(picked.has(await node.getAttribute('data-id')))continue;await node.click();await idle();acted=true;break;}
   assert(acted,'magic step could not be filled');
  }
  const final=await contract();
  console.log('READY',JSON.stringify({ready:final.ready,issues:final.issues,blocking:final.blocking,rows:(final.steps||[]).map(row=>[row.id,row.choice_key,row.status,row.missing])}));
  assert.equal(final.ready,true,JSON.stringify(final.issues));
  await page.screenshot({path:path.join(out,'wizard-levelup-1280.png')});
  const unchanged=await (await context.request.get(base+'/api/campaigns/'+campaign+'/character')).json();
  assert.equal(unchanged.sheet.hp_max,beforeHp,'the hero is untouched before confirmation');
  const applied=page.waitForResponse(r=>r.url().endsWith('/level-up')&&r.request().method()==='POST');
  await page.locator('#wizard button.primary').last().click();
  const response=await applied;assert(response.ok(),await response.text());
  const result=await response.json();
  const after=await (await context.request.get(base+'/api/campaigns/'+campaign+'/character')).json();
  assert(after.sheet.hp_max>beforeHp,'the confirmed level really raises the hero');
  assert.equal(after.sheet.hp_max,result.hp_max);
  assert.equal(errors.length,0,errors.join('|'));
  console.log(JSON.stringify({from:beforeLevel,to:result.to_level,hpBefore:beforeHp,hpAfter:after.sheet.hp_max,
   checks:['level-up reuses the wizard','было → станет shown','hero unchanged until confirm','confirm commits once']}));
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
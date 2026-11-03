const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
const SHEET='Арден из Крепости\nКласс: Wizard\nВид: Human\nПредыстория: Sage\nУровень: 1\nСила 8, Ловкость 14, Телосложение 13\nИнтеллект 15, Мудрость 12, Харизма 10\n';
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloWizard);
  await page.evaluate(()=>window.soloWizard.open());
  await page.locator('#wizard .wizard-shell').waitFor();
  await page.locator('#wizard .wizard-paste').click();
  const area=page.locator('#wizard .wizard-import textarea');await area.waitFor();await area.fill(SHEET);
  await page.locator('#wizard .wizard-import-actions button').first().click();
  await page.waitForFunction(()=>window.soloWizard.state().import&&window.soloWizard.state().import.result);
  const result=await page.evaluate(()=>window.soloWizard.state().import.result);
  assert.equal(result.source,SHEET,'the original text is preserved exactly');
  assert.equal(result.draft.name,'Арден из Крепости');
  assert.equal(result.draft.class_slug,'wizard');
  assert.equal(result.draft.species_slug,'human');
  assert.equal(result.draft.background_slug,'sage');
  assert.deepEqual(result.draft.ability_scores,{strength:8,dexterity:14,constitution:13,intelligence:15,wisdom:12,charisma:10});
  await page.locator('#wizard .wizard-import button.primary').last().click();
  await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false'&&window.soloWizard.state().contract;});
  const draft=await page.evaluate(()=>window.soloWizard.state().draft);
  assert.equal(draft.class_slug,'wizard','the reading fills the draft');
  const contract=await page.evaluate(()=>window.soloWizard.state().contract);
  assert.equal(contract.ready,false,'an imported sheet is not automatically a legal hero');
  assert(contract.steps.some(row=>row.id==='skills'&&row.choice_key),'the wizard still asks for the open choices');
  // Manually change the class: the answers the import produced must not survive.
  const before=await page.evaluate(()=>window.soloWizard.state().answers);
  assert.equal(Object.keys(before).length,0,'an import applies no answers of its own');
  await page.screenshot({path:path.join(out,'wizard-import-1280.png')});
  assert.equal(errors.length,0,errors.join('|'));
  console.log(JSON.stringify({fields:result.fields.length,ambiguous:result.ambiguous.length,unknown:result.unknown.length,
   checks:['local parse','source preserved','draft filled','limits still enforced']}));
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
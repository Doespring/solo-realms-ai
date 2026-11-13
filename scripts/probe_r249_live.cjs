// r249: живая проверка редактора героя — виды, подвиды, классы и предыстории
// по-русски. node scripts/probe_r249_live.cjs <base> <width> <height> <tag>
// Снимки — в reports/r249/evi (в git не кладутся).
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,width,height,tag]=process.argv.slice(2);
const out=path.resolve(__dirname,'../reports/r249/evi');fs.mkdirSync(out,{recursive:true});
const LATIN=/[A-Za-z]/;
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SOLO_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:Number(width),height:Number(height)},locale:'ru-RU'});
 const page=await context.newPage();page.setDefaultTimeout(60000);
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 page.on('console',m=>{if(m.type()==='error')errors.push('console: '+m.text());});
 const shots=[];
 const shot=async name=>{const file=path.join(out,tag+'-'+name+'.png');await page.screenshot({path:file});shots.push(file);};
 const state=()=>page.evaluate(()=>window.soloWizard.state());
 const contract=async()=>(await state()).contract;
 const step=async id=>((await contract()).steps||[]).find(row=>row.id===id);
 async function waitIdle(){await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});}
 // Клик по ключу движка, а не по подписи: подпись подвида бывает и в пояснении
 // вида («Эльф» пишет в detail «Эльф, дроу, …»), и фильтр по тексту бьёт не туда.
 async function pick(id){const button=page.locator('#wizard .wizard-stage .wizard-option[data-id="'+id+'"]').first();await button.click();await waitIdle();}
 async function navTo(stepId){const list=((await contract()).steps||[]).filter(row=>row.id!=='equipment');
  const index=list.findIndex(row=>row.id===stepId);assert(index>=0,'шага '+stepId+' нет в договоре');
  await page.locator('#wizard .wizard-list button').nth(index).click();await waitIdle();}
 function latinIn(rows){const bad=[];for(const row of rows)for(const option of row.options||[])if(LATIN.test(String(option.name)))bad.push(row.id+': '+option.name);return bad;}
 const report={};
 try{
  // 'load' на этом экране не наступает (висит один из ресурсов), ждём разбор документа.
  await page.goto(base,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>window.soloWizard);
  // Чистый лист: иначе прошлый черновик из localStorage подсвечивает чужой вид.
  await page.evaluate(()=>{localStorage.removeItem('solo-wizard-draft');localStorage.removeItem('solo-wizard-pending');});
  await page.evaluate(()=>window.soloWizard.open());await page.locator('#wizard .wizard-shell').waitFor();await waitIdle();
  report.species=(await step('species')).options.map(o=>o.name);
  await shot('01-species');
  await pick('Elf');
  const lineage=await step('lineage');assert(lineage,'шаг наследия не появился');
  report.lineage=lineage.options.map(o=>o.name);
  await navTo('lineage');
  await shot('02-lineage');
  await pick('elf-drow');
  await navTo('class');report.class=(await step('class')).options.map(o=>o.name);
  await shot('03-class');
  await pick('wizard');await navTo('background');
  report.background=(await step('background')).options.map(o=>o.name);
  await shot('04-background');
  await pick('sage');await navTo('class');
  const live=await state();
  report.draft={species_slug:live.draft.species_slug,class_slug:live.draft.class_slug,
                background_slug:live.draft.background_slug,sheet_ready:!!(await contract()).sheet_ready};
  report.sheetHead=await page.locator('#wizard .wizard-sheet .wizard-sheet-head p').first().textContent();
  const all=((await contract()).steps||[]).filter(row=>['species','lineage','class','subclass','background'].includes(row.id));
  report.latin=latinIn(all);
  assert.deepEqual(report.latin,[],'латиница в названиях шагов: '+JSON.stringify(report.latin));
  assert(!LATIN.test(String(report.sheetHead)),'латиница в шапке листа: '+report.sheetHead);
  assert.equal((await step('species')).status,'complete');
  report.errors=errors;report.shots=shots;report.ok=true;
  console.log(JSON.stringify(report,null,1));
 }catch(error){
  await shot('failure');report.errors=errors;report.shots=shots;report.ok=false;report.failure=String(error&&error.stack||error);
  console.log(JSON.stringify(report,null,1));process.exitCode=1;
 }finally{await browser.close();}
})();

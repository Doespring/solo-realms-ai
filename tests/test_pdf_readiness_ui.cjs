const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();
 const errors=[],prepares=[],writes=[],checks=[];let readinessCalls=0,reads=0,failReadiness=false,mock=false;
 const saved=Array.from({length:31},(_,i)=>({id:'validated-'+i,status:'validated',body:'Saved block '+i}));
 const draft={id:'qa-pdf',revision:7,description:'Сохранённое описание PDF',status:'needs_review',ruleset:'dnd2024',saved_blocks:structuredClone(saved),
  files:[{name:'synthetic.pdf',document:'own-source',bytes:2048,job:{status:'complete',done:37,total:37}}],
  preparation:{id:'partial-31',preparation_mode:'source_extract',source_ruleset:'dnd2014',status:'partial',done:31,total:37,message:'Сохранено 31 из 37 блоков.',errors:[{block:32,field:'source_spans',message:'Старая wrapper-ошибка'}]}};
 const originalDescription=draft.description,reason='Источник требует D&D2014, выбрана другая редакция';
 page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 await page.route('**/api/health',async route=>{const response=await route.fetch(),health=await response.json();health.llm={mock};return route.fulfill({response,json:health});});
 await page.route(base+'/api/creation/drafts/qa-pdf',route=>{if(route.request().method()!=='GET')writes.push(route.request().method());reads++;return route.fulfill({json:draft});});
 await page.route(base+'/api/creation/drafts/qa-pdf/readiness',route=>{readinessCalls++;return failReadiness?route.fulfill({status:503,json:{detail:'Локальная проверка временно недоступна'}}):route.fulfill({json:{id:'local-recheck',status:'blocked',done:31,total:37,message:reason}});});
 await page.route(base+'/api/creation/drafts/qa-pdf/prepare',route=>{prepares.push(route.request().postDataJSON());draft.preparation={...draft.preparation,status:'running',message:'Продолжение незавершённых блоков.'};return route.fulfill({json:draft.preparation});});
 await page.route(base+'/api/creation/drafts/qa-pdf/files/**',route=>{writes.push(route.request().method()+' file');return route.fulfill({json:draft});});
 const ready=page.getByRole('button',{name:'Проверить готовность (без API)',exact:true}),resume=page.getByRole('button',{name:'Продолжить незавершённые блоки',exact:true}),dialog=page.locator('#preparation-confirm'),message=page.locator('#creation-readiness');
 try{
  await page.goto(base);await page.evaluate(()=>document.body.classList.remove('effects'));
  await page.evaluate(()=>soloOpenCreation('qa-pdf'));await ready.waitFor();assert(await resume.isVisible());
  await ready.click();await page.waitForFunction(()=>document.getElementById('creation-readiness').textContent.includes('D&D2014'));
  const beforePoll=reads;await page.waitForFunction(()=>!document.getElementById('creation-readiness').textContent.includes('Проверяем готовность'));await page.waitForTimeout(1350);assert(reads>beforePoll);
  assert.equal(await message.innerText(),reason);assert.equal(prepares.length,0);assert(await dialog.isHidden());assert.equal(draft.ruleset,'dnd2024');
  await page.locator('#creation details').filter({hasText:'Сохранённая история ошибок подготовки'}).locator('summary').click();assert.match(await page.locator('#creation').innerText(),/Старая wrapper-ошибка/);assert.equal(await message.innerText(),reason);
  await page.screenshot({path:path.join(out,'blocked-edition.png')});checks.push('Readiness: zero prepare calls, explicit edition mismatch survives polling beside old errors');
  failReadiness=true;await ready.click();await page.waitForFunction(()=>document.getElementById('creation-readiness').textContent.includes('Локальная проверка временно недоступна'));
  await page.waitForTimeout(1350);assert.equal(prepares.length,0);assert(await dialog.isHidden());assert(await ready.isEnabled());assert(await resume.isVisible());
  await page.screenshot({path:path.join(out,'readiness-error.png')});checks.push('Readiness failure: zero prepare/fallback/consent dialog calls and retry remains available');
  await resume.click();await dialog.waitFor({state:'visible'});assert.equal(prepares.length,0);assert.equal(await dialog.locator('select').first().inputValue(),'dnd2014');
  await dialog.getByRole('button',{name:'Вернуться',exact:true}).click();assert.equal(prepares.length,0);
  await resume.click();await dialog.waitFor({state:'visible'});assert.equal(await dialog.locator('select').first().inputValue(),'dnd2014');
  await dialog.getByRole('button',{name:'Подготовить через API',exact:true}).evaluate(button=>{button.click();button.click();});await dialog.waitFor({state:'hidden'});
  assert.equal(prepares.length,1);assert.deepEqual(Object.keys(prepares[0]).sort(),['consent_to_send','request_id','ruleset','world_structure']);assert.equal(prepares[0].consent_to_send,true);assert.equal(prepares[0].ruleset,'dnd2014');assert(prepares[0].request_id);
  assert.deepEqual(draft.saved_blocks,saved);assert.equal(draft.preparation.done,31);assert.equal(draft.description,originalDescription);assert.deepEqual(writes,[]);
  checks.push('31/37 resume: only after explicit edition and consent; double click sends once; no reset/delete/PUT, saved blocks unchanged');
  draft.preparation.status='blocked';mock=true;failReadiness=false;await page.evaluate(()=>soloOpenCreation('qa-pdf'));
  assert(await ready.isEnabled());assert(await resume.isVisible());assert(await resume.isDisabled());await ready.click();await page.waitForFunction(()=>document.getElementById('creation-readiness').textContent.includes('D&D2014'));assert.equal(prepares.length,1);
  draft.preparation.done=37;await page.waitForTimeout(1350);assert(await resume.isHidden());assert(await ready.isEnabled());checks.push('Mock permits local readiness, blocks AI resume; complete-but-blocked run has no unfinished-block resume');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({mocked_endpoints:true,real_provider_calls:0,checks,readiness_calls:readinessCalls,prepare_fixture_calls:prepares,writes,saved_blocks_unchanged:true,page_errors:errors},null,2));console.log(JSON.stringify({checks,readiness_calls:readinessCalls,prepare_fixture_calls:prepares.length,page_errors:errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

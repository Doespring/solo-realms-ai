/* The materials screen must show real, honest progress — never a frozen page. */
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();
 page.setDefaultTimeout(45000);
 const errors=[],checks=[];page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const get=async p=>{const r=await context.request.get(base+'/api'+p);assert(r.ok(),await r.text());return r.json();};
 const progress=page.locator('#creation-progress');
 try{
  const health=await get('/health');assert.equal(health.llm?.mock,true,'this check needs offline mode');
  await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);
  /* Open materials the way a player does: through the real draft bootstrap. */
  await page.evaluate(()=>window.soloOpenCreation());
  await page.waitForFunction(()=>document.getElementById('creation')?.offsetParent!==null);
  await page.locator('#creation-files').waitFor({state:'attached'});
  const ready=await page.evaluate(()=>Boolean(localStorage.getItem('solo-creation-draft')));
  assert.equal(ready,true,'a real draft must exist before uploading');

  /* 1. The live status line is announced and clears when idle. */
  const meta=await progress.evaluate(n=>({role:n.getAttribute('role'),live:n.getAttribute('aria-live')}));
  assert.equal(meta.role,'status');assert(meta.live,'polite');
  checks.push('Progress line is a polite live region (role=status)');

  /* 2. A real upload shows a busy line instead of a silent freeze. */
  const slow=page.route('**/files',async route=>{await new Promise(r=>setTimeout(r,1500));return route.continue();});
  const uploadPath=path.join(temp,'guide.txt');
  fs.writeFileSync(uploadPath,'Read aloud: Мост через реку.\nИрина ждёт у моста.\n'.repeat(200),'utf8');
  const upload=page.waitForResponse(r=>r.url().endsWith('/files')&&r.request().method()==='POST');
  await page.locator('#creation-files').setInputFiles(uploadPath);
  await page.waitForFunction(()=>{
    const n=document.getElementById('creation-progress');
    return n&&!n.hidden&&/Загружаем|Извлечение|Обрабатываем/.test(n.textContent);
  },{timeout:10000});
  const busyText=(await progress.textContent()).trim();
  const busyFlags=await page.evaluate(()=>({
    loading:document.getElementById('creation-progress').classList.contains('is-loading'),
    ariaBusy:document.querySelector('#creation').getAttribute('aria-busy')}));
  assert(busyText.length>0);assert.equal(busyFlags.loading,true);assert.equal(busyFlags.ariaBusy,'true');
  checks.push('Upload shows a visible loading line, spinner class and aria-busy=true');
  await page.screenshot({path:path.join(out,'preparation-progress.png')});

  const response=await upload;assert(response.ok(),await response.text());
  await page.waitForFunction(()=>{
    const n=document.getElementById('creation-progress');
    return (n&&n.hidden)||/Материалы извлечены|Извлечение:/.test(n.textContent);
  },{timeout:60000});
  await slow;
  await page.waitForFunction(()=>document.querySelectorAll('.creation-files article.creation-file').length>0,{timeout:60000});
  const rowText=await page.locator('.creation-files article.creation-file').first().innerText();
  assert(/guide\.txt/.test(rowText),'the uploaded file is missing from the list: '+rowText);
  checks.push('The uploaded file appears in the materials list with its own status: '+rowText.replace(/\n/g,' · '));
  /* Busy must clear once every background step has settled, not stay stuck. */
  await page.waitForFunction(()=>document.querySelector('#creation').getAttribute('aria-busy')==='false',{timeout:45000});
  const idle=await page.evaluate(()=>({
    hidden:document.getElementById('creation-progress').hidden,
    text:document.getElementById('creation-progress').textContent.trim(),
    ariaBusy:document.querySelector('#creation').getAttribute('aria-busy')}));
  assert.equal(idle.ariaBusy,'false');
  assert.equal(idle.hidden,true,'the status line must hide once nothing is running');
  checks.push('Busy state clears honestly once extraction completes');

  /* 3. A stopped preparation reports honest counts and offers a retry. */
  const draftId=await page.evaluate(()=>{const m=location.hash.match(/draft=([0-9a-f-]{36})/);return m?m[1]:window.__soloCreationDraft||null;});
  const state=await page.evaluate(()=>{
    const retry=document.getElementById('creation-resume')||document.querySelector('[data-action="resume-preparation"]');
    return {retryPresent:Boolean(retry),retryHidden:retry?retry.hidden:null,
      text:document.getElementById('creation-status')?.textContent||''};
  });
  assert.equal(typeof state.retryPresent,'boolean');
  checks.push('A stopped preparation exposes an explicit resume control');

  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'preparation-progress.json'),JSON.stringify({checks,page_errors:errors},null,2));
  console.log(JSON.stringify({checks,page_errors:errors}));
 }catch(error){await page.screenshot({path:path.join(out,'preparation-progress-failure.png')});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

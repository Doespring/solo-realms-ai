// A02: экран старой кампании. Сервер и механика настоящие, подмен нет.
// Проверяет ровно приёмку Q03 п. 1-2 и 5: причина видна ДО ввода, по-русски,
// с тремя действиями; выгрузка и проверка работают при несовместимости; после
// обновления кампания играется; в интерфейсе нет английских исключений.
//   node tests/test_compat_ui.cjs <base> <campaign-broken> <campaign-legacy> <temp>
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,broken,legacy,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;
fs.mkdirSync(out,{recursive:true});
const ENGLISH=/\b(?:[A-Za-z]{3,}\s+){2,}[A-Za-z]{3,}\b/;
(async()=>{
 const browser=await chromium.launch(process.env.SOLO_UI_BROWSER?{executablePath:process.env.SOLO_UI_BROWSER,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']}:{channel:'msedge',headless:true});
 const open=async(cid,width,height)=>{
  const context=await browser.newContext({viewport:{width,height}});
  await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  await context.addInitScript(id=>{localStorage.clear();localStorage.setItem('solo-campaign',id);
   localStorage.setItem('solo-remember-campaign','on');},cid);
  const page=await context.newPage();page.setDefaultTimeout(90000);
  const errors=[];page.on('pageerror',error=>errors.push(String(error)));
  page.on('console',message=>{if(message.type()==='error')console.log('CONSOLE',message.text());});
  page.on('requestfailed',request=>console.log('FAILED',request.method(),request.url(),request.failure()?.errorText));
  page.on('response',async response=>{const url=new URL(response.url());
   if(url.pathname.startsWith('/api/')&&response.status()>=400){let body='';try{body=(await response.text()).slice(0,200);}catch(error){}
    console.log('API',response.status(),response.request().method(),url.pathname,body);}});
  await page.goto(base,{waitUntil:'commit'});
  await page.waitForFunction(()=>window.SoloGameScreen&&document.getElementById('game-compat'));
  return {context,page,errors};
 };
 const shot=(page,name)=>page.screenshot({path:path.join(out,name+'.png'),fullPage:false});
 const compatText=page=>page.locator('#game-compat').innerText();
 const waitFeedback=async(page,predicate,timeout=20000)=>{
  const started=Date.now();let last='';
  while(Date.now()-started<timeout){last=await page.locator('#game-feedback').innerText().catch(()=> '');
   if(predicate(last))return last;await page.waitForTimeout(250);}
  throw new Error('ответ не появился; на экране: '+JSON.stringify(last));
 };
 const clickCompat=async(page,action)=>{
  const button=page.locator('#game-compat button[data-action="'+action+'"]');
  assert.equal(await button.count(),1,'кнопка '+action+' должна быть одна');
  await button.scrollIntoViewIfNeeded();
  await button.click();
  console.log('CLICKED',action);
 };
 try{
  // ── 1. Несовместимость видна ДО ввода, по-русски, с тремя действиями ─────
  const {context,page,errors}=await open(broken,1280,860);
  await page.waitForFunction(()=>{const box=document.getElementById('game-compat');
   return box&&!box.hidden&&box.dataset.state==='needs-choice';});
  assert.equal(await page.locator('#send').isDisabled(),true,'ввод должен быть заперт до обновления');
  const text=await compatText(page);
  assert(/МАСТЕР · совместимость кампании/i.test(text),text);
  assert(text.includes('не играется, пока её не обновят')&&text.includes('История читается'),text);
  assert(text.includes('Регистрация сценария:'),text);
  const labels=await page.locator('#game-compat button').allInnerTexts();
  assert.deepEqual(labels,['Сделать копию и обновить','Проверить базу','Экспортировать кампанию'],labels);
  assert.equal(await page.locator('#game-compat button[data-action="migrate"]').isDisabled(),false,'обновление возможно — кнопка обязана работать');
  assert(!ENGLISH.test(text),'английское исключение на экране: '+text);
  // Причина стоит ОДИН раз — в блоке над вводом. Строка отклика не дублирует
  // её и не зовёт «Обновить сцену», которая на такой кампании ничего не меняет.
  assert.equal(await page.locator('#game-feedback').isHidden(),true,'причина не должна дублироваться в строке отклика');
  const boxes=await page.evaluate(()=>{const r=id=>{const n=document.getElementById(id);return n?n.getBoundingClientRect():null;};
   const compat=r('game-compat'),form=r('action-form');
   return {compat:compat&&{top:compat.top,bottom:compat.bottom,left:compat.left,right:compat.right},
    form:form&&{top:form.top,bottom:form.bottom,left:form.left,right:form.right},
    width:document.documentElement.scrollWidth,inner:window.innerWidth};});
  assert(boxes.compat.bottom<=boxes.form.top+1,'блок совместимости перекрывает поле ввода: '+JSON.stringify(boxes));
  assert.equal(boxes.width>boxes.inner+1,false,'горизонтальная прокрутка на 1280: '+JSON.stringify(boxes));
  await shot(page,'a02-broken-1280');
  console.log('STAGE needs-choice 1280',JSON.stringify({labels,sendDisabled:true}));

  // ── 2. «Проверить базу» отвечает по-русски и ничего не меняет ───────────
  await clickCompat(page,'verify');
  const verified=await waitFeedback(page,text=>text.includes('Целостность базы'));
  assert(verified.includes('записей истории'),verified);
  assert(!ENGLISH.test(verified),'английский ответ проверки: '+verified);
  await shot(page,'a02-verify-1280');
  console.log('STAGE verify',JSON.stringify(verified.slice(0,160)));

  // ── 3. Обновление на экране: копия, потом изменение, ввод открывается ────
  await clickCompat(page,'migrate');
  const started=Date.now();let state=null;
  while(Date.now()-started<90000){
   state=await page.evaluate(()=>({hidden:document.getElementById('game-compat').hidden,
    disabled:document.getElementById('send').disabled,
    feedback:document.getElementById('game-feedback').textContent}));
   if(state.hidden&&!state.disabled)break;
   await page.waitForTimeout(300);
  }
  assert(state.hidden&&!state.disabled,'после обновления ввод не открылся: '+JSON.stringify(state));
  const screen=await (await context.request.get(base+'/api/campaigns/'+broken+'/screen')).json();
  assert.equal(screen.snapshot.compatibility.status,'ok');
  assert.equal(screen.snapshot.terminal,null);
  assert(screen.commands.length>0,'после обновления у кампании должны быть действия');
  const after=await page.locator('#play').innerText();
  assert(!ENGLISH.test(after),'английское исключение после обновления: '+after);
  await shot(page,'a02-migrated-1280');
  console.log('STAGE migrated',JSON.stringify({commands:screen.commands.length,compat:screen.snapshot.compatibility.status}));
  assert.deepEqual(errors,[],'ошибки JS на экране: '+errors.join('; '));
  await context.close();

  // ── 4. Телефон 375: старая запись со событиями — играть можно ───────────
  const small=await open(legacy,375,812);
  await small.page.waitForFunction(()=>{const box=document.getElementById('game-compat');
   return box&&!box.hidden;});
  const history=await compatText(small.page);
  assert(/МАСТЕР · старая запись/i.test(history),history);
  assert(history.includes('исторические'),history);
  assert.equal(await small.page.locator('#send').isDisabled(),false,'со старыми событиями играть можно');
  assert(!ENGLISH.test(history),'английское исключение на телефоне: '+history);
  const overflow=await small.page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
  assert.equal(overflow,false,'горизонтальная прокрутка на 375');
  await small.page.screenshot({path:path.join(out,'a02-history-375.png')});
  console.log('STAGE history 375',JSON.stringify(history.slice(0,160)));
  assert.deepEqual(small.errors,[],'ошибки JS на телефоне: '+small.errors.join('; '));
  await small.context.close();
  await browser.close();
 }catch(error){await browser.close();throw error;}
})().catch(error=>{console.error(error&&error.stack||error);process.exit(1);});

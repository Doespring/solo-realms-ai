// n01: отказ по версии — одно нажатие, один запрос, обновлённая сцена и одна
// русская строка. Экран настоящий (web/*.js), ответы — явные фикстуры: 409 на
// command-turn и сдвинутая версия в пакете сцены. Модель и движок не нужны.
//
//   node tests/test_conflict_recovery_ui.cjs <base> <campaign id> <out dir>
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,cid,temp]=process.argv.slice(2),out=path.resolve(process.env.SOLO_UI_EVIDENCE||temp||'reports/n01/evi');
fs.mkdirSync(out,{recursive:true});
const SHOT=path.join(out,'n01-conflict-375.png');
const WIDTH=375,HEIGHT=812;

(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SOLO_UI_BROWSER,headless:true,
  args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const context=await browser.newContext({viewport:{width:WIDTH,height:HEIGHT},locale:'ru-RU'});
 const page=await context.newPage();page.setDefaultTimeout(30000);
 const pageErrors=[];
 page.on('pageerror',e=>pageErrors.push(String(e)));
 try{
  const real=await (await context.request.get(base+'/api/campaigns/'+cid)).json();
  const screen=await (await context.request.get(base+'/api/campaigns/'+cid+'/screen')).json();
  const campaign=structuredClone(real);campaign.backend='nat20';campaign.conversation=[];campaign.gm_history=[];campaign.history=[];campaign.saves=[];
  const packet=structuredClone(screen);
  packet.snapshot.backend='nat20';packet.snapshot.command_turn=true;packet.snapshot.version=14;
  packet.snapshot.combat=null;packet.snapshot.terminal=null;
  packet.snapshot.scene={title:'Домик смотрительницы',description:'У старого фонаря стоит домик.'};
  packet.snapshot.hero.resources=[];packet.events=[];packet.commands=[];
  let turnCount=0,refreshes=0,screenLoads=0,sent=[];
  const turns=[];
  await page.route(base+'/api/campaigns/'+cid,route=>{refreshes++;return route.fulfill({json:campaign});});
  await page.route(base+'/api/campaigns/'+cid+'/screen',async route=>{
   screenLoads++;
   // Первая выдача задержана: у игрока есть окно, в котором сцена ещё не пришла.
   if(screenLoads===1)await new Promise(r=>setTimeout(r,400));
   return route.fulfill({json:packet});
  });
  await page.route(base+'/api/campaigns/'+cid+'/command-turn',async route=>{
   turnCount++;sent.push(route.request().postDataJSON());turns.push(Date.now());
   await new Promise(r=>setTimeout(r,250));
   // Пока клиент ждал ответа, другой таб ушёл вперёд: сцена в базе уже версии 15.
   packet.snapshot.version=15;campaign.version=15;
   // Первый ход — отказ по версии; повторы по нажатию после обновления сцены
   // проходят, иначе окно 400 мс не отличить от «кнопка сломана навсегда».
   if(turnCount===1)return route.fulfill({status:409,json:{detail:{code:'version_conflict',
    message:'Сцена изменилась. Обновите её и выберите действие снова.'}}});
   return route.fulfill({json:{status:'resolved',narrative:'Готово.'}});
  });
  // Игра открывается не сама: стартовая страница ждёт нажатия. Кампанию берём
  // из localStorage (как после «Продолжить») и открываем экран игры кнопкой.
  await context.addInitScript(id=>{localStorage.setItem('solo-campaign',id);
   localStorage.setItem('solo-remember-campaign','on');
   // Стартовая страница берётся из этой настройки: иначе оболочка показывает
   // «Приветствие», и экран игры не открывается без живого профиля.
   localStorage.setItem('solo-start-page','campaign');},cid);
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>typeof window.SoloGameScreen==='object');
  if(!await page.evaluate(()=>!!document.querySelector('#play.active'))){
   const start=page.locator('#welcome .start__btn--primary');
   if(await start.count()){await start.first().click({timeout:5000}).catch(()=>{});await page.waitForTimeout(400);}}
  if(!await page.evaluate(()=>!!document.querySelector('#play.active'))){
   const nav=page.locator('.nav[data-view="play"], [data-view="play"]').first();
   if(await nav.count())await nav.click({timeout:8000}).catch(()=>{});}
  await page.waitForSelector('#play.active',{timeout:25000});
  await page.locator('#player-input').fill('Осматриваю калитку');

  // 1. Три нажатия в один тик, пока сцена ещё грузится: окно до блокировки.
  const submits=await page.evaluate(()=>{
   const form=document.getElementById('action-form');
   // dispatchEvent возвращает false, когда обработчик отменил отправку, поэтому
   // считаем сами события, а не результат вызова.
   let seen=0;form.addEventListener('submit',()=>{seen+=1;},true);
   for(let i=0;i<3;i++)form.dispatchEvent(new Event('submit',{cancelable:true,bubbles:true}));
   return seen;
  });
  assert.equal(submits,3,'форма не приняла три submit');
  await page.waitForFunction(()=>{const f=document.getElementById('game-feedback');
   return f&&!f.hidden&&/Сцена обновилась/.test(f.textContent);},{timeout:15000});
  await page.waitForTimeout(1200);
  assert.equal(turnCount,1,'на три нажатия ушло запросов: '+turnCount);
  assert.equal(new Set(sent.map(body=>body&&body.request_id)).size,1,'у запросов разный request_id');
  assert.equal(sent[0].expected_version,14,'первый запрос ушёл с чужой версией: '+JSON.stringify(sent[0]));
  assert.ok(refreshes>0,'сцену не перезапрашивали после отказа');
  assert.ok(screenLoads>1,'пакет сцены не перечитан после отказа');

  const line=(await page.locator('#game-feedback').textContent()||'').trim();
  assert.ok(/Сцена обновилась\. Вот что можно сделать/.test(line),'на экране не та строка: '+line);
  assert.ok(!/version_conflict|конфликт версий/i.test(line),'в строке остался код отказа: '+line);
  assert.ok(!/Не удалось обработать запрос/.test(line),'общая ошибка вместо причины: '+line);
  await page.screenshot({path:SHOT});

  // 2. Повтора того же тела нет: за 1,2 с после отказа ушёл ровно один запрос.
  assert.equal(turnCount,1,'клиент повторил тело запроса сам');

  // 3. После окна повтор разрешён — и уходит уже со свежей версией сцены.
  await page.waitForTimeout(500);
  await page.locator('#send').click();
  await page.waitForFunction(()=>{const f=document.getElementById('game-feedback');
   return f&&f.dataset.state==='resolved';},{timeout:15000});
  assert.equal(turnCount,2,'повтор по нажатию после обновления сцены не ушёл');
  assert.equal(sent[1].expected_version,15,'повтор ушёл со старой версией: '+JSON.stringify(sent[1]));
  assert.notEqual(sent[1].request_id,sent[0].request_id,'повтор переиспользовал request_id отказа');

  // 4. Окно 400 мс после ответа: клик внутри него не рождает запрос. Ждём ответ
  //    опросом в самой странице, иначе ожидание в тесте съедает всё окно.
  const windowState=await page.evaluate(async()=>{
   const input=document.getElementById('player-input'),send=document.getElementById('send');
   const state=()=>document.getElementById('game-feedback').dataset.state;
   // Предыдущий ответ держит кнопку 400 мс: своё окно начинаем после него.
   await new Promise(resolve=>setTimeout(resolve,500));
   input.value='Осматриваю калитку';input.dispatchEvent(new Event('input',{bubbles:true}));
   send.click();const started=Date.now();
   await new Promise(resolve=>{const tick=()=>{if(state()==='resolved')return resolve();
    if(Date.now()-started>15000)return resolve();setTimeout(tick,20);};tick();});
   input.value='Осматриваю калитку';input.dispatchEvent(new Event('input',{bubbles:true}));
   const disabledInWindow=send.disabled;
   send.click();
   await new Promise(resolve=>setTimeout(resolve,700));
   return {disabledInWindow,disabledAfterWindow:send.disabled};
  });
  assert.equal(turnCount,3,'нажатие в 400-мс окне не должно слать запрос: запросов '+turnCount);
  assert.equal(windowState.disabledInWindow,true,'кнопка не погашена в 400-мс окне');
  assert.equal(windowState.disabledAfterWindow,false,'кнопка осталась погашенной после окна');

  // 5. После окна то же нажатие уходит — блокировка именно на время ответа.
  await page.locator('#send').click();
  await page.waitForTimeout(900);
  assert.equal(turnCount,4,'нажатие после окна не ушло: запросов '+turnCount);

  await page.screenshot({path:SHOT});
  assert.deepEqual(pageErrors,[],'ошибки страницы: '+pageErrors.join(' | '));
  console.log(JSON.stringify({status:'ok',width:WIDTH,height:HEIGHT,turnCount,refreshes,screenLoads,
   line,first:sent[0],retry:sent[1],screenshot:SHOT},null,1));
 }finally{await browser.close();}
})().catch(error=>{console.error('FAIL',error&&error.stack||error);process.exit(1);});

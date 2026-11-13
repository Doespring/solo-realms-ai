// L02: сверка уровня героя с книгой на старте кампании.
// Экран настоящий, сервер живой, кампания настоящая (книга 8-го уровня,
// герой 1-го). Подменены только ответы, которых в песочнице нет: контекст
// книги для редактора и сам ответ на слово «пересобрать» (маршрут пересборки
// проверяется отдельным HTTP-тестом).
//   node tests/test_level_gate_ui.cjs <base> <temp> <campaign id>
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp,cid]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
const BOOK={scope:'world',ready:true,world_ready:true,run_id:'run-1',revision:1,
 minimum_level:8,maximum_level:8,hero_level:8,book:'Тиамат',reasons:[],blocked_operations:[]};
(async()=>{
 const launch=async()=>{const binary=process.env.SOLO_UI_BROWSER||undefined;try{return await chromium.launch({channel:binary?undefined:(process.env.SOLO_UI_CHANNEL||'msedge'),executablePath:binary,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});}catch(error){if(process.env.SOLO_UI_CHANNEL&&!binary)throw error;return await chromium.launch({executablePath:binary,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});}};
 const browser=await launch(),context=await browser.newContext({viewport:{width:1280,height:860}}),page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 page.on('response',async response=>{const path=new URL(response.url()).pathname;if(path.endsWith('/command-turn')||path.endsWith('/screen')){let body='';try{body=await response.text();}catch(error){}console.log('DIAG',response.status(),path,body.slice(0,400));}});
 await context.addInitScript(id=>{localStorage.clear();localStorage.setItem('solo-campaign',id);},cid);
 let rebuildAsked=0;
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/api/creation/drafts/stub-book/world-readiness')
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(BOOK)});
  if(url.pathname.endsWith('/command-turn')){
   const body=route.request().postDataJSON()||{};
   // Что именно набрал игрок — видно в логе прогона: «играем так» и
   // «пересобрать героя» разбирает сервер, а не экран.
   console.log('REQ',JSON.stringify(body));
   if(String(body.text||'').includes('пересобра')){
    rebuildAsked++;
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
     status:'gate_rebuild',command:null,state_version:1,events:[],snapshot:null,
     rebuild:{draft_id:'stub-book',revision:1,run_id:'run-1',level:8,book:'Тиамат'},
     narrative:'Открываю редактор героя на 8-м уровне.'})});
   }
  }
  return url.origin===base?route.continue():route.abort();
 });
 const gateText=()=>page.locator('#game-gate').innerText();
 // Готовность экрана к следующей реплике: кнопка отправки снова доступна и
 // короткая пауза после ответа (400 мс «одно нажатие — один запрос») прошла.
 const settle=async()=>{await page.waitForFunction(()=>{const button=document.getElementById('send');return button&&!button.disabled;});await page.waitForTimeout(450);};
 try{
  // `waitUntil:'load'` на этой странице не наступает — ждём DOM и готовность экрана.
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.SoloGameScreen&&document.getElementById('game-gate'));
  await page.waitForFunction(()=>{const node=document.getElementById('game-gate');return node&&!node.hidden;});

  // 1. До первого броска Мастер называет оба числа и оба пути.
  const ask=await gateText();
  assert.match(ask,/МАСТЕР/,'строка подписана Мастером: '+ask);
  assert.match(ask,/рассчитана на 8-й уровень/,'назван уровень книги: '+ask);
  assert.match(ask,/твой герой 1-го/,'назван уровень героя: '+ask);
  assert.match(ask,/Пересобери героя/,'предложена пересборка: '+ask);
  assert.match(ask,/играем как есть/,'назван второй путь: '+ask);
  assert.match(ask,/«пересобрать героя»/,'слово для пересборки написано на экране: '+ask);
  assert.match(ask,/«играем так»/,'слово для отказа написано на экране: '+ask);
  await page.screenshot({path:path.join(out,'gate-ask-1280.png')});

  // Телефон: тот же текст не вылезает за 375.
  await page.setViewportSize({width:375,height:812});
  await page.waitForFunction(()=>{const box=document.getElementById('game-gate');return box&&!box.hidden;});
  const box=await page.locator('#game-gate').evaluate(node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right};});
  assert(box.left>=-0.5&&box.right<=375.5,'строка Мастера вылезает за 375: '+JSON.stringify(box));
  await page.screenshot({path:path.join(out,'gate-ask-375.png')});
  await page.setViewportSize({width:1280,height:860});

  // 2. Любое другое слово не начинает историю: Мастер повторяет вопрос числами.
  const before=await (await context.request.get(base+'/api/campaigns/'+cid)).json();
  await settle();
  await page.fill('#player-input','осматриваюсь');
  await page.evaluate(()=>sendAction());
  // Ответ Мастера пришёл: экран получил отказ с тем же вопросом, а не молчание.
  await page.waitForFunction(()=>{const node=document.getElementById('game-feedback');return node&&node.dataset.state==='unsupported';});
  await settle();
  const still=await gateText();
  assert.match(still,/8-й уровень/,'вопрос не сменился на сцену: '+still);
  const unchanged=await (await context.request.get(base+'/api/campaigns/'+cid)).json();
  assert.equal(unchanged.version,before.version,'реплика до решения не сдвигает кампанию');

  // 3. «Играем так»: решение в кампании, способ назван словами и числами.
  // Реплику игрок пишет, когда Мастер уже ответил: кнопка отправки снова
  // доступна. Тест ждёт то же состояние, что и человек на экране, а не
  // определённое число миллисекунд.
  await settle();
  await page.fill('#player-input','играем так');
  await page.evaluate(()=>sendAction());
  await page.waitForFunction(()=>{const node=document.getElementById('game-gate');return node&&/Решено:/.test(node.innerText);});
  const decided=await gateText();
  assert.match(decided,/опасность боёв ведётся от уровня героя/,'способ назван в строке сверки: '+decided);
  assert.match(decided,/Книга просит 8-й уровень, герой 1-й/,'обе числа остались на экране: '+decided);
  // Полный ответ Мастера называет состав боя и способ — и стоит в ленте один
  // раз. Второй пересказ той же фразы в строке сверки был бы шумом.
  const feed=await page.locator('#play').innerText();
  assert.match(feed,/опасность считается от твоего 1-го уровня/,'Мастер назвал способ: '+feed.slice(0,400));
  assert.match(feed,/1 × Kobold Warrior/,'состав боя назван числом: '+feed.slice(0,400));
  assert.match(feed,/а не отряд на четырёх героев 8-го уровня/,'книжный отряд назван: '+feed.slice(0,400));
  assert.equal(feed.split('а не отряд на четырёх героев 8-го уровня').length-1,1,'решение названо один раз');
  await page.screenshot({path:path.join(out,'gate-decided-1280.png')});
  // `GET /api/campaigns/{id}` отдаёт проекцию (`balance_override` в ней есть, а
  // `state.nat20` — нет): состав боя считает обёртка теста прямо из базы
  // (tests/test_level_gate_ui.py), а экран обязан назвать его словами.
  const state=(await (await context.request.get(base+'/api/campaigns/'+cid)).json()).state;
  assert.equal(state.balance_override,'player_level','решение игрока в кампании');

  // 4. Карточка кампании показывает, каким способом ведётся игра.
  await page.evaluate(()=>view('campaigns'));
  await page.waitForFunction(()=>[...document.querySelectorAll('.chronicle-card')].some(card=>/Опасность ведётся под уровень героя/.test(card.innerText)));
  const card=await page.locator('.chronicle-card').filter({hasText:'Опасность ведётся под уровень героя'}).first().innerText();
  assert.match(card,/рассчитана на 8-й/,'карточка называет книжный уровень: '+card);
  await page.screenshot({path:path.join(out,'gate-card-1280.png')});

  // 5. «Пересобрать героя» открывает редактор на книжном 8-м уровне.
  await page.evaluate(()=>view('play'));
  await page.waitForFunction(()=>window.SoloGameScreen&&document.getElementById('game-gate'));
  await settle();
  await page.fill('#player-input','пересобрать героя');
  await page.evaluate(()=>sendAction());
  await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&!root.hidden;});
  await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false'&&window.soloWizard.state().contract;});
  const select=page.locator('#wizard .wizard-level select');
  await select.waitFor();
  assert.equal(await select.inputValue(),'8','редактор открылся на уровне книги');
  const wizard=await page.evaluate(()=>window.soloWizard.state());
  assert.equal(wizard.draft.level,8,'уровень книги стоит в черновике');
  assert.equal(rebuildAsked,1,'редактор открыт по слову игрока, а не сам');
  // Подпись книги живёт на первом экране редактора (часть «Уровень героя»),
  // поэтому читается до перехода на «Итог и имя».
  const note=await page.locator('#wizard .wizard-level .hint').innerText();
  assert.match(note,/Тиамат/,'редактор называет книгу: '+note);
  const final=await page.evaluate(()=>window.soloWizard.goto('final'));
  assert(final,'экран «Итог и имя» существует');
  await page.waitForFunction(()=>{const button=document.querySelector('#wizard .wizard-foot .primary');return button&&button.textContent==='Пересобрать героя';});
  await page.screenshot({path:path.join(out,'gate-rebuild-1280.png')});
  await page.evaluate(()=>window.soloWizard.close());

  assert.deepEqual(errors,[],'ошибки страницы');
  console.log('L02 gate: '+ask.slice(0,160));
  console.log('L02 decided: '+decided.slice(0,200));
  console.log('L02 cards and rebuild level: 8, rebuilt button «Пересобрать героя»');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

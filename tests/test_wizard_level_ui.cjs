const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
const BOOK={scope:'world',ready:true,world_ready:true,run_id:'run-1',revision:1,
 minimum_level:8,maximum_level:8,hero_level:8,book:'Тиамат',reasons:[],blocked_operations:[]};
// Книга с диапазоном: список уровней — от минимума до максимума, а не одно число.
const RANGE={...BOOK,run_id:'run-2',minimum_level:5,maximum_level:8,hero_level:5};
(async()=>{
 const launch=async()=>{const binary=process.env.SOLO_UI_BROWSER||undefined;try{return await chromium.launch({channel:binary?undefined:(process.env.SOLO_UI_CHANNEL||'msedge'),executablePath:binary,headless:true});}catch(error){if(process.env.SOLO_UI_CHANNEL&&!binary)throw error;return await chromium.launch({executablePath:binary,headless:true});}};
 const browser=await launch(),context=await browser.newContext({viewport:{width:1280,height:860}}),page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 // Книжный ответ подменяется только для проверки подписи «из какой книги
 // уровень»: сам мир в этом тесте не готовится. Всё остальное — живой сервер.
 await context.route('**/*',route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/api/creation/drafts/stub-book/world-readiness')
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(BOOK)});
  if(url.pathname==='/api/creation/drafts/stub-range/world-readiness')
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(RANGE)});
  return url.origin===base?route.continue():route.abort();});
 const state=()=>page.evaluate(()=>window.soloWizard.state());
 async function waitIdle(){await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});}
 try{
  // `waitUntil:'load'` на этой странице не наступает (висит один из ресурсов),
  // поэтому ждём DOM, а готовность редактора проверяем по `window.soloWizard`.
  await page.goto(base,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>window.soloWizard);
  const select=page.locator('#wizard .wizard-level select');
  // 1. Требование книги: селект открывается на 8 и называет книгу словами.
  await page.evaluate(()=>{localStorage.clear();return window.soloWizard.open({preparationDraft:'stub-book'});});
  await select.waitFor();await waitIdle();
  assert.equal(await select.inputValue(),'8','книга просит 8 — редактор открывается на 8');
  const note=await page.locator('#wizard .wizard-level .hint').innerText();
  assert.match(note,/Тиамат/,'подпись называет книгу: '+note);
  assert.match(note,/8-й уровень/,'подпись говорит словом, а не числом в селекте: '+note);
  assert.equal((await state()).draft.level,8,'уровень уходит в черновик, а не только в подпись');
  // Черновик — пока форма, а не герой: класс не выбран, и сборки (build) ещё нет.
  // Сервер обязан видеть в присланном черновике именно 8-й уровень.
  assert.equal((await state()).contract.draft.level,8,'сервер видит уровень 8 в черновике');
  await page.screenshot({path:path.join(out,'level-book-1280.png')});
  await page.evaluate(()=>window.soloWizard.close());

  // 2. Полная сборка на 8-м: подкласс, повышения характеристик, заклинания.
  const clickOption=async name=>{await page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:name}).first().click();await waitIdle();};
  await page.evaluate(()=>{localStorage.clear();return window.soloWizard.open({fresh:true});});
  await select.waitFor();await waitIdle();
  await select.selectOption('8');await waitIdle();
  await clickOption('Человек');
  await page.evaluate(()=>window.soloWizard.goto('class'));await waitIdle();
  await page.locator('#wizard .wizard-part .wizard-option[data-id="fighter"]').first().click();await waitIdle();
  let value=await state();
  assert.equal(value.draft.level,8,'выбранный уровень живёт в черновике');
  assert.equal(value.contract.build.level,8,'сборка, которую получит сервер, тоже 8-го уровня');

  const openSteps=value.contract.steps||[];
  assert(openSteps.some(step=>step.id==='subclass'),'подкласс предлагается с 3-го уровня');
  const asi=openSteps.filter(step=>step.kind==='asi');
  assert(asi.length>=2,'повышения на 4-м и 8-м: '+JSON.stringify(asi.map(step=>step.choice_key)));
  await page.locator('#wizard .wizard-recommend').click();
  // Предложение на 8-м считает движок: ждём, пока запрос отработает, и только
  // потом судим результат — иначе таймаут врёт о причине.
  await page.waitForFunction(()=>document.getElementById('wizard').getAttribute('aria-busy')==='false',null,{timeout:180000});
  value=await state();
  assert(value.contract.ready,'предложение закрывает все шаги: '+JSON.stringify({issues:value.contract.issues,error:value.error}));

  assert.equal(value.draft.level,8,'предложение не меняет выбранный уровень');
  assert(value.draft.subclass_slug,'подкласс выбран, а не оставлен шагом: '+JSON.stringify(value.draft.subclass_slug));
  assert(value.contract.steps.filter(step=>step.kind==='asi').every(step=>step.status==='complete'),'все повышения закрыты');
  assert.equal(value.contract.sheet.proficiency_bonus,3,'лист героя 8-го уровня: бонус мастерства +3');
  assert.match(await page.locator('#wizard .wizard-sheet .wizard-sheet-head').innerText(),/уровень 8/,'на листе «уровень 8»');
  assert(value.contract.sheet.hp_max>20,'хиты посчитаны на 8-м, а не на 1-м: '+value.contract.sheet.hp_max);
  await page.screenshot({path:path.join(out,'level-eight-1280.png')});

  // 3. После создания уровень 8 остаётся и в карточке, и на листе.
  await page.evaluate(()=>window.soloWizard.goto('final'));await waitIdle();
  const name=page.locator('#wizard .wizard-field input[type=text]');
  await name.fill('Талла Восьмая');await name.dispatchEvent('change');await waitIdle();await waitIdle();
  const created=page.waitForResponse(response=>response.url().endsWith('/prototype/campaigns')&&response.request().method()==='POST');
  await page.locator('#wizard button.primary').last().click();
  const response=await created;assert(response.ok(),await response.text());
  const id=(await response.json()).id;
  await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));
  const sheet=await (await context.request.get(base+'/api/campaigns/'+id+'/character')).json();
  assert.equal(sheet.identity.level,8,'лист кампании — 8-го уровня: '+JSON.stringify(sheet.identity));
  assert.equal(sheet.sheet.proficiency_bonus,3,'бонус мастерства на листе кампании');
  const rows=await (await context.request.get(base+'/api/chronicles')).json();
  const card=(rows.campaigns||[]).find(row=>row.id===id);
  assert(card,'кампания есть в списке');
  assert.equal(Number(card.level),8,'карточка кампании говорит «уровень 8»: '+JSON.stringify(card));

  // 4. Книга с диапазоном 5..8: в списке все уровни кампании, открыт нижний,
  // подпись называет диапазон словами и не грозит «выбран не тот уровень» на
  // законном 7-м (раньше предупреждение висело на любом уровне, кроме рекомендованного).
  const rangePage=await context.newPage();rangePage.setDefaultTimeout(45000);
  rangePage.on('pageerror',error=>errors.push(String(error)));
  try{
   await rangePage.goto(base,{waitUntil:'domcontentloaded'});
   await rangePage.waitForFunction(()=>window.soloWizard);
   await rangePage.evaluate(()=>{localStorage.clear();return window.soloWizard.open({preparationDraft:'stub-range'});});
   const rangeSelect=rangePage.locator('#wizard .wizard-level select');
   await rangeSelect.waitFor();
   await rangePage.waitForFunction(()=>document.getElementById('wizard').getAttribute('aria-busy')==='false');
   const offered=await rangeSelect.locator('option').evaluateAll(nodes=>nodes.map(node=>node.value));
   assert.deepEqual(offered,['5','6','7','8'],'список уровней кампании: '+offered.join(','));
   assert.equal(await rangeSelect.inputValue(),'5','книга с диапазоном открывается на нижнем уровне');
   const rangeNote=await rangePage.locator('#wizard .wizard-level .hint').innerText();
   assert.match(rangeNote,/с 5-го по 8-й/,'подпись называет диапазон словами: '+rangeNote);
   assert.equal(await rangePage.evaluate(()=>window.soloWizard.state().draft.level),5,'нижний уровень диапазона уходит в черновик');
   await rangeSelect.selectOption('7');
   await rangePage.waitForFunction(()=>document.getElementById('wizard').getAttribute('aria-busy')==='false');
   assert.equal(await rangePage.locator('#wizard .wizard-level .wizard-reason').count(),0,'7-й — законный уровень этой книги, предупреждения нет');
   await rangePage.screenshot({path:path.join(out,'level-range-1280.png')});
  } finally {await rangePage.close();}
  assert.equal(errors.length,0,errors.join('|'));
  console.log(JSON.stringify({created:id,checks:['book level on the first screen','a range book offers every level and names the range','level reaches the draft and the build','subclass and 4/8 increases at level 8','sheet bonus +3','campaign card and sheet say level 8']}));
 } finally { await browser.close(); }
})();

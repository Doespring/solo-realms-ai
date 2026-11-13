// A06/F10: разбор книги на экране мастера.
//  * у приложения книги нет кнопки старта, и оно названо справочником;
//  * у каждой находки видна цитата со страницы и список типа сущности;
//  * правка типа человеком сохраняется: место уходит из «Мест» в «Справочник».
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const binary=process.env.SOLO_UI_BROWSER||undefined;
 const browser=await chromium.launch({executablePath:binary,headless:true});
 let page;
 try{
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  page=await context.newPage();page.setDefaultTimeout(30000);
  const errors=[];page.on('pageerror',error=>errors.push(String(error)));
  await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>typeof window.soloOpenAdventureLibrary==='function');
  await page.evaluate(()=>window.soloOpenAdventureLibrary());
  await page.waitForFunction(()=>document.querySelector('#adventure-library .page-card button'));
  await page.locator('#adventure-library .page-card button').first().click();
  await page.waitForFunction(()=>document.querySelector('#adventure-library .readiness'));
  const review=page.locator('#adventure-library button',{hasText:'Подготовка Мастера'});
  await review.first().click();
  await page.waitForFunction(()=>document.querySelector('.cards-list .page-card'));

  // 1. Приложение — справочник без кнопки старта.
  const appendix=page.locator('.cards-list .page-card',{hasText:'приложение'});
  assert.equal(await appendix.count(),1,'на экране нет карточки приложения');
  const appendixText=await appendix.first().innerText();
  assert.match(appendixText,/Справочник:/,'приложение не названо справочником: '+appendixText);
  // У приложения нет стартовой сцены: строка «Стартовая сцена: “Приложение…”»
  // над «старт отсюда не начинается» противоречила сама себе.
  assert.doesNotMatch(appendixText,/Стартовая сцена/,'у приложения нарисована стартовая сцена: '+appendixText);
  const startLabels=['Создать героя','Продолжить с текущим героем','Подключить сценарий к кампании'];
  for(const label of startLabels){
   assert.equal(await appendix.first().locator('button',{hasText:label}).count(),0,
    'у приложения есть кнопка старта: '+label);
  }

  // 2. У находки есть цитата со страницы и выбор типа сущности.
  const placeRow=page.locator('.items .item',{hasText:'Соляные амбары'}).first();
  assert.equal(await placeRow.count(),1,'в «Местах» нет разобранного места');
  const disclosure=placeRow.locator('details',{hasText:'Цитата со страницы'});
  assert.equal(await disclosure.count(),1,'у места нет цитаты со страницы');
  // Цитата живёт в свёрнутом блоке: человек открывает её сам, поэтому проба
  // сначала нажимает заголовок, а не читает скрытый текст.
  await disclosure.first().locator('summary').click();
  const quote=await disclosure.first().innerText();
  assert.match(quote,/Соляные амбары стояли у воды/,'цитата не показывает напечатанный текст: '+quote);
  const select=placeRow.locator('select');
  assert.equal(await select.count(),1,'у сущности нет выбора типа');

  // Раздел ищем по ТОЧНОМУ заголовку: `hasText` в Playwright ищет подстроку без
  // учёта регистра, и слово «справочник» есть ещё в выборе типа у «Мест».
  const sectionText=title=>page.evaluate(wanted=>{
   const box=[...document.querySelectorAll('.bsec')].find(b=>b.querySelector('.bsec__title')?.textContent===wanted);
   return box?box.innerText:'';
  },title);
  // Строки раздела считаем по `.item`, а не по вхождениям имени: имя, пояснение
  // и цитата строки содержат его трижды.
  const sectionRows=(title,name)=>page.evaluate(([wanted,needle])=>{
   const box=[...document.querySelectorAll('.bsec')].find(b=>b.querySelector('.bsec__title')?.textContent===wanted);
   return box?[...box.querySelectorAll('.items .item')].filter(item=>item.textContent.includes(needle)).length:0;
  },[title,name]);
  // 3. Правка типа сохраняется и меняет раздел: место становится справочником.
  const beforePlaces=await sectionRows('Места','Соляные амбары');
  await page.evaluate(()=>{
   const row=[...document.querySelectorAll('.items .item')].find(item=>item.textContent.includes('Соляные амбары'));
   const control=row.querySelector('select');control.value='monster';
   control.dispatchEvent(new Event('change',{bubbles:true}));
  });
  await page.waitForFunction(()=>{
   const sections=[...document.querySelectorAll('.bsec')];
   const reference=sections.find(box=>box.querySelector('.bsec__title')?.textContent==='Справочник');
   return reference&&reference.textContent.includes('Соляные амбары');
  },{timeout:30000});
  const saved=await sectionText('Справочник');
  assert.match(saved,/Соляные амбары/,'правка не попала в справочник: '+saved);
  assert.match(saved,/монстр|исправлено человеком/,'правка не показана в справочнике: '+saved);
  // Выбор типа показывает сохранённый тип, а не пустое поле: у записи
  // справочника `entity_type` в единственном числе и совпадает с option.
  const referenceType=await page.evaluate(()=>{
   const box=[...document.querySelectorAll('.bsec')].find(b=>b.querySelector('.bsec__title')?.textContent==='Справочник');
   const row=box&&[...box.querySelectorAll('.items .item')].find(item=>item.textContent.includes('Соляные амбары'));
   const select=row&&row.querySelector('select');
   return select?select.value:'';
  });
  assert.equal(referenceType,'monster','тип записи справочника не выбран в списке: '+referenceType);
  // Одно и то же имя книга печатает в нескольких модулях страницы: правка
  // снимает ОДНУ строку (ту, у которой названа страница), а не все сразу.
  const seen=await sectionRows('Места','Соляные амбары');
  assert.equal(seen,beforePlaces-1,'место не ушло из «Мест»: было '+beforePlaces+', стало '+seen);
  await page.screenshot({path:path.join(out,'reference-editor-1280.png'),fullPage:true});

  // 4. После перезагрузки экрана правка на месте: она хранится, а не живёт в DOM.
  await page.reload({waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>typeof window.soloOpenAdventureLibrary==='function');
  await page.evaluate(()=>window.soloOpenAdventureLibrary());
  await page.waitForFunction(()=>document.querySelector('#adventure-library .page-card button'));
  await page.locator('#adventure-library .page-card button').first().click();
  await page.waitForFunction(()=>document.querySelector('#adventure-library .readiness'));
  await page.locator('#adventure-library button',{hasText:'Подготовка Мастера'}).first().click();
  await page.waitForFunction(()=>document.querySelector('.cards-list .page-card'));
  await page.waitForFunction(()=>{
   const sections=[...document.querySelectorAll('.bsec')];
   const reference=sections.find(box=>box.querySelector('.bsec__title')?.textContent==='Справочник');
   return reference&&reference.textContent.includes('Соляные амбары');
  },{timeout:30000});
  assert.deepEqual(errors,[],'ошибки страницы: '+errors.join(' | '));
  console.log(JSON.stringify({appendix:appendixText.split('\n')[0],quote:quote.split('\n')[0],
   checks:['приложение без старта','цитата со страницы','выбор типа','правка сохранена после перезагрузки']}));
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});

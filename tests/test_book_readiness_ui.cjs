// A05/F11: экран библиотеки печатает ОДИН статус книги. Старое сообщение
// операции («Модулей: 12») лежит в истории и не спорит с числом модулей.
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
  await page.waitForFunction(()=>document.getElementById('adventure-library').classList.contains('active'));
  await page.waitForFunction(()=>document.querySelector('#adventure-library .page-card'));
  const card=await page.locator('#adventure-library .page-card').first().innerText();
  // Одно число модулей: эпизоды и приложения названы по отдельности и не спорят.
  assert.match(card,/Эпизоды: 11/,'карточка не назвала эпизоды: '+card);
  assert.match(card,/Приложения: 1/,'карточка не назвала приложения: '+card);
  assert.doesNotMatch(card,/Модулей: 12/,'старое сообщение операции попало в статус карточки: '+card);
  assert.match(card,/Старт готов|Старт не готов/,'карточка не сказала про старт: '+card);
  await page.screenshot({path:path.join(out,'library-card-1280.png'),fullPage:false});

  await page.locator('#adventure-library .page-card button').first().click();
  await page.waitForFunction(()=>document.querySelector('#adventure-library .readiness'));
  const steps=await page.locator('#adventure-library .readiness-step').count();
  assert.equal(steps,6,'на экране не шесть шагов книги: '+steps);
  const bundleText=await page.locator('#adventure-library').innerText();
  // Сообщение прошлой операции — только в закрытой истории.
  assert.doesNotMatch(bundleText,/Модулей: 12/,'старое сообщение видно как статус: '+bundleText.slice(0,600));
  const history=page.locator('#adventure-library details summary',{hasText:'История операций'});
  assert.equal(await history.count(),1,'нет раздела истории операций');
  await history.first().click();
  const opened=await page.locator('#adventure-library').innerText();
  assert.match(opened,/Модулей: 12/,'история не показывает запись операции: '+opened.slice(0,600));
  assert.match(opened,/не описывают текущее состояние/,'история не подписана как история');

  // У каждого предупреждения есть кнопка действия из закрытого набора.
  const warnings=await page.locator('#adventure-library .readiness-warnings .warning').count();
  assert(warnings>0,'на неразобранной книге нет ни одного предупреждения');
  const buttons=await page.locator('#adventure-library .readiness-warnings .warning button').count();
  assert.equal(buttons,warnings,'не у каждого предупреждения есть действие: '+buttons+' из '+warnings);
  const labels=await page.locator('#adventure-library .readiness-warnings .warning button').allInnerTexts();
  assert(labels.every(text=>['Исправить','Повторить','Подробности'].includes(text)),'действие вне набора: '+labels);
  const headline=await page.locator('#adventure-library .readiness-headline').innerText();
  assert.match(headline,/Играть пока нельзя|Можно играть/,'нет одного статуса книги: '+headline);

  await page.setViewportSize({width:375,height:812});
  await page.evaluate(()=>document.getElementById('adventure-library').scrollIntoView());
  await page.screenshot({path:path.join(out,'readiness-375.png'),fullPage:true});
  await page.setViewportSize({width:1280,height:900});
  await page.screenshot({path:path.join(out,'readiness-1280.png'),fullPage:true});
  assert.deepEqual(errors,[],'ошибки страницы: '+errors.join(' | '));
  console.log(JSON.stringify({steps,warnings,buttons,headline,checks:['шесть шагов','одно число модулей','действие у каждого предупреждения','старое сообщение только в истории']}));
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});

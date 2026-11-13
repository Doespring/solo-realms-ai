// A03/F01: восемь шагов мастера → «Выбрать героя Nat20» → тот же черновик → история.
// Затем второй вход — карточка книги в «Моих приключениях» → те же 3 из 3.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const [base,temp,draft,bundle,moduleId,itemsRaw,itemNamesRaw,opening]=process.argv.slice(2);
const items=String(itemsRaw||'').split('|').filter(Boolean);
const itemNames=String(itemNamesRaw||'').split('|').filter(Boolean);
const START='Гринест у заставы';
(async()=>{
 const launch=async()=>{const binary=process.env.SOLO_UI_BROWSER||undefined;try{return await chromium.launch({channel:binary?undefined:(process.env.SOLO_UI_CHANNEL||'msedge'),executablePath:binary,headless:true});}catch(error){if(process.env.SOLO_UI_CHANNEL&&!binary)throw error;return await chromium.launch({executablePath:binary,headless:true});}};
 const browser=await launch(),context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 page.setDefaultTimeout(40000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const button=text=>page.locator('#preparation button').filter({hasText:text}).first();
 const resume=async(id,step)=>{await page.waitForFunction(()=>typeof window.soloResumePreparation==='function');await page.evaluate(([id,s])=>window.soloResumePreparation(id,s),[id,step]);await page.waitForFunction(()=>document.querySelector('#preparation.active'));await page.waitForFunction(()=>document.querySelector('#preparation h2'));};
 const heading=text=>page.waitForFunction(t=>document.querySelector('#preparation h2')?.textContent===t,text);
 const counts=async()=>page.evaluate(async()=>{const c=await(await fetch('/api/creation/drafts')).json();const p=await(await fetch('/api/preparation/drafts')).json();return {creation:c.map(row=>row.id),preparation:p.map(row=>row.id)};});
 const story=async id=>page.evaluate(async campaign=>{const s=await(await fetch('/api/campaigns/'+campaign)).json();const c=await(await fetch('/api/campaigns/'+campaign+'/character')).json();return {hero:s.state&&s.state.hero&&s.state.hero.name,location:s.state&&s.state.location,equipment:((s.state&&s.state.preparation)||{}).equipment||[],shown:(c.equipment||[]).filter(row=>row.source==='preparation').map(row=>row.id)};},id);
 // Герой собирается в редакторе: «Рекомендовать» закрывает оставшиеся шаги, ничего не
 // подтверждая; дальше игрок сам жмёт «Создать героя». На 390 px подвал редактора
 // прячет «Рекомендовать» — это дизайн, поэтому редактор идёт на ширине стола.
 const finishHero=async()=>{
  await page.waitForFunction(()=>window.soloWizard&&window.soloWizard.state().contract);
  await page.setViewportSize({width:1280,height:900});
  await page.locator('#wizard button.wizard-recommend').click();
  await page.waitForFunction(()=>{const c=window.soloWizard.state().contract;return c&&(c.confirm_ready===undefined?c.ready:c.confirm_ready);},null,{timeout:90000});
  await page.evaluate(()=>window.soloWizard.goto('final'));
  await page.waitForFunction(()=>{const b=[...document.querySelectorAll('#wizard button')].find(x=>x.textContent.trim()==='Создать героя');return b&&!b.disabled;},null,{timeout:90000});
  await page.locator('#wizard button').filter({hasText:'Создать героя'}).first().click();
  await page.waitForFunction(()=>document.querySelector('#play.active'),null,{timeout:120000});
  return page.evaluate(()=>localStorage.getItem('solo-campaign'));
 };
 try{
  await page.goto(base,{waitUntil:'domcontentloaded'});

  // Шаг 3 «Подготовка»: возврат назад не теряет введённое.
  await resume(draft,2);
  const startField=page.locator('#preparation input[aria-label="Название открытой стартовой локации"]');
  const openingField=page.locator('#preparation textarea[aria-label="Открытая стартовая сцена — только то, что герой может знать"]');
  assert.equal(await startField.inputValue(),'Гринест','старт из подготовки не показан');
  assert.equal(await openingField.inputValue(),opening,'стартовая сцена из подготовки не показана');
  await startField.fill(START);
  await startField.dispatchEvent('change');
  await openingField.fill('Пламя над Гринестом. Улица пуста, но из окон смотрят люди.');
  await openingField.dispatchEvent('change');
  await button('Продолжить').click();
  await heading('Подтверждение');
  // Правка старта снимает подтверждение подготовки — так и должно быть: игрок
  // подтверждает снова, иначе запуск начал бы сцену, которую никто не одобрял.
  await page.locator('#preparation input[type=checkbox]').first().check();
  await button('Назад').click();
  await heading('Подготовка');
  assert.equal(await page.locator('#preparation input[aria-label="Название открытой стартовой локации"]').inputValue(),START,'возврат потерял старт');
  assert.match(await page.locator('#preparation textarea[aria-label="Открытая стартовая сцена — только то, что герой может знать"]').inputValue(),/Пламя над Гринестом/,'возврат потерял сцену');
  await button('Продолжить').click();
  await heading('Подтверждение');
  assert.equal(await page.locator('#preparation input[type=checkbox]').first().isChecked(),true,'подтверждение подготовки сброшено');

  // Шаг 7 «Снаряжение»: назад и снова вперёд — те же три предмета.
  await resume(draft,6);
  const checked=async()=>page.locator('#preparation .inventory-grid input[type=checkbox]:checked').count();
  assert.equal(await checked(),items.length,'снаряжение из подготовки не отмечено');
  await button('Назад').click();
  await heading('Предыстория');
  await button('Продолжить').click();
  await heading('Снаряжение');
  assert.equal(await checked(),items.length,'возврат потерял снаряжение');

  // Шаг 8 «Начало истории»: кнопка передаёт ЭТОТ черновик.
  await resume(draft,7);
  await button('Выбрать героя Nat20').click();
  await page.waitForFunction(()=>document.querySelector('#creation.active'));
  await page.waitForFunction(()=>document.querySelector('#creation-handoff')&&!document.querySelector('#creation-handoff').hidden);
  const note=await page.locator('#creation-handoff').innerText();
  /* n16: в тексте для игрока герой и предметы называются без служебного
     «Старт: «не назван» · предметов: 0» и без UUID черновика. */
  assert.match(note,/Предметов: 3/,'запуск не назвал переданные предметы: '+note);
  assert.match(note,/Герой: Лира/,'запуск не назвал переданного героя: '+note);
  assert.match(note,/Гринест/,'запуск не назвал старт: '+note);
  // Материалы того же черновика видны, а не пустое «Новое приключение».
  await page.waitForFunction(()=>/tiamat\.pdf/.test(document.querySelector('#creation .creation-files')?.innerText||''),null,{timeout:20000});
  const files=await page.locator('#creation .creation-files').innerText();
  assert.match(files,/tiamat\.pdf/,'материалы черновика не открылись: '+files);
  assert.equal(await page.evaluate(()=>localStorage.getItem('solo-creation-draft')),draft,'запуск открыл другой черновик');
  // Черновик остался ОДИН: ни мастер, ни запуск не создали второй.
  let ids=await counts();
  assert.deepEqual(ids.creation,[draft],'запуск создал новый черновик: '+JSON.stringify(ids));
  assert.deepEqual(ids.preparation,[draft],'лишний черновик после прохода: '+JSON.stringify(ids.preparation));

  // Проверенная глава книги — законный старт без подготовки мира.
  const bookButton=page.locator('#creation-world button').filter({hasText:'начать по книге'}).first();
  assert.equal(await bookButton.isHidden(),false,'нет кнопки старта по проверенной главе');
  await bookButton.click();
  await page.waitForFunction(()=>{const root=document.querySelector('#wizard');return root&&root.hidden===false;});
  await page.waitForFunction(()=>window.soloWizard&&window.soloWizard.state().contract);
  const wizardState=await page.evaluate(()=>window.soloWizard.state());
  assert.equal(wizardState.draft.name,'Лира','редактор не подставил имя героя мастера');
  assert.match(String(wizardState.draft.backstory||''),/Гринест/,'редактор не подставил предысторию мастера');
  assert(wizardState.screens.some(row=>row.id==='gear'),'шаг снаряжения не открылся');
  assert.equal(await page.evaluate(()=>window.soloWizard.goto('gear')),true);
  await page.waitForFunction(()=>document.querySelector('#wizard .wizard-stage').textContent.includes('Из подготовки Мастера'));
  const gear=await page.locator('#wizard .wizard-stage').innerText();
  assert.match(gear,/Из подготовки Мастера: 3/,'предметы мастера не показаны в редакторе: '+gear);

  // «Начать историю»: имя героя, старт и 3 предмета — в новой истории.
  const first=await finishHero();
  const one=await story(first);
  assert.equal(one.hero,'Лира','в истории нет имени героя: '+JSON.stringify(one));
  assert.equal(one.location,START,'в истории не тот старт: '+JSON.stringify(one));
  assert.deepEqual(one.equipment,items,'в истории не 3 предмета мастера: '+JSON.stringify(one));
  assert.deepEqual(one.shown.slice().sort(),items.slice().sort(),'лист героя не показывает 3 предмета: '+JSON.stringify(one));
  const play=await page.locator('#play').innerText();
  assert.match(play,/Пламя над Гринестом/,'история не началась с открывающей сцены: '+play.slice(0,300));
  assert.match(play,/Лира/,'в истории нет героя: '+play.slice(0,300));
  ids=await counts();
  assert.deepEqual(ids.creation,[draft],'после старта появился второй черновик: '+JSON.stringify(ids));

  // Второй вход — из книги: «Библиотека» → модуль → «Создать героя» (тот же обработчик,
  // что висит на карточке модуля) заводит НОВЫЙ черновик Мастера для той же книги.
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(([b,m])=>window.soloChooseAdventure(b,m),[bundle,moduleId]);
  await page.waitForFunction(()=>document.querySelector('#preparation.active'));
  await page.waitForFunction(()=>document.querySelector('#preparation h2'));
  const secondPass=await page.evaluate(()=>localStorage.getItem('solo-draft'));
  assert(secondPass&&secondPass!==draft,'второй вход не завёл свой черновик: '+secondPass);
  const stepNow=()=>page.evaluate(()=>document.querySelector('#preparation h2')?.textContent);
  const stepIs=text=>page.waitForFunction(t=>document.querySelector('#preparation h2')?.textContent===t,text);
  const forward=async text=>{await button('Продолжить').click();await stepIs(text);};
  await stepIs('Источник');
  await forward('Правила и Мастер');
  await forward('Подготовка');
  await page.locator('#preparation input[aria-label="Название открытой стартовой локации"]').fill(START);
  await page.locator('#preparation input[aria-label="Название открытой стартовой локации"]').dispatchEvent('change');
  await page.locator('#preparation textarea[aria-label="Открытая стартовая сцена — только то, что герой может знать"]').fill(opening);
  await page.locator('#preparation textarea[aria-label="Открытая стартовая сцена — только то, что герой может знать"]').dispatchEvent('change');
  await forward('Подтверждение');
  await page.locator('#preparation input[type=checkbox]').first().check();
  await forward('Лист героя');
  for(const [label,value] of [['Имя героя','Лира'],['Вид / раса','Человек'],['Класс','Воин']]){
    const field=page.locator('#preparation input[aria-label="'+label+'"]');
    await field.fill(value);await field.dispatchEvent('change');
  }
  await forward('Предыстория');
  await page.locator('#preparation textarea[aria-label="История героя: происхождение, события, мотивация"]').fill('Выросла в Гринесте и знает каждую улицу.');
  await page.locator('#preparation textarea[aria-label="История героя: происхождение, события, мотивация"]').dispatchEvent('change');
  await page.locator('#preparation input[aria-label="Я подтверждаю эту предысторию для включения в кампанию"]').check();
  await forward('Снаряжение');
  for(const name of itemNames)await page.locator('#preparation label').filter({hasText:name}).first().locator('input').check();
  assert.equal(await page.locator('#preparation .inventory-grid input:checked').count(),items.length,'второй вход: отмечено не 3 предмета');
  await forward('Начало истории');
  await button('Выбрать героя Nat20').click();
  await page.waitForFunction(()=>document.querySelector('#creation.active'),null,{timeout:40000});
  await page.waitForFunction(()=>document.querySelector('#creation-handoff')&&!document.querySelector('#creation-handoff').hidden);
  assert.equal(await page.evaluate(()=>localStorage.getItem('solo-creation-draft')),secondPass,'второй вход открыл не свой черновик');
  const secondNote=await page.locator('#creation-handoff').innerText();
  assert.match(secondNote,/Герой: Лира/,'второй вход не назвал героя: '+secondNote);
  assert.match(secondNote,/Предметов: 3/,'второй вход не назвал предметы: '+secondNote);
  const secondBook=page.locator('#creation-world button').filter({hasText:'начать по книге'}).first();
  assert.equal(await secondBook.isHidden(),false,'второй вход не дал старт по проверенной главе');
  await secondBook.click();
  await page.waitForFunction(()=>{const root=document.querySelector('#wizard');return root&&root.hidden===false;});
  const two=await story(await finishHero());
  assert.equal(two.hero,'Лира','второй вход: нет имени героя: '+JSON.stringify(two));
  assert.equal(two.location,START,'второй вход: не тот старт: '+JSON.stringify(two));
  assert.deepEqual(two.equipment,items,'второй вход: не 3 предмета: '+JSON.stringify(two));
  assert.deepEqual(two.shown.slice().sort(),items.slice().sort(),'второй вход: лист без предметов: '+JSON.stringify(two));
  ids=await counts();
  assert.deepEqual(ids.creation.slice().sort(),[draft,secondPass].sort(),'второй вход завёл лишний черновик: '+JSON.stringify(ids));
  assert.deepEqual(ids.preparation.slice().sort(),[draft,secondPass].sort(),'второй вход завёл лишний черновик мастера: '+JSON.stringify(ids));
  assert.deepEqual(errors,[],'ошибки страницы: '+errors.join(' | '));
  console.log('кивок A03/F01: шаги 3 и 7 держат введённое; запуск открыл тот же черновик ('+draft+'), предметов 3, герой «Лира», старт «'+START+'»; второй вход из книги ('+secondPass+') — те же 3 из 3');
 }finally{await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

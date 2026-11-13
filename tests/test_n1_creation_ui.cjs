/* n16/N1: первый кадр экрана создания и экран подготовки книги.
   Проверяет ровно то, что просил критик захода n16, и на 375, и на 1440:
     • «Подготовить приключение» стоит в кадре (top ≥ 0, bottom ≤ высоты кадра,
       страница не прокручена);
     • в тексте для игрока нет UUID черновика и слова «mock»;
     • у экрана подготовки есть заголовок «Подготовка книги», имя файла и одно
       действие «Отменить подготовку» — все три в кадре без прокрутки;
     • число готовых блоков одно («Готово 3 из 5»), счётчик времени один и он
       убывает («осталось …»);
     • журнал подготовки не пуст и у него есть «Скопировать журнал» даже при
       одной записи.
   Подготовка на стенде — UI-фикстура: ответ `/api/creation/drafts/{id}` несёт тот
   же `preparation`, что отдаёт `pipeline.status` (done/total/progress/retry). */
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;
fs.mkdirSync(out,{recursive:true});
const UUID=/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const SIZES=[{width:375,height:812},{width:1440,height:900}];
const FIXTURE={id:'n1-ui-fixture-run',status:'running',done:3,total:5,
 message:'AI структурирует источники. Сценарий пока не готов.',
 progress:{phase:'extracting',done:3,total:5,failed:1,elapsed_s:96,eta_s:180},
 retry:{done:3,total:5,pending:1,failed:1,exhausted:1,can_retry:true,
        blocks:[{block:4,pages:'страница 1',short:'поле name пришло числом вместо строки'}]},
 canonical_issues:[],usage:null,ready:false};
(async()=>{
 const browser=await chromium.launch(process.env.SOLO_UI_BROWSER
   ?{executablePath:process.env.SOLO_UI_BROWSER,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']}
   :{channel:'msedge',headless:true});
 const checks=[],errors=[];
 try{
  for(const size of SIZES){
   const tag=size.width;
   const context=await browser.newContext({viewport:size});
   const page=await context.newPage();page.setDefaultTimeout(45000);
   page.on('pageerror',e=>errors.push(`${tag}: ${e}`));
   let preparation=null;
   await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
   await page.route(/\/api\/creation\/drafts\/[^/]+$/,async route=>{
    if(route.request().method()!=='GET'||!preparation)return route.continue();
    const response=await route.fetch(),draft=await response.json();
    draft.preparation=preparation;
    /* Книга, по которой идёт подготовка: у настоящего прогона документ привязан
       к черновику, и панель называет его в шапке. */
    if(!(draft.files||[]).length)draft.files=[{document:'n1-book',name:'книга севера.pdf',format:'.pdf',bytes:1048576,job:{id:'n1-job',status:'complete',done:1,total:1}}];
    await route.fulfill({response,json:draft});
   });
   /* `load` на `/` не наступает вовсе (висит один из ресурсов) — как и в
      scripts/probe_r249_live.cjs, идём по `domcontentloaded`. */
   await page.goto(base,{waitUntil:'domcontentloaded'});
   await page.waitForFunction(()=>window.soloPrototypeEnabled!==undefined&&typeof window.soloOpenCreation==='function');

   /* 1. Призыв в первом кадре. */
   await page.evaluate(()=>window.soloOpenCreation());
   await page.waitForFunction(()=>document.querySelector('#creation.view.active'));
   await page.waitForFunction(()=>!!document.querySelector('#creation-description'));
   await page.waitForTimeout(400);
   const fold=await page.evaluate(()=>{
    const button=[...document.querySelectorAll('#creation .button-row button')]
      .find(b=>/Подготовить приключение|Проверить готовность/.test(b.textContent))||null;
    if(!button)return null;
    const r=button.getBoundingClientRect();
    return {text:button.textContent.trim(),top:Math.round(r.top),bottom:Math.round(r.bottom),
            viewportH:innerHeight,scrollY:Math.round(scrollY),hidden:button.hidden,
            visible:r.width>0&&r.height>0};
   });
   assert(fold,'кнопка призыва не найдена на экране создания');
   assert(fold.visible,'призыв не отрисован');
   assert.equal(fold.scrollY,0,'страница не должна быть прокручена при открытии');
   assert(fold.top>=0,`призыв начинается выше кадра: top=${fold.top}`);
   assert(fold.bottom<=fold.viewportH,`призыв ниже сгиба: bottom=${fold.bottom} при кадре ${fold.viewportH}`);
   checks.push(`${tag}: призыв «${fold.text}» в кадре: top ${fold.top}, bottom ${fold.bottom} ≤ ${fold.viewportH}`);
   await page.screenshot({path:path.join(out,`n1-new-${tag}.png`),fullPage:false});

   /* 2. В тексте для игрока нет UUID и слова «mock». */
   const text=await page.evaluate(()=>document.querySelector('#creation').innerText);
   const found=text.match(UUID);
   if(found)assert.fail('в тексте экрана есть UUID: '+found[0]);
   const mockLine=text.split('\n').find(line=>/mock/i.test(line));
   if(mockLine)assert.fail('в тексте экрана осталось «mock»: '+mockLine);
   checks.push(`${tag}: текст экрана без UUID и без «mock»`);

   /* 3. Экран подготовки: заголовок, имя файла, одно действие — в кадре. */
   preparation=FIXTURE;
   await page.evaluate(()=>window.soloOpenCreation(window.localStorage.getItem('solo-creation-draft')));
   await page.waitForFunction(()=>!!document.querySelector('#creation-progress-panel .prep-panel'));
   await page.waitForTimeout(400);
   const panel=await page.evaluate(()=>{
    const p=document.querySelector('#creation-progress-panel .prep-panel');
    const txt=node=>(node&&node.textContent||'').replace(/\s+/g,' ').trim();
    const box=node=>{if(!node||node.hidden)return null;const r=node.getBoundingClientRect();
      return {top:Math.round(r.top),bottom:Math.round(r.bottom),visible:r.width>0&&r.height>0,
              inFrame:r.top>=0&&r.bottom<=innerHeight};};
    return {
      viewportH:innerHeight,scrollY:Math.round(scrollY),
      panelBox:box(p),
      title:txt(p.querySelector('.prep-title')),
      titleBox:box(p.querySelector('.prep-title')),
      file:txt(p.querySelector('.prep-file')),
      fileBox:box(p.querySelector('.prep-file')),
      phase:txt(p.querySelector('.prep-phase')),
      clock:txt(p.querySelector('.prep-clock')),
      clockBox:box(p.querySelector('.prep-clock')),
      meta:txt(p.querySelector('.prep-meta')),
      cancel:txt(p.querySelector('.prep-cancel')),
      cancelBox:box(p.querySelector('.prep-cancel')),
      copy:txt(p.querySelector('.prep-copy')),
      copyBox:box(p.querySelector('.prep-copy')),
      rows:p.querySelectorAll('.prep-failed-list li').length,
      barWidth:(p.querySelector('.prep-fill')||{}).style?.width||'',
      panelText:txt(p),
      body:(document.querySelector('#creation').innerText||'')
    };
   });
   assert.equal(panel.title,'Подготовка книги','заголовок экрана подготовки потерян: '+panel.title);
   assert(panel.titleBox&&panel.titleBox.inFrame,`заголовок подготовки вне кадра: ${JSON.stringify(panel.titleBox)}`);
   assert(panel.file&&panel.file.length>0&&panel.fileBox,'имя файла не показано');
   assert(panel.fileBox.inFrame,`имя файла вне кадра: ${JSON.stringify(panel.fileBox)}`);
   assert(/Отменить подготовку/.test(panel.cancel)&&panel.cancelBox,'действия «Отменить подготовку» нет в кадре');
   assert(panel.cancelBox.inFrame,`«Отменить подготовку» вне кадра: ${JSON.stringify(panel.cancelBox)}`);
   assert(/^осталось /.test(panel.clock)&&panel.clockBox,'часы не показывают остаток: '+panel.clock);
   checks.push(`${tag}: шапка «${panel.title}» (${panel.titleBox.top}–${panel.titleBox.bottom}) · ${panel.file} · «${panel.cancel}» (${panel.cancelBox.top}–${panel.cancelBox.bottom}) · ${panel.clock}`);

   /* 4. Одно число готовых блоков и нет спорящей «Части 5 из 5». */
   assert(/Готово 3 из 5/.test(panel.meta),'в строке чисел нет «Готово 3 из 5»: '+panel.meta);
   assert(!/Часть\s+\d+\s+из\s+\d+/.test(panel.meta),'в строке чисел снова «Часть N из M»: '+panel.meta);
   assert(!/Подготовка:\s*\d+\s*\/\s*\d+/.test(panel.body),'строка экрана снова дублирует счёт блоков');
   assert(!/Часть\s+5\s+из\s+5/.test(panel.meta+panel.phase),'«Часть 5 из 5» вернулась');
   /* n16: счётчик «3 / 5» строкой экрана — тот же дубль, что и «Часть N из M»:
      вне панели чисел прогресса блоков быть не должно. «1 / 1» у файла — это
      извлечение одного файла, а не блоки книги, поэтому ищем именно дописку
      строки экрана («… · 3 / 5») и «N / M блоков». */
   const outside=panel.body.split(panel.panelText).join(' ');
   const stray=/·\s*\d+\s*\/\s*\d+|\d+\s*\/\s*\d+\s*блок/.exec(outside);
   if(stray)assert.fail('вне панели остался второй счётчик блоков: '+stray[0]);
   checks.push(`${tag}: числа: «${panel.meta}», заливка ${panel.barWidth}, фаза «${panel.phase}»`);

   /* n16: «Отменить подготовку» — одно действие экрана, а не два одинаковых. */
   const cancels=await page.evaluate(()=>[...document.querySelectorAll('#creation button')]
     .filter(b=>/Отменить подготовку/.test(b.textContent)&&!b.hidden&&b.getBoundingClientRect().width>0).length);
   assert.equal(cancels,1,`кнопок «Отменить подготовку» на экране: ${cancels}`);
   checks.push(`${tag}: «Отменить подготовку» — одна кнопка на экране`);

   /* 5. Журнал: одна запись — но кнопка копирования есть. */
   assert(panel.rows>=1,'журнал подготовки пуст');
   assert(/Скопировать журнал/.test(panel.copy)&&panel.copyBox,'кнопки «Скопировать журнал» нет при одной записи');
   checks.push(`${tag}: журнал: ${panel.rows} запись(и), кнопка «${panel.copy}» на месте`);

   /* Часы убывают: два замера с интервалом. */
   const first=panel.clock,second=await page.evaluate(()=>new Promise(resolve=>{
    const read=()=>((document.querySelector('#creation-progress-panel .prep-clock')||{}).textContent||'').trim();
    setTimeout(()=>resolve(read()),2200);
   }));
   const seconds=value=>{const m=/(\d+):(\d+)/.exec(value);return m?Number(m[1])*60+Number(m[2]):null;};
   assert(seconds(second)!==null&&seconds(first)!==null,'часы не в формате M:SS');
   assert(seconds(second)<seconds(first),`часы не убывают: ${first} → ${second}`);
   checks.push(`${tag}: часы идут вниз: ${first} → ${second}`);

   await page.screenshot({path:path.join(out,`n1-prep-${tag}.png`),fullPage:false});
   await page.evaluate(()=>{const p=document.querySelector('#creation-progress-panel .prep-panel');if(p)p.scrollIntoView({block:'start'});});
   await page.waitForTimeout(200);
   await page.screenshot({path:path.join(out,`n1-prep-log-${tag}.png`),fullPage:false});
   await context.close();
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'n1-creation.json'),JSON.stringify({checks,page_errors:errors},null,2));
  console.log(JSON.stringify({checks,page_errors:errors}));
 }catch(error){throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

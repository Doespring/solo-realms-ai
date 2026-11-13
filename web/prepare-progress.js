(()=>{
 'use strict';
 /* Полоса подготовки книги: живой прогресс с фразами, как в загрузке The Sims.
    Владелец видит не «7 / 9 блоков», а что именно сейчас происходит. */
 const FLAVOR={
  reading:['Перелистываю страницы…','Отделяю колонтитулы от текста…','Считаю страницы, где есть текст…','Ищу, с чего начинается история…'],
  extracting:['Ищу локации на карте…','Выписываю имена тех, кто встретится…','Сверяю проверки и спасброски…','Собираю сцены и события…','Раскладываю карту на столе…','Читаю, что осталось за кадром…'],
  assembling:['Свожу блоки в один мир…','Проверяю, что все дороги ведут куда-то…','Считаю, что получилось…']};
 const AMBIENT=['Свеча догорает, а дело идёт…','Кто-то скрипит стулом в таверне…','Мастер перебирает заметки…','За окном шумит дождь…'];
 const PHASE_TITLE={reading:'Читаю книгу',extracting:'Разбираю книгу на части',assembling:'Свожу части вместе'};
 /* Порядок шагов виден всегда: короткая фаза («чтение» на тексте в 9 тыс.
    знаков длится меньше секунды) иначе не оставляет следа на экране, и
    владелец видит одну и ту же строку весь прогон. */
 const PHASE_ORDER=['reading','extracting','assembling'];
 const PHASE_STEP={reading:'Чтение',extracting:'Разбор',assembling:'Сборка'};
 /* Сколько держать последнее состояние: иначе полоса исчезает ровно в тот
    миг, когда появляется результат, и «3 из 3» не видит никто. */
 const FINISH_MS=6000;
 const fmt=seconds=>{const s=Math.max(0,Math.round(Number(seconds)||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
 const mk=(tag,text,cls)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node};
 const el=(tag,text,cls)=>mk(tag,text,cls);
 const still=()=>window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 /* «2 блока», «5 блоков»: число и слово читаются вместе, поэтому склоняем. */
 const blockWord=n=>{const hundred=n%100;if(hundred>=11&&hundred<=14)return'блоков';const last=n%10;return last===1?'блок':last>=2&&last<=4?'блока':'блоков'};
 /* Сервер говорит «страницы 88–104»; в списке под полосой это «стр. 88–104». */
 const pagesText=value=>{const text=String(value||'');return text.startsWith('страницы ')?'стр. '+text.slice(8):text.startsWith('страница ')?'стр. '+text.slice(9):text};
 let panels=new WeakMap();

 function build(host){
  const root=el('div',undefined,'prep-panel');root.setAttribute('role','group');root.setAttribute('aria-label','Ход подготовки приключения');
  /* n16: у экрана подготовки должен быть свой заголовок. Было «Подготовка» без
     имени файла, а h1 экрана создания уезжал выше кадра — игрок видел обрезок
     поля и три служебные кнопки. Теперь шапка панели сама отвечает на три
     вопроса: что делаем («Подготовка книги»), с чем (имя файла) и что можно
     сделать (одно действие — отменить). */
  const head=el('div',undefined,'prep-head');
  const title=el('h2','Подготовка книги','prep-title');
  const file=el('p','','prep-file');file.hidden=true;
  const clock=el('p','','prep-clock');clock.hidden=true;
  const phase=el('p','','prep-phase');
  const cancel=el('button','Отменить подготовку','prep-cancel');cancel.type='button';cancel.hidden=true;
  head.append(title,clock,file,phase,cancel);
  const steps=el('p',undefined,'prep-steps');steps.setAttribute('aria-label','Шаги подготовки');
  PHASE_ORDER.forEach((phase,index)=>{
   if(index)steps.append(Object.assign(el('span','→','prep-arrow'),{'aria-hidden':'true'}));
   const step=el('span',PHASE_STEP[phase],'prep-step');step.dataset.phase=phase;steps.append(step);
  });
  const bar=el('div',undefined,'prep-bar');bar.setAttribute('role','progressbar');bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');
  const fill=el('div',undefined,'prep-fill'),shine=el('span',undefined,'prep-shine');fill.append(shine);bar.append(fill);
  const ticks=el('div',undefined,'prep-ticks');
  /* n11: у живой фразы есть подпись «Сейчас». Без неё ротатор читается как
     случайная строка, а не как ответ на вопрос, что идёт прямо сейчас. */
  const now=el('p','Сейчас','prep-now');
  const caption=el('p','','prep-caption');caption.setAttribute('aria-live','polite');
  const meta=el('p','','prep-meta');
  /* r241: отказавшие блоки называются списком под полосой. Причина живёт в
     базе (`creation_parts.diagnostic`), и без неё «Готово 9 из 12» не отвечает
     на единственный вопрос владельца: ждать или добивать. */
  const failed=el('div',undefined,'prep-failed');failed.hidden=true;
  const failedTitle=el('p','','prep-failed-title');
  const failedList=el('ul',undefined,'prep-failed-list');
  const retry=el('button','Добить','prep-retry');retry.type='button';retry.hidden=true;
  /* n16: кнопка копирования есть всегда, когда журнал показан, — даже с одной
     записью. Игрок приносит журнал в отчёт, а не переписывает его руками. */
  const copy=el('button','Скопировать журнал','prep-copy');copy.type='button';copy.hidden=true;
  const actions=el('div',undefined,'prep-actions');
  failed.append(failedTitle,failedList,actions);actions.append(retry,copy);
  root.append(head,steps,bar,ticks,now,caption,meta,failed);host.append(root);
  const state={host,root,title,clock,file,phase,cancel,steps,bar,fill,ticks,now,caption,meta,failed,failedTitle,failedList,retry,copy,actions,started:Date.now(),phaseName:'',tick:0,timer:null,rotor:null,exit:null,finished:false,retrying:false,cancelling:false,copied:'',failure:'',fileName:'',remaining:null,run:null,lastDone:-1};
  retry.onclick=()=>retryFailed(state,state.run);
  copy.onclick=()=>copyJournal(state,state.run);
  cancel.onclick=()=>cancelRun(state,state.run);
  return state;
 }

 /* Список отказов: номер блока, место в книге и причина словами. Технический
    код диагностики (`unknown_source_span`, `string_type`) на экран не идёт —
    он остаётся в JSON для разбора. */
 function renderFailures(state,run){
  const blocks=run&&run.retry&&Array.isArray(run.retry.blocks)?run.retry.blocks:[];
  const issues=run&&Array.isArray(run.canonical_issues)?run.canonical_issues:[];
  state.root.classList.toggle('is-failed',Boolean(blocks.length||issues.length));
  if(!blocks.length&&!issues.length){
   state.failed.hidden=true;state.failedList.replaceChildren();state.retry.hidden=true;state.copy.hidden=true;
   return;
  }
  const running=run.status==='running'||run.status==='queued';
  state.failed.hidden=false;
  /* n16: заголовок журнала называет обе части: и отказавшие блоки, и замечания
     канона. Раньше он говорил только про блоки, и записи канона повисали без
     подписи. */
  state.failedTitle.textContent=blocks.length?(blocks.length===1?'Один блок не разобрался — остальные части готовы':blocks.length+' '+blockWord(blocks.length)+' не разобрались — остальные части готовы'):'Замечания канона — остальные части готовы';
  state.failedList.replaceChildren(...blocks.map(block=>{
   const item=el('li');
   item.append(el('span','Блок '+block.block,'prep-block'));
   const pages=pagesText(block.pages);if(pages)item.append(el('span',' ('+pages+')','prep-pages'));
   item.append(el('span',': '+(block.short||'ответ не прошёл проверку'),'prep-why'));
   return item;
  }),...issues.map(issue=>{
   const item=el('li');
   item.append(el('span','Канон','prep-block'),el('span',': '+issue.count+' × '+issue.reason,'prep-why'));
   return item;
  }));
  /* Кнопка копирования — всегда, даже когда запись одна: журнал уносят в отчёт. */
  state.copy.hidden=Boolean(state.copied);
  if(!state.copied)state.copy.textContent='Скопировать журнал';
  /* Пока идёт прогон, добивать нечего: остальные блоки ещё читаются. */
  state.retry.hidden=running||!Boolean(run.retry&&run.retry.can_retry);
  if(!state.retry.hidden){
   state.retry.disabled=false;
   /* Ошибка повтора живёт в состоянии панели: опрос экрана перерисовывает
      список каждые 1,2 с и иначе стирал бы её раньше, чем владелец прочёл. */
   state.retry.textContent=state.failure||('Добить '+blocks.length+' '+blockWord(blocks.length));
  }
 }

 /* Текст журнала — то, что уходит в буфер обмена: те же строки, что на экране,
    без служебных кодов схемы. */
 function journalText(state){
  const lines=[];
  for(const block of (state.run&&state.run.retry&&Array.isArray(state.run.retry.blocks)?state.run.retry.blocks:[])){
   const pages=pagesText(block.pages);
   lines.push('Блок '+block.block+(pages?' ('+pages+')':'')+': '+(block.short||'ответ не прошёл проверку'));
  }
  for(const issue of (state.run&&Array.isArray(state.run.canonical_issues)?state.run.canonical_issues:[])){
   lines.push('Канон: '+issue.count+' × '+issue.reason);
  }
  if(state.fileName)lines.unshift('Подготовка книги: '+state.fileName);
  return lines.join('\n');
 }
 async function copyJournal(state,run){
  state.run=run||state.run;
  const text=journalText(state);
  let copied=false;
  try{
   if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(text);copied=true;}
  }catch(error){copied=false;}
  if(!copied){
   /* Буфер обмена без разрешения недоступен (http, iframe): тот же текст через
      скрытое поле — иначе кнопка на телефоне молча ничего не делает. */
   const area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');
   area.style.position='fixed';area.style.left='-9999px';document.body.append(area);area.select();
   try{copied=document.execCommand('copy');}catch(error){copied=false;}
   area.remove();
  }
  state.copied=copied?'Журнал скопирован':'Не удалось скопировать';
  state.copy.textContent=state.copied;state.copy.hidden=false;
  setTimeout(()=>{
   state.copied='';
   if(state.failed&&!state.failed.hidden){state.copy.textContent='Скопировать журнал';state.copy.hidden=false;}
  },2400);
 }

 /* Отмена — то самое одно действие экрана подготовки. Панель не знает про
    черновик и опрос экрана, поэтому после ответа сервера она только сообщает
    событием — экран обновляет его хозяин. */
 async function cancelRun(state,run){
  if(state.cancelling||!run||!run.id)return;
  state.cancelling=true;state.cancel.disabled=true;state.cancel.textContent='Отменяю…';
  try{
   const response=await fetch('/api/creation/runs/'+encodeURIComponent(run.id)+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   if(!response.ok){
    let detail='сервер отказал';
    try{const body=await response.json();detail=(body&&body.detail)||detail}catch(error){}
    throw new Error(detail);
   }
   window.dispatchEvent(new CustomEvent('solo-prep-retry',{detail:{run:run.id}}));
  }catch(error){
   state.cancel.textContent='Не удалось отменить: '+error.message;
   setTimeout(()=>{if(state.cancel)state.cancel.textContent='Отменить подготовку'},4000);
  }finally{
   state.cancelling=false;state.cancel.disabled=false;
   if(state.cancel.textContent==='Отменяю…')state.cancel.textContent='Отменить подготовку';
  }
 }

 /* Кнопка повторяет только отказавшие блоки: сервер (`pipeline.retry`)
    обнуляет попытки `failed`/`interrupted`, принятые не трогает. Панель не
    знает про черновик и очередь запросов экрана, поэтому после успеха она
    только сообщает событием — обновляет экран его хозяин. */
 async function retryFailed(state,run){
  if(state.retrying||!run||!run.id)return;
  state.retrying=true;state.failure='';state.retry.disabled=true;state.retry.textContent='Добиваю…';
  try{
   const response=await fetch('/api/creation/runs/'+encodeURIComponent(run.id)+'/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   if(!response.ok){
    let detail='сервер отказал';
    try{const body=await response.json();detail=(body&&body.detail)||detail}catch(error){}
    throw new Error(detail);
   }
   window.dispatchEvent(new CustomEvent('solo-prep-retry',{detail:{run:run.id}}));
   /* Экран обновляется опросом раз в 1,2 с; если он не ответил, кнопка не
      должна навсегда остаться «Добиваю…». */
   setTimeout(()=>{if(state.retrying||!state.run)return;renderFailures(state,state.run)},10000);
  }catch(error){
   state.failure='Не вышло: '+error.message;
   renderFailures(state,state.run);
   setTimeout(()=>{state.failure='';if(state.run)renderFailures(state,state.run)},6000);
  }finally{state.retrying=false}
 }

 function paint(state,run){
  state.run=run;
  const progress=run.progress||{},phase=PHASE_ORDER.includes(progress.phase)?progress.phase:'extracting';
  const done=Math.max(0,Number(progress.done??run.done??0)),total=Math.max(0,Number(progress.total??run.total??0));
  const failed=Math.max(0,Number(progress.failed||0));
  /* Готовые и упавшие блоки — разные сегменты. Упавший сегмент красится
     красным сразу, даже когда готовых ещё нет: раньше он был невидим, пока
     `done` не перевалит за него, а потом красным становился ГОТОВЫЙ блок. */
  const processed=Math.min(total,done+failed);
  const running=run.status==='running'||run.status==='queued';
  state.phaseName=phase;
  /* n16: «Подготовка книги» — постоянный заголовок экрана, а что идёт сейчас —
     строка под ним. Раньше заголовок переписывался фазой, и в кадре не было
     ни одного слова о том, что это за экран. */
  state.title.textContent='Подготовка книги';
  state.phase.textContent=PHASE_TITLE[phase]||'Разбираю книгу';
  const percent=total?Math.min(100,Math.round(done/total*100)):0;
  state.fill.style.width=percent+'%';
  state.bar.setAttribute('aria-valuenow',String(percent));
  state.bar.setAttribute('aria-valuetext',done+' из '+total+' готово');
  const current=PHASE_ORDER.indexOf(phase);
  Array.from(state.steps.querySelectorAll('.prep-step')).forEach((step,index)=>{
   step.classList.toggle('is-done',index<current||(index===current&&!state.finished&&run.status!=='running'&&run.status!=='queued'));
   step.classList.toggle('is-current',index===current&&(run.status==='running'||run.status==='queued'));
   if(index===current)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');
  });
  if(state.ticks.childElementCount!==total){
   state.ticks.replaceChildren(...Array.from({length:total},(_,i)=>{const cell=el('span',undefined,'prep-tick');cell.setAttribute('aria-hidden','true');return cell}));
  }
  Array.from(state.ticks.children).forEach((cell,i)=>{
   const validated=i<done,broken=i>=done&&i<processed;
   cell.classList.toggle('is-done',validated);cell.classList.toggle('is-failed',broken);
   cell.classList.toggle('is-active',i===processed&&(run.status==='running'||run.status==='queued'));
  });
  /* n16: одно число готовых блоков. Было два сразу — «3 / 5 блоков» строкой
     экрана и «Часть 5 из 5» в панели при заливке 60 %; «Часть 5» считалась по
     обработанным (готовые + упавший), а заливка — по готовым, и числа спорили
     друг с другом. Теперь и полоса, и строка говорят про ГОТОВОЕ. */
  const bits=[];
  if(total)bits.push('Готово '+done+' из '+total);
  if(failed)bits.push('не вышло: '+failed);
  state.meta.textContent=bits.join(' · ');
  /* n16: один счётчик времени и только вниз. Было два: часы 1:36 → 1:37
     (прошло) и «осталось ≈3:00» рядом. Остаётся убывающий остаток. */
  syncClock(state,run,running);
  const ready=Boolean(run.playable)&&running;
  if(ready){
   state.phase.textContent='Можно играть';
   state.meta.textContent='готово '+done+' из '+total+', остальное готовится';
   state.root.classList.add('is-playable');
  }else state.root.classList.remove('is-playable');
  /* Подпись «Сейчас» относится только к живой фразе: в остановленной полосе
     её нет, иначе она висела бы над пустой строкой. */
  state.now.hidden=!running;
  state.root.classList.toggle('is-stopped',!running);
  /* Отмена имеет смысл, только пока идёт прогон. */
  state.cancel.hidden=!running;
  if(!running)state.cancel.textContent='Отменить подготовку';
  if(!state.rotor&&running)startRotor(state);
  if(!running)stopRotor(state);
  renderFailures(state,run);
 }

 function startRotor(state){
  if(still()){state.caption.textContent=(FLAVOR[state.phase]||FLAVOR.extracting)[0];return}
  const step=()=>{
   const lines=(FLAVOR[state.phase]||FLAVOR.extracting).slice();
   const line=state.tick%3===2?AMBIENT[(state.tick/3|0)%AMBIENT.length]:lines[(state.tick)%lines.length];
   state.caption.classList.remove('is-in');state.caption.textContent=line;
   requestAnimationFrame(()=>state.caption.classList.add('is-in'));
   state.tick++;
  };
  step();state.rotor=setInterval(step,2600);
 }
 function stopRotor(state){if(state.rotor){clearInterval(state.rotor);state.rotor=null}}
 /* n16: часы — остаток, а не «прошло». Убывание считается локально между
    опросами, а серверный `eta_s` только укорачивает его: оценка не имеет права
    отскакивать вверх на каждом опросе, иначе счётчик читается как растущий. */
 function syncClock(state,run,running){
  const eta=Number(run&&run.progress&&run.progress.eta_s);
  if(!running||!Number.isFinite(eta)||eta<=0){state.clock.hidden=true;return;}
  const value=Math.max(0,Math.round(eta));
  state.remaining=state.remaining===null?value:Math.min(state.remaining,value);
  state.clock.textContent='осталось '+fmt(state.remaining);state.clock.hidden=false;
 }
 function startClock(state,run){
  if(state.timer)return;
  syncClock(state,run,true);
  state.timer=setInterval(()=>{
   if(state.remaining===null){state.clock.hidden=true;return;}
   state.remaining=Math.max(0,state.remaining-1);
   state.clock.textContent='осталось '+fmt(state.remaining);
  },1000);
 }
 function dispose(state){
  stopRotor(state);
  if(state.timer)clearInterval(state.timer);
  if(state.exit)clearTimeout(state.exit);
  state.timer=null;state.exit=null;
  state.root.remove();panels.delete(state.host);
 }
 /* Конец подготовки: числа берутся из последнего ответа сервера, а не
    додумываются, — «3 из 3» и «не вышло: 1» должны быть правдой. */
 function finish(state,run){
  state.finished=true;
  paint(state,run);
  const progress=run.progress||{};
  const done=Math.max(0,Number(progress.done??run.done??0)),total=Math.max(0,Number(progress.total??run.total??0));
  const failed=Math.max(0,Number(progress.failed||0));
  /* n16: итог пишется строкой фазы, а «Подготовка книги» остаётся заголовком
     экрана — иначе в кадре снова нет ни одного слова о том, где игрок. */
  state.phase.textContent=failed?'Часть блоков не вышла':(total&&done>=total?'Части собраны':'Подготовка остановлена');
  /* r246: после завершения под полосой остаётся цена разбора одной строкой —
     «128 блоков, N вызовов, X токенов на вход». Валюты нет: тарифы провайдера
     меняются, а число вызовов и токенов остаётся фактом. */
  state.caption.textContent=run.usage&&run.usage.text?run.usage.text:'';
  state.caption.classList.remove('is-in');
  state.root.classList.add('is-finished');state.root.classList.remove('is-stopped');
  /* Прошло время — не второй счётчик: часов на остановленной полосе нет. */
  state.clock.hidden=true;
  if(state.timer)clearInterval(state.timer);state.timer=null;
  if(state.exit){clearTimeout(state.exit);state.exit=null}
  /* r241: список причин не исчезает через шесть секунд. Раньше панель гасла
     сразу после конца, и «часть блоков не вышла» оставалось без ответа на
     вопрос «какие и почему». Успешная подготовка гаснет как раньше. */
  const blocks=run.retry&&Array.isArray(run.retry.blocks)?run.retry.blocks.length:0;
  if(!blocks)state.exit=setTimeout(()=>dispose(state),FINISH_MS);
 }

 window.soloPrepPanel=function(host,run,file){
  if(!host)return;
  const failures=run&&run.retry&&Array.isArray(run.retry.blocks)?run.retry.blocks.length:0;
  const issues=run&&Array.isArray(run.canonical_issues)?run.canonical_issues.length:0;
  /* r239: build() возвращает состояние полосы, но прежняя строка звала
     startClock с ещё не присвоенным `state` (undefined) и падала на первом
     же кадре. После перезагрузки страницы посреди подготовки это оставляло
     полосу пустой, а панель мира — недорисованной: таймер опроса, который
     ставится ниже по стеку, не создавался вовсе, и «Можно играть» не
     появлялось никогда. */
  let state=panels.get(host),running=Boolean(run&&['queued','running'].includes(run.status));
  if(!running&&!failures&&!issues){
   /* Завершённую подготовку задним числом не показываем: иначе при открытии
      старого черновика полоса висела бы над готовым приключением. Отказы —
      исключение: их список и кнопка «добить» и есть польза этой панели. */
   if(!state||state.finished)return;
   if(!run)dispose(state);else finish(state,run);
   return;
  }
  if(!state){state=build(host);panels.set(host,state);if(running)startClock(state,run)}
  else if(state.finished){state.finished=false;if(state.exit){clearTimeout(state.exit);state.exit=null}state.root.classList.remove('is-finished');state.remaining=null}
  /* n16: имя файла приходит с экрана — панель не ходит за ним сама. */
  if(file!==undefined){state.fileName=String(file||'');state.file.textContent=state.fileName;state.file.hidden=!state.fileName;}
  if(running)paint(state,run);else finish(state,run);
 };
})();

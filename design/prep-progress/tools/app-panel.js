(()=>{
 'use strict';
 /* Полоса подготовки книги: живой прогресс с фразами, как в загрузке The Sims.
    Владелец видит не «7 / 9 блоков», а что именно сейчас происходит.

    r234: панель переодета в язык «Стол и лист» — лист на столе, корешок
    состояния, линейка между волосяными линиями, засечки-стежки, медная
    плашка сбоя. Разметка и числа сверены с макетом design/prep-progress/
    (там же SPEC.md и HANDOFF.md). Логика r237 сохранена: «можно играть» не
    прячет полосу, конец держится 6 с, числа берутся из последнего ответа. */
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
 /* Засечек не больше 48: при 120 блоках одна засечка — три блока. Иначе ряд
    становится сплошной линией и перестаёт что-либо значить. */
 const MAX_TICKS=48;
 const fmt=seconds=>{const s=Math.max(0,Math.round(Number(seconds)||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
 const mk=(tag,text,cls)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node};
 const el=(tag,text,cls)=>mk(tag,text,cls);
 const still=()=>window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const plural=(n,one,few,many)=>{const d=n%10,h=n%100;if(d===1&&h!==11)return one;if(d>=2&&d<=4&&(h<10||h>=20))return few;return many};
 /* Числа в панели везде цифрами: «2 блока не разобрались», «12 частей из 12».
    Слово согласуется с числом, чтобы строка не звучала как машинный перевод. */
 const alertLine=(failed,over)=>{
  const verb=failed%10===1&&failed%100!==11?'не разобрался':'не разобрались';
  const head=failed+' '+plural(failed,'блок','блока','блоков')+' '+verb;
  return head+(over?'. Остальные части собраны.':' — остальные идут дальше.');
 };
 /* Числа собираются узлами: каждый кусок не рвётся внутри себя, перенос —
    только между кусками. Одинаковый текст не переписываем: иначе aria-live
    читает одно и то же на каждом опросе. */
 function setMeta(node,parts){
  const text=parts.join(' · ');
  if(node.dataset.text===text)return;
  node.dataset.text=text;
  node.replaceChildren(...parts.flatMap((part,index)=>index?[el('span','·','sep'),el('span',part)]:[el('span',part)]));
 }
 let panels=new WeakMap();

 function build(host){
  const root=el('div',undefined,'prep-panel');root.setAttribute('role','group');root.setAttribute('aria-label','Ход подготовки приключения');
  const head=el('div',undefined,'prep-head');
  const eyebrow=el('p','Подготовка книги','prep-eyebrow');
  const clock=el('p','0:00','prep-clock');
  const title=el('p','Подготовка','prep-title');
  head.append(eyebrow,clock,title);
  const steps=el('p',undefined,'prep-steps');steps.setAttribute('aria-label','Шаги подготовки');
  PHASE_ORDER.forEach((phase,index)=>{
   if(index)steps.append(Object.assign(el('span','→','prep-arrow'),{'aria-hidden':'true'}));
   const step=el('span',PHASE_STEP[phase],'prep-step');step.dataset.phase=phase;steps.append(step);
  });
  const bar=el('div',undefined,'prep-bar');bar.setAttribute('role','progressbar');bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');
  const fill=el('div',undefined,'prep-fill');bar.append(fill);
  const ticks=el('div',undefined,'prep-ticks');ticks.setAttribute('aria-hidden','true');
  /* Фраза меняется каждые 2,6 с — в очередь чтеца она не идёт. */
  const caption=el('p','','prep-caption');caption.setAttribute('aria-live','off');
  const meta=el('p','','prep-meta');meta.setAttribute('aria-live','polite');
  const alert=el('p','','prep-alert');alert.hidden=true;
  root.append(head,steps,bar,ticks,caption,meta,alert);host.append(root);
  const state={host,root,title,clock,steps,bar,fill,ticks,caption,meta,alert,started:Date.now(),phase:'',tick:0,timer:null,rotor:null,exit:null,finished:false,lastDone:-1};
  return state;
 }

 function paint(state,run){
  const progress=run.progress||{},phase=PHASE_ORDER.includes(progress.phase)?progress.phase:'extracting';
  const done=Math.max(0,Number(progress.done??run.done??0)),total=Math.max(0,Number(progress.total??run.total??0));
  const failed=Math.max(0,Number(progress.failed||0));
  /* Готовые и упавшие блоки — разные засечки. Упавшая засечка красится
     красным сразу, даже когда готовых ещё нет: раньше она была невидима, пока
     `done` не перевалит за неё, а потом красной становилась ГОТОВАЯ. */
  const processed=Math.min(total,done+failed);
  const running=run.status==='running'||run.status==='queued';
  state.phase=phase;
  state.title.textContent=PHASE_TITLE[phase]||'Разбираю книгу';
  const percent=total?Math.min(100,Math.round(done/total*100)):0;
  state.fill.style.width=percent+'%';
  state.root.toggleAttribute('data-empty',percent===0);
  state.bar.setAttribute('aria-valuenow',String(percent));
  state.bar.setAttribute('aria-valuetext',done+' из '+total+' частей');
  const current=PHASE_ORDER.indexOf(phase);
  Array.from(state.steps.querySelectorAll('.prep-step')).forEach((step,index)=>{
   step.classList.toggle('is-done',index<current||(index===current&&!running));
   step.classList.toggle('is-current',index===current&&running);
   if(index===current)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');
  });
  const unit=total>MAX_TICKS?Math.ceil(total/MAX_TICKS):1,cells=total?Math.ceil(total/unit):0;
  if(state.ticks.childElementCount!==cells){
   state.ticks.replaceChildren(...Array.from({length:cells},()=>el('span',undefined,'prep-tick')));
  }
  Array.from(state.ticks.children).forEach((cell,i)=>{
   const from=i*unit,to=Math.min(total,from+unit);
   /* Порядок: сбой → готово → сейчас. Упавшая засечка не бывает готовой. */
   const broken=Math.min(to,processed)>Math.max(from,done);
   cell.classList.toggle('is-failed',broken);
   cell.classList.toggle('is-done',!broken&&to<=done);
   cell.classList.toggle('is-active',!broken&&running&&from<=processed&&processed<to);
  });
  const bits=[];
  if(total)bits.push('Часть '+Math.min(processed+1,total)+' из '+total);
  if(progress.eta_s!==null&&progress.eta_s!==undefined&&done>0&&processed<total)bits.push('осталось ≈'+fmt(progress.eta_s));
  if(failed)bits.push('не вышло: '+failed);
  setMeta(state.meta,bits);
  state.alert.textContent=failed?alertLine(failed,false):'';
  state.alert.hidden=!failed;
  /* r237: глава 1 собрана — играть уже можно, а книга ещё читается. Пишем это
     словами и не прячем полосу: владелец видит и вход, и остаток работы. */
  if(Boolean(run.playable)&&running){
   state.title.textContent='Можно играть';
   setMeta(state.meta,['готово '+done+' из '+total+', остальное готовится']);
   state.root.classList.add('is-playable');
  }else state.root.classList.remove('is-playable');
  /* is-failed ставится всегда, когда есть сбои: CSS окрашивает медь только у
     `is-finished.is-failed`, поэтому во время работы корешок остаётся свечным. */
  state.root.classList.toggle('is-failed',Boolean(failed));
  state.root.classList.toggle('is-stopped',!running);
  if(!state.rotor&&running)startRotor(state);
  if(!running)stopRotor(state);
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
 function startClock(state,run){
  if(state.timer)return;
  const begin=Date.now()-(Number(run.progress?.elapsed_s||0)*1000);
  state.timer=setInterval(()=>{state.clock.textContent=fmt((Date.now()-begin)/1000)},1000);
  state.clock.textContent=fmt(Number(run.progress?.elapsed_s||0));
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
  state.title.textContent=failed?'Часть блоков не вышла':(total&&done>=total?'Части собраны':'Подготовка остановлена');
  const parts=[];
  if(total)parts.push(done+' '+plural(done,'часть','части','частей')+' из '+total);
  if(failed)parts.push('не вышло: '+failed);
  setMeta(state.meta,parts);
  state.alert.textContent=failed?alertLine(failed,true):'';
  state.alert.hidden=!failed;
  state.root.classList.toggle('is-failed',Boolean(failed));
  state.caption.textContent='';state.caption.classList.remove('is-in');
  state.root.classList.add('is-finished');state.root.classList.remove('is-stopped');
  if(state.timer)clearInterval(state.timer);state.timer=null;
  state.exit=setTimeout(()=>dispose(state),FINISH_MS);
 }

 window.soloPrepPanel=function(host,run){
  if(!host)return;
  let state=panels.get(host);const running=Boolean(run&&['queued','running'].includes(run.status));
  if(!running){
   /* Завершённую подготовку задним числом не показываем: иначе при открытии
      старого черновика полоса висела бы над готовым приключением. */
   if(!state||state.finished)return;
   if(!run)dispose(state);else finish(state,run);
   return;
  }
  /* r234: panels.set() возвращает Map, а не состояние — раньше первый вызов
     терял state и падал в startClock(); панель появлялась только со второго
     опроса. Состояние строим в переменную. */
  if(!state){state=build(host);panels.set(host,state);startClock(state,run)}
  else if(state.finished){state.finished=false;state.root.classList.remove('is-finished','is-failed');if(state.exit){clearTimeout(state.exit);state.exit=null}}
  paint(state,run);
 };
})();

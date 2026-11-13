/* r242 — «Мои миры»: мир уезжает файлом и поднимается без книги.
 *
 * Экран делает три вещи, и все три — про одно и то же состояние кампании:
 *  - «Сохранить в файл» — снимок мира открытой кампании в *.soloworld.json;
 *  - «Сохранить в библиотеку» — тот же снимок в runtime/worlds на сервере;
 *  - «Загрузить готовый мир» и «Играть» — новая кампания из пакета.
 *
 * Ни одно число этот файл не считает: имя, система, дата, размер и состав мира
 * приходят с сервера (solo/world_service.py). Книга в пакет не кладётся, и при
 * импорте модель не вызывается — это проверяет приёмка, а не текст на экране.
 *
 * Экран собирается сам: он не входит в index.html разметкой, чтобы у библиотеки
 * миров был один владелец — этот файл. Кнопка в меню встаёт рядом с «Кампании»,
 * где игрок и ищет свои миры.
 */
(()=>{
 'use strict';
 const SUFFIX='.soloworld.json',LIMIT=24*1048576;
 const mb=bytes=>bytes>=1048576?(bytes/1048576).toFixed(1)+' МБ':Math.max(1,Math.round(bytes/1024))+' КБ';
 const when=value=>{
  if(!value)return '';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?String(value):date.toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
 };
 const counts=value=>{
  const c=value||{};
  return [c.locations?'мест: '+c.locations:'',c.routes?'дорог: '+c.routes:'',c.npcs?'персонажей: '+c.npcs:''].filter(Boolean).join(' · ');
 };
 /* Сервер отвечает причиной словами; «Сервер ответил 500» игроку ничего не
    говорит, поэтому текст берётся из detail, а код — только запасной вариант. */
 async function failure(response){
  let reason='';
  try{
   const body=await response.json();
   reason=typeof body.detail==='string'?body.detail:(body.detail?.message||body.message||'');
  }catch(error){}
  return reason||('Сервер ответил '+response.status+'. Мир не загружен.');
 }

 /* ── Экран ──────────────────────────────────────────────────────────────── */
 const screen=el('section',undefined,'view');
 screen.id='worlds';
 screen.append(el('p','ГОТОВЫЕ МИРЫ','eyebrow'),el('h1','Мои миры'));
 const intro=el('p','Собранный мир уезжает файлом '+SUFFIX+': на другой машине он поднимается сразу — книга не разбирается заново, вызовов модели нет. В пакет едет структура мира: места, дороги, герой и механика; сама книга остаётся у вас.','hint');
 intro.id='worlds-intro';

 const pack=el('section',undefined,'worlds-block');
 pack.append(el('h2','Мир открытой кампании'));
 const packHint=el('p','','hint');packHint.id='worlds-pack-hint';
 pack.append(packHint);
 const packRow=el('div',undefined,'button-row');
 const saveFile=el('button','Сохранить мир в файл');saveFile.type='button';saveFile.id='world-save-file';
 const saveLibrary=el('button','Сохранить в библиотеку сервера');saveLibrary.type='button';saveLibrary.id='world-save-library';
 packRow.append(saveFile,saveLibrary);
 pack.append(packRow);

 const library=el('section',undefined,'worlds-block');
 library.append(el('h2','Библиотека миров на сервере'));
 library.append(el('p','Всё, что лежит в runtime/worlds. Мир открывается сразу: без книги, без ожидания разбора и без вызовов модели. Здесь же можно выбрать файл мира с другой машины.','hint'));
 const libraryRow=el('div',undefined,'button-row');
 const reload=el('button','Обновить список');reload.type='button';reload.id='worlds-reload';
 const upload=el('label','Загрузить готовый мир','file-button worlds-upload'),file=el('input');
 file.type='file';file.accept=SUFFIX+',application/json,.json';file.hidden=true;file.id='worlds-upload';
 upload.append(file);
 libraryRow.append(reload,upload);
 const status=el('p','','worlds-status');status.id='worlds-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 /* Загруженный файл сразу становится новой кампанией, но в библиотеку он не
    попадает: библиотека — это миры, которые положили на сервер кнопкой
    «Сохранить в библиотеку». Поэтому после загрузки здесь же стоит «Играть»:
    иначе игрок видит «мир загружен» и пустой список и не понимает, где игра. */
 const imported=el('div',undefined,'button-row');imported.id='worlds-imported';
 const list=el('div','','worlds-list');list.id='worlds-list';
 library.append(libraryRow,status,imported,list);
 screen.append(intro,pack,library);

 function say(text,bad){
  status.textContent=text;
  status.classList.toggle('is-bad',Boolean(bad));
 }
 function busy(value){
  for(const node of [saveFile,saveLibrary,reload])node.disabled=value;
  status.setAttribute('aria-busy',String(value));
 }

 /* ── Открыть мир кампанией ──────────────────────────────────────────────── */
 async function openCampaign(world){
  if(!world||!world.id)throw new Error('Сервер не вернул кампанию мира.');
  campaignId=world.id;
  try{localStorage.setItem('solo-campaign',world.id);}catch(error){}
  await refresh();
  view('play');
  toast(world.notice||('Мир «'+(world.name||'')+'» открыт.'));
 }

 /* ── Мир открытой кампании → файл и библиотека ──────────────────────────── */
 async function download(){
  if(!campaignId)throw new Error('Откройте кампанию: в файл сохраняется мир открытой кампании, а не пустая библиотека.');
  const response=await fetch('/api/campaigns/'+encodeURIComponent(campaignId)+'/world/export',{headers:{'Accept':'application/json'}});
  if(!response.ok)throw new Error(await failure(response));
  const disposition=response.headers.get('content-disposition')||'';
  const named=/filename="([^"]+)"/.exec(disposition);
  const name=named?named[1]:'world'+SUFFIX;
  const blob=await response.blob();
  const url=URL.createObjectURL(blob),link=el('a');
  link.href=url;link.download=name;link.hidden=true;document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),20000);
  return name+' · '+mb(blob.size);
 }
 saveFile.onclick=protect(async()=>{
  busy(true);say('Собираем мир в файл…');
  try{say('Файл скачан: '+await download());}
  catch(error){say(error.message,true);}
  finally{busy(false);}
 });
 saveLibrary.onclick=protect(async()=>{
  if(!campaignId)throw new Error('Откройте кампанию: в библиотеку сохраняется мир открытой кампании.');
  busy(true);say('Кладём мир в библиотеку сервера…');
  try{
   const saved=await api('/campaigns/'+campaignId+'/world/save',{});
   say('Мир сохранён: '+(saved.folder||'runtime/worlds')+'/'+saved.file+' · '+mb(saved.size)+' · '+(saved.system||''));
   await draw();
  }catch(error){say(error.message,true);}
  finally{busy(false);}
 });

 /* ── Библиотека: список, загрузка файла, игра ───────────────────────────── */
 function row(entry){
  const article=el('article',undefined,'world-row');
  if(entry.broken)article.classList.add('is-broken');
  const main=el('div',undefined,'world-row__main');
  const title=el('strong',entry.name||entry.file);
  const detail=entry.broken?('Файл не читается: '+(entry.problem||'причина не указана')):
   [entry.system||'система не указана',counts(entry.counts)].filter(Boolean).join(' · ');
  main.append(title,el('small',detail));
  const meta=el('div',undefined,'world-row__meta');
  meta.append(el('small',when(entry.created_at||(entry.saved?new Date(entry.saved*1000).toISOString():''))),el('small',mb(entry.size||0)));
  const actions=el('div',undefined,'button-row');
  const play=el('button','Играть','primary');play.type='button';play.id='world-play-'+entry.file;play.disabled=Boolean(entry.broken);
  play.onclick=protect(async()=>{
   play.disabled=true;say('Открываем мир «'+(entry.name||entry.file)+'»…');
   try{await openCampaign(await api('/worlds/'+encodeURIComponent(entry.file)+'/play',{}));say('Мир открыт.');}
   catch(error){say(error.message,true);play.disabled=false;}
  });
  const grab=el('button','Скачать');grab.type='button';
  grab.onclick=()=>{window.location.assign('/api/worlds/'+encodeURIComponent(entry.file)+'/download');};
  if(!entry.broken)actions.append(play,grab);
  article.append(main,meta,actions);
  return article;
 }
 async function draw(){
  say('Читаем библиотеку миров…');
  try{
   const data=await api('/worlds'),rows=data.worlds||[];
   list.replaceChildren();
   if(!rows.length)list.append(el('p','Пока пусто. Соберите мир и нажмите «Сохранить в библиотеку сервера» — или загрузите готовый файл '+SUFFIX+'.','hint'));
   for(const entry of rows)list.append(row(entry));
   say(rows.length?'Миров в библиотеке: '+rows.length:'');
  }catch(error){
   list.replaceChildren();say('Библиотека миров недоступна: '+error.message,true);
  }
 }
 reload.onclick=protect(async()=>{reload.disabled=true;try{await draw();}finally{reload.disabled=false;}});

 /* Файл читает браузер и отправляет текстом: сервер разбирает его тем же
    кодом, что и «Играть» из библиотеки, поэтому битый пакет объясняет причину
    одинаково на обоих путях. */
 file.onchange=protect(async()=>{
  const picked=file.files&&file.files[0];
  file.value='';
  if(!picked)return;
  if(!picked.name.toLowerCase().endsWith(SUFFIX))throw new Error('Это не пакет мира: нужен файл '+SUFFIX+'.');
  if(picked.size>LIMIT)throw new Error('Файл больше '+Math.round(LIMIT/1048576)+' МБ — это не пакет мира.');
  say('Загружаем мир из «'+picked.name+'»…');
  busy(true);
  try{
   const response=await fetch('/api/worlds/import',{method:'POST',headers:{'Content-Type':'application/json'},body:await picked.text()});
   if(!response.ok)throw new Error(await failure(response));
   const created=await response.json();
   await draw();
   say((created.notice||'Мир загружен.')+' Кампания: '+(created.name||''));
   const play=el('button','Играть в загруженный мир','primary');play.type='button';play.id='world-play-imported';
   play.onclick=protect(async()=>{
    play.disabled=true;
    try{await openCampaign(created);}
    catch(error){say(error.message,true);play.disabled=false;}
   });
   imported.replaceChildren(play);
  }catch(error){say(error.message,true);imported.replaceChildren();}
  finally{busy(false);}
 });

 /* Подпись к блоку кампании: у пустой библиотеки нет мира, который можно
    унести файлом, и обещать кнопку без кампании нельзя. */
 function paint(){
  const open=Boolean(campaignId)&&Boolean(current);
  packHint.textContent=open?('Кампания: '+(current.name||'')+'. Снимок мира уходит файлом '+SUFFIX+' и открывается на другой машине без книги.')
   :'Кампания не открыта. Загрузите мир из библиотеки ниже или откройте кампанию, чтобы сохранить её мир в файл.';
  for(const node of [saveFile,saveLibrary])node.disabled=!open;
 }

 /* ── Место в интерфейсе ─────────────────────────────────────────────────── */
 const campaigns=$('campaigns');
 if(campaigns)campaigns.after(screen);
 else document.querySelector('main').append(screen);
 const anchor=document.querySelector('.nav[data-view="campaigns"]');
 const nav=el('button',undefined,'nav');
 nav.dataset.view='worlds';nav.id='worlds-nav';
 nav.append(window.soloIcon?window.soloIcon('compass'):document.createTextNode('✧'),el('span','Мои миры','nav-text'));
 nav.onclick=()=>{view('worlds');protect(draw)();};
 if(anchor)anchor.after(nav);
 else document.querySelector('.sidebar nav').append(nav);

 document.addEventListener('solo:render',paint);
 paint();
 window.SoloWorlds={open:()=>{view('worlds');return protect(draw)();},refresh:()=>protect(draw)()};
})();

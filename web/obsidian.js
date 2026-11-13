'use strict';
(()=>{
 const icon=name=>{const i=el('img',undefined,'obsidian-icon solo-icon');i.src='/static/visual/icons/'+name+'.svg';i.alt='';i.setAttribute('aria-hidden','true');return i;};window.soloIcon=icon;
 // r223: тема переключается в play.js (ключ solo-theme, значения dark|light).
 // Пункт «Obsidian Chronicle» и миграция старых тем убраны: таких тем нет.
 const main=document.querySelector('main'),footer=main.querySelector('footer');
 function section(id,title){const p=el('section',undefined,'view');p.id=id;p.append(el('p','OBSIDIAN CHRONICLE','eyebrow'),el('h1',title));main.insertBefore(p,footer);return p;}
 // r222: первый экран — макет design/screens/01-start. Одна главная кнопка
 // «Начать историю», одна «Загрузить книгу», строка «продолжить» и роза ветров
 // из SVG. Картинок-заглушек нет: пока иллюстраций владельца нет, стол держат
 // токены design-tokens.css.
 const start=section('welcome','');
 const startCopy=el('div',undefined,'start__copy'),startArt=el('div',undefined,'start__art'),startMain=el('div',undefined,'start__main');
 startArt.setAttribute('aria-hidden','true');
 startArt.innerHTML='<svg viewBox="-200 -200 400 400" role="presentation">'
  +'<circle r="190" fill="none" stroke="currentColor" stroke-width="1" opacity=".20"/>'
  +'<circle r="164" fill="none" stroke="currentColor" stroke-width="1" stroke-dasharray="1 13.9" opacity=".28"/>'
  +'<circle r="120" fill="none" stroke="currentColor" stroke-width="1" opacity=".12"/>'
  +'<g fill="none" stroke="currentColor" stroke-width="1" opacity=".16"><path d="M0 -120 L0 -26 M0 26 L0 120 M-120 0 L-26 0 M26 0 L120 0"/><path d="M-86 -86 L-24 -24 M24 24 L86 86 M-86 86 L-24 24 M24 -24 L86 -86"/></g>'
  +'<g fill="currentColor"><g opacity=".92"><polygon points="0,-150 13,-16 0,0 -13,-16"/><polygon points="0,-150 13,-16 0,0 -13,-16" transform="rotate(90)"/><polygon points="0,-150 13,-16 0,0 -13,-16" transform="rotate(180)"/><polygon points="0,-150 13,-16 0,0 -13,-16" transform="rotate(270)"/></g>'
  +'<g opacity=".42" transform="rotate(45)"><polygon points="0,-104 9,-12 0,0 -9,-12"/><polygon points="0,-104 9,-12 0,0 -9,-12" transform="rotate(180)"/></g>'
  +'<g opacity=".42" transform="rotate(135)"><polygon points="0,-104 9,-12 0,0 -9,-12"/><polygon points="0,-104 9,-12 0,0 -9,-12" transform="rotate(180)"/></g></g>'
  +'<circle r="5" fill="currentColor" opacity=".9"/></svg>';
 const eyebrow=start.querySelector('.eyebrow'),heading=start.querySelector('h1');
 eyebrow.textContent='одиночное приключение';
 heading.className='start__title';
 heading.append(document.createTextNode('Тихая история'),el('br'),document.createTextNode('на целый вечер'));
 const startLead=el('p','Мастер помнит, что было вчера. Правила считает движок, слова — рассказчик.','start__lead');
 const startActions=el('div',undefined,'start__actions');
 const beginStory=el('button','Начать историю','start__btn start__btn--primary'),loadBook=el('button',undefined,'start__btn start__btn--ghost');
 beginStory.type=loadBook.type='button';
 beginStory.onclick=()=>window.soloOpenPreparation?window.soloOpenPreparation():openCreate();
 loadBook.append(icon('book'),document.createTextNode('Загрузить книгу'));
 loadBook.onclick=()=>view('adventure-library');
 startActions.append(beginStory,loadBook);
 const resume=el('button',undefined,'start__continue');resume.type='button';resume.hidden=true;
 // n04: прошлые истории — строки, а не карточки-плитки: на телефоне один столбец,
 // на столе не больше трёх в ряд (design-table.css). Список ограничен по высоте,
 // поэтому первый экран не растёт вместе с числом кампаний.
 // n14: одна секция вместо двух (замечание №9 критика): подпись «Продолжить»,
 // под ней последняя история, ниже — прошлые строки и строка «Все истории».
 // Две секции несли одинаковые метаданные и читались как два разных списка.
 const stories=el('div',undefined,'start__stories'),storyList=el('div',undefined,'start__list');
 stories.hidden=true;
 const storiesKicker=el('p','Продолжить','start__stories-kicker');
 const allStories=el('button','Все истории','start__all');allStories.type='button';
 allStories.onclick=()=>view('campaigns');allStories.hidden=true;
 stories.append(storiesKicker,resume,storyList,allStories);
 const empty=el('p','У вас пока нет историй. Начните с книги или загрузите свою','start__empty');empty.hidden=true;
 const startFoot=el('div',undefined,'start__foot'),localNote=el('p',undefined,'start__local');
 localNote.append(icon('save'),document.createTextNode('Сохранения и книги остаются на этом '+hereWord()));
 startFoot.append(localNote);
 // n04: истории живут НЕ в колонке текста. Внутри неё на столе оставалось 350 px,
 // и три истории сжимались до 101 px; теперь у блока своя ширина — 960.
 const feed=el('div',undefined,'start__feed');
 feed.append(stories,empty);
 startCopy.append(eyebrow,heading,startLead,startActions);
 startMain.append(startCopy,startArt);
 start.append(startMain,feed,startFoot);
 // «Продолжить» и прошлые истории. Данные — /api/chronicles: там уже есть имя
 // героя, уровень и место остановки, и это лёгкая проекция без ленты ходов.
 // В строке — место остановки и герой; уровень в неё не влезает вместе с датой
 // (замер 375: 343 px на 14 px — это ~45 знаков), а нужен он на экране игры.
 // n08: в мете — герой с уровнем и место остановки, без названия истории:
 // оно стоит строкой выше, и «Дозор на тракте · Тайна трёх сосен» читалось как
 // одно и то же, сказанное дважды (макет: «Мирон, 3-й уровень · вчера, 22:14»).
 // Уровень приходит из /chronicles (`level`), браузер его не считает.
 const placeOf=row=>{
  const hero=[row.hero||'',row.level?row.level+'-й уровень':''].filter(Boolean).join(', ');
  return [hero,row.scene||''].filter(Boolean).join(' · ');
 };
 // «вчера, 22:14» читается быстрее, чем дата; дальше — обычная дата.
 function whenText(ts){
  if(!ts)return '';
  const date=new Date(ts*1000),now=new Date(),pad=n=>String(n).padStart(2,'0');
  const same=(a,b)=>a.getFullYear()===b.getFullYear()&&a.getMonth()===b.getMonth()&&a.getDate()===b.getDate();
  const yesterday=new Date(now.getFullYear(),now.getMonth(),now.getDate()-1);
  if(same(date,now))return 'сегодня, '+pad(date.getHours())+':'+pad(date.getMinutes());
  if(same(date,yesterday))return 'вчера, '+pad(date.getHours())+':'+pad(date.getMinutes());
  return date.toLocaleDateString('ru-RU');
 }
 // Дата и «где остановились» — одна строка 14 px, но так, чтобы дата не
 // обрезалась: место остановки сжимается многоточием, дата остаётся целиком.
 function metaNode(row,cls){
  const line=el('span',undefined,cls);
  line.append(el('span',placeOf(row),'story-where'),el('span',whenText(row.updated||row.created),'story-when'));
  return line;
 }
 function openStory(row){
  return protect(async()=>{if(busy)throw new Error('Дождитесь завершения текущего ответа.');campaignId=row.id;localStorage.setItem('solo-campaign',row.id);await refresh();view('play');});
 }
 function storyRow(row){
  const button=el('button',undefined,'story');
  button.type='button';
  button.append(el('span',row.name,'story-title'),metaNode(row,'story-meta'));
  button.onclick=openStory(row);
  return button;
 }
 // Старый список кампаний остаётся запасным путём: без него отказ /chronicles
 // оставил бы главную без единой истории.
 async function readStories(){
  try{const data=await api('/chronicles');if(data&&Array.isArray(data.campaigns))return data.campaigns;}catch(error){}
  try{const list=await api('/campaigns');return (list||[]).map(row=>({...row,updated:row.created}));}catch(error){return [];}
 }
 async function loadStories(){
  const rows=(await readStories()).filter(row=>row.backend==='nat20');
  resume.hidden=true;stories.hidden=true;empty.hidden=true;allStories.hidden=true;
  resume.replaceChildren();storyList.replaceChildren();
  if(!rows.length){empty.hidden=false;return;}
  const recent=rows.find(row=>row.id===localStorage.getItem('solo-campaign'))||rows[0];
  // n14: подпись «Продолжить» одна — на секции (storiesKicker). Вторая, внутри
  // кнопки, дублировала её и вместе с «Прошлыми историями» давала два списка.
  resume.replaceChildren(el('span',recent.name,'start__continue-title'),metaNode(recent,'start__continue-meta'));
  resume.onclick=openStory(recent);
  resume.hidden=false;
  // n16: на главной — ОДНА карточка: последняя история. Было три строки сразу
  // (кнопка «Продолжить», строка истории с той же метой и «Все истории»), и две
  // первые не различались ничем, кроме рамки. Остальные истории живут на экране
  // «Мои приключения», и «Все истории» появляется, только когда их 2+.
  storyList.replaceChildren();
  storyList.hidden=true;
  allStories.hidden=rows.length<2;
  stories.hidden=false;
 }
 loadStories().catch(()=>{});
 const settings=section('settings','Настройки приложения'),tabs=el('div',undefined,'settings-tabs'),contents=el('div');settings.append(tabs,contents);const panes={};
 function settingsPane(id,title){const b=el('button',title),p=el('div',undefined,'settings-page');b.setAttribute('role','tab');b.setAttribute('aria-selected','false');b.onclick=()=>{for(const[key,value]of Object.entries(panes)){value.panel.hidden=key!==id;value.button.setAttribute('aria-selected',String(key===id));}};p.dataset.settingsPane=id;p.hidden=true;tabs.append(b);contents.append(p);panes[id]={panel:p,button:b};return p;}
 const general=settingsPane('general','Общие'),appearance=settingsPane('appearance','Внешний вид'),windowPane=settingsPane('window','Окно и экран'),controls=settingsPane('controls','Управление'),data=settingsPane('data','Данные и копии'),about=settingsPane('about','О программе');panes.general.button.click();
 function preference(container,label,key,kind,choices,defaultValue){const input=el(kind==='select'?'select':'input');if(kind==='select')for(const[value,text]of choices){const o=el('option',text);o.value=value;input.append(o);}else input.type=kind;
  const saved=localStorage.getItem(key);if(kind==='checkbox')input.checked=saved? saved==='on':defaultValue;else input.value=saved||defaultValue;
  input.onchange=()=>{localStorage.setItem(key,kind==='checkbox'?(input.checked?'on':'off'):input.value);document.documentElement.dataset.compact=localStorage.getItem('solo-compact')||'off';document.documentElement.dataset.cardSize=localStorage.getItem('solo-card-size')||'normal';};container.append(field(label,input));input.onchange();return input;
 }
 preference(general,'При запуске','solo-start-page','select',[['campaign','Последняя кампания'],['campaigns','Мои приключения'],['welcome','Стартовая страница']],'campaign');preference(general,'Запоминать последнюю кампанию','solo-remember-campaign','checkbox',[],true);general.append(el('p','Язык — русский. Подтверждённые действия сохраняются сразу. Закрытие окна останавливает незавершённое описание и сохраняет механический результат.','hint'));
 const themeNode=$('theme'),themeLabel=el('label','Тема интерфейса');themeLabel.append(themeNode);appearance.append(themeLabel,field('Мягкие переходы',$('effects')));preference(appearance,'Компактные панели','solo-compact','checkbox',[],false);preference(appearance,'Карточки предметов','solo-card-size','select',[['normal','Обычные'],['large','Крупные']],'normal');
 const look=document.querySelector('.shell-preferences');if(look)appearance.append(look);const moved=new WeakSet();function relocate(){for(const item of document.querySelectorAll('.shell-preferences'))if(!moved.has(item)){moved.add(item);if(item!==look)windowPane.append(item);}}
 relocate();new MutationObserver(relocate).observe(main,{childList:true,subtree:true});windowPane.append(el('p','Размер и полноэкранный режим доступны в Windows-приложении. Настройки сохраняются в пользовательском профиле.','hint'));controls.append(el('p','Enter — отправить реплику, Shift + Enter — новая строка. F11 — полноэкранный режим Windows. Tab / Shift + Tab — переход между элементами. Escape закрывает открытый диалог.'));
 const dataInfo=el('p',undefined,'hint'),backupList=el('div'),backupButton=el('button','Создать резервную копию'),refreshBackups=el('button','Обновить список копий'),exportButton=el('button','Экспорт данных');data.append(dataInfo,backupButton,refreshBackups,exportButton,backupList);
 async function loadBackups(){const result=await api('/data/backups');dataInfo.textContent='Профиль: '+result.profile+' · схема '+result.schema;backupList.replaceChildren();for(const b of result.backups){const card=el('div',undefined,'list-card'),preview=el('button','Проверить копию');card.append(el('span',b.name+' · '+b.created+(b.damaged?' · файл повреждён':'')+(b.broken_links?' · нарушено связей: '+b.broken_links:'')),preview);preview.onclick=protect(async()=>{const details=await api('/data/backups/'+encodeURIComponent(b.id)+'/preview');toast('Копия проверена: '+details.campaigns+' кампаний, схема '+details.schema+'.'+(details.warning?' '+details.warning+'.':''));if(window.soloDesktop){const restore=el('button','Восстановить при следующем запуске');restore.onclick=protect(async()=>{if(!confirm('Перед восстановлением будет создана новая копия текущего профиля. Приложение закроется; выбранные данные применятся при следующем запуске.'))return;await api('/desktop/restore',{backup:b.id,confirm:true});});card.append(restore);}});backupList.append(card);}if(!result.backups.length)backupList.append(el('p','Копий пока нет.','hint'));}
 backupButton.onclick=protect(async()=>{if(busy)throw new Error('Остановите ответ перед резервным копированием.');const answer=await api('/data/backup',{});await loadBackups();toast(answer&&answer.report_file?('Копия создана: '+answer.id+'. Замечания: '+answer.summary+'. Отчёт '+answer.report_file+' лежит рядом с копией.'):('Копия создана: '+answer.id+'. Замечаний нет.'));});refreshBackups.onclick=protect(loadBackups);
 // Экспорт через fetch: при отказе сервер называет причину и код, а файл приходит с отчётом внутри.
 exportButton.onclick=protect(async()=>{const response=await fetch('/api/data/export');
  if(!response.ok){const raw=await response.text();let body=null;try{body=JSON.parse(raw);}catch(parse){body=null;}
   const raw_reason=body&&body.detail;const reason=(typeof raw_reason==='string'?raw_reason:(raw_reason&&raw_reason.message))||('Не удалось скачать данные: сервер ответил кодом '+response.status);
   const code=(body&&body.code)||(raw_reason&&raw_reason.code);
   toast(code?reason+' Код: '+code+'.':reason);return;}
  const integrity=response.headers.get('X-Solo-Integrity')||'',blob=await response.blob(),link=el('a');
  link.href=URL.createObjectURL(blob);link.download='SoloRealmsAI-data.zip';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),10000);
  toast(/links=damaged/.test(integrity)?'Данные скачаны, но файл базы повреждён: в архиве отчёт integrity-report.json с причиной. Сохраните архив — данные из него ещё можно разобрать.':(/broken_links=[1-9]/.test(integrity)?'Данные скачаны. Внешние связи в базе нарушены: в архиве отчёт integrity-report.json с таблицей, rowid и родителем каждой записи. Записи не удалялись.':'Данные скачаны.'));});
 about.append(el('h2','Solo Realms AI — Obsidian Chronicle'),el('p','Рабочая Windows Preview. Полный SRD и автоматическое классовое развитие ещё не реализованы.'),el('p','Правила, память и подтверждённый журнал остаются источником игрового состояния. Ассеты отвечают за оформление.'));const licenses=el('div'),gallery=el('div',undefined,'asset-gallery');about.append(licenses,gallery);
 protect(async()=>{const h=await api('/health');about.prepend(el('p','Версия: '+h.version,'hint'));const assets=await api('/visual-assets');licenses.append(el('h3','Лицензии и атрибуция'),el('p','Реестр: '+assets.source_count+' источников · импортировано '+assets.assets.length+' ресурсов.'));const unique=new Set();for(const a of assets.assets){const label=a.author+' · '+a.license;if(!unique.has(label)){licenses.append(el('p',label,'license-record'));unique.add(label);}if(a.media==='svg'){const f=el('figure'),img=el('img');img.src='/static/'+a.thumbnail;img.alt=a.id;f.append(img,el('figcaption',a.id));gallery.append(f);}}const link=el('a','Полная атрибуция и сведения о преобразованиях');link.href=assets.attribution;link.target='_blank';licenses.append(link);await loadBackups();})();
 // Reuse the proven controls and callbacks in a hierarchical shell.
 const nav=document.querySelector('.sidebar nav'),campaignGroup=el('details',undefined,'nav-group campaign-nav'),campaignMenu=el('div',undefined,'nav-submenu');campaignGroup.open=true;const title=el('summary');title.append(icon('book'),el('span','Текущая кампания'));campaignGroup.append(title,campaignMenu);
 const originals=[...nav.querySelectorAll('button[data-view]')];nav.replaceChildren();function navButton(label,name,asset,fn){const b=el('button',undefined,'nav');b.dataset.view=name;b.append(icon(asset),el('span',label,'nav-text'));b.onclick=fn||(()=>view(name));return b;}
 const campaigns=originals.find(b=>b.dataset.view==='campaigns');campaigns.replaceChildren(icon('chronicle'),el('span','Мои приключения','nav-text'));nav.append(campaigns,campaignGroup);
 const labels={play:['Игровая сцена','book'],character:['Персонаж','person'],inventory:['Инвентарь','inventory'],world:['Карта мира','map'],journal:['Блокнот','journal'],saves:['Сохранения','save'],master:['Параметры кампании','feather']};for(const[name,[label,asset]]of Object.entries(labels)){const b=originals.find(b=>b.dataset.view===name);if(b){b.replaceChildren(icon(asset),el('span',label,'nav-text'));campaignMenu.append(b);}}
 const rules=originals.find(b=>b.dataset.view==='rulesets');if(rules){rules.replaceChildren(icon('book'),el('span','Правила','nav-text'));nav.append(rules);campaignMenu.append(navButton('Правила кампании','rulesets','book',()=>rules.click()));}
 const library=section('master-library','Профили Мастера'),libraryBody=el('div',undefined,'library-grid');library.append(el('p','Профили хранятся в общей библиотеке. Выбор профиля для кампании доступен в её параметрах.','hint'),libraryBody);nav.append(navButton('Профили Мастера','master-library','feather',protect(async()=>{view('master-library');await loadProfiles();libraryBody.replaceChildren();for(const p of profiles){const c=el('div',undefined,'page-card');c.append(el('h3',p.name),el('p',p.master.style||'Профиль литературной подачи','hint'));const edit=el('button','Открыть редактор');edit.onclick=()=>{profileId=p.id;profileName.value=p.name;profileJSON.value=JSON.stringify(p.master,null,2);profileEditor.open=true;};c.append(edit);libraryBody.append(c);}})));
 let profileId=null;const profileEditor=el('details',undefined,'disclosure'),profileName=el('input'),profileJSON=el('textarea'),saveProfile=el('button','Сохранить профиль в библиотеке'),copyProfile=el('button','Создать копию профиля'),deleteProfile=el('button','Удалить профиль из библиотеки');profileEditor.append(el('summary','Редактор профиля'),field('Название профиля',profileName),field('Инструкции и литературные параметры JSON',profileJSON),el('p','Литературные инструкции не меняют механику. Профиль сохраняется отдельно от кампаний.','hint'),saveProfile,copyProfile,deleteProfile);profileJSON.rows=18;profileJSON.maxLength=40000;library.append(profileEditor);
 saveProfile.onclick=protect(async()=>{if(!profileId)throw new Error('Откройте профиль.');await api('/master/profiles/'+profileId,{name:profileName.value,master:JSON.parse(profileJSON.value)},'PUT');await loadProfiles();toast('Профиль сохранён в библиотеке.');});copyProfile.onclick=protect(async()=>{const p=await api('/master/profiles',{name:profileName.value+' — копия',master:JSON.parse(profileJSON.value)});profileId=p.id;profileName.value+=' — копия';await loadProfiles();toast('Копия создана.');});deleteProfile.onclick=protect(async()=>{if(!profileId||!confirm('Удалить профиль из библиотеки? Кампании сохранят свои параметры.'))return;await api('/master/profiles/'+profileId,undefined,'DELETE');profileId=null;profileEditor.open=false;await loadProfiles();toast('Профиль удалён из библиотеки.');});
 document.querySelector('.side-bottom').replaceChildren(el('p','OBSIDIAN CHRONICLE'),el('small','Ваши хроники хранятся локально'));const settingsNav=navButton('Настройки','settings','settings');const technical=el('details',undefined,'nav-group'),techMenu=el('div',undefined,'nav-submenu');technical.append(el('summary','Настройки'),techMenu);techMenu.append(settingsNav);for(const name of['cloud','costs','performance']){const b=originals.find(b=>b.dataset.view===name);if(b){/* n13: подписи — словами игрока: «Нейросети и API» → «Подключение Мастера», «Расходы API» → «Расходы», «Производительность» → «Скорость». n18: предмет в подписи назван — «Расходы на Мастера», «Скорость ответа» (замечание №5 с кадра n16). */const label={cloud:'Подключение Мастера',costs:'Расходы на Мастера',performance:'Скорость ответа'}[name];b.replaceChildren(icon(name==='costs'?'coins':name==='cloud'?'wizard':'heart'),el('span',label,'nav-text'));techMenu.append(b);}}const aboutNav=originals.find(b=>b.dataset.view==='about');
 // r221: колофон живёт отдельным экраном и не прячется в «Настройках».
 if(aboutNav){aboutNav.replaceChildren(icon('book'),el('span','О проекте','nav-text'));nav.append(aboutNav);}
nav.append(technical);
 // n18: «Выход из игры» спрашивает подтверждение. Прежний обработчик одним
 // нажатием прерывал поток и обнулял кампанию (`campaignId=null`, экран
 // «Продолжить») — на телефоне строка стоит вплотную к «Сохранениям», палец
 // попадал в неё мимо (замечание №4 с кадра n16). Отмена не трогает ни
 // кампанию, ни поток. «Прогресс сохранён» — правда: каждый ход пишется в
 // журнал до ответа Мастера, вернуться можно кнопкой «Продолжить».
 const exitDialog=el('dialog',undefined,'exit-confirm');exitDialog.id='exit-confirm';
 const exitForm=el('form');exitForm.method='dialog';exitForm.id='exit-confirm-form';
 const exitYes=el('button','Выйти','primary'),exitNo=el('button','Отмена');
 exitYes.type=exitNo.type='button';exitYes.id='exit-confirm-yes';exitNo.id='exit-confirm-no';
 const exitActions=el('div',undefined,'button-row');exitActions.append(exitYes,exitNo);
 exitForm.append(el('p','ВЫХОД ИЗ ИГРЫ','eyebrow'),el('h2','Выйти из кампании?'),el('p','Прогресс сохранён. Вернуться можно кнопкой «Продолжить» на главной.','hint'),exitActions);
 exitDialog.append(exitForm);document.body.append(exitDialog);
 function leaveGame(){streamController?.abort();if(window.soloDesktop)return api('/desktop/exit',{});campaignId=null;current=null;document.body.classList.remove('campaign-open');view('welcome');}
 exitNo.onclick=()=>exitDialog.close('cancel');
 exitYes.onclick=protect(async()=>{exitDialog.close('exit');await leaveGame();});
 const exit=navButton('Выход из игры','exit','exit',protect(async()=>{if(exitDialog.open){exitDialog.close();return;}exitDialog.showModal();}));exit.classList.add('nav-exit');nav.append(exit);
 const baseView=view;view=function(name){if(['play','character','inventory','world','journal','saves','master'].includes(name)&&!current){baseView('welcome');return;}baseView(name);if(name==='settings')protect(loadBackups)();};
 let startupApplied=!!current;function context(c){document.body.classList.add('campaign-open');title.lastChild.textContent=c.name;if(!startupApplied){startupApplied=true;if(localStorage.getItem('solo-start-page')==='campaign')view('play');}}
 document.addEventListener('solo:render',e=>context(e.detail));if(current)context(current);
 const startup=localStorage.getItem('solo-start-page');if(!current)view(startup==='campaigns'?'campaigns':'welcome');else if(startup!=='campaign')view(startup||'welcome');
 for(const note of main.querySelectorAll(':scope > .subtext'))$('play').append(note);$('model-status').title='Ключ и провайдер Мастера настраиваются в меню «Ещё» → «Подключение Мастера».';
})();

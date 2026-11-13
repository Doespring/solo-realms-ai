/* Ordinary preparation uses the same bounded source importer as the library. */
(()=>{
 'use strict';
 window.soloOpenLegacyPreparation=window.soloOpenPreparation;
 const choosePreparedModule=window.soloChooseAdventure;
 const page=el('section',undefined,'view');page.id='creation';page.append(el('p','ВАША НОВАЯ ИСТОРИЯ','eyebrow'),el('h1','Новое приключение'));
 const controls=el('div',undefined,'creation-controls'),back=el('button','Назад'),save=el('button','Сохранить и выйти'),mine=el('button','Мои приключения');controls.append(back,save,mine);
 const drop=el('div',undefined,'creation-drop'),choose=el('button','Выбрать файлы'),file=el('input'),list=el('div',undefined,'creation-files');
 file.type='file';file.multiple=true;file.accept='.pdf,.zip,.txt,.md,.json,.png,.jpg,.jpeg,.webp';file.hidden=true;file.id='creation-files';
 drop.tabIndex=0;drop.setAttribute('aria-label','Перетащите сюда материалы приключения');drop.append(el('h2','Материалы приключения'),el('p','Перетащите PDF, ZIP, текст или изображения. Можно выбрать несколько файлов.','hint'),choose,file);
 const description=el('textarea');description.id='creation-description';description.rows=6;description.maxLength=12000;description.placeholder='Например: проведи приключение по приложенной книге. Или: хочу мрачную историю на севере о пропавшем караване.';
 const prepare=el('button','Подготовить приключение','primary'),cancel=el('button','Отменить загрузку'),status=el('p','','hint');status.setAttribute('role','status');cancel.hidden=true;
 // n13: ряд призыва держим ссылкой — по `page.querySelector('.button-row')` в
 // странице есть и второй такой ряд (кнопки мира), и приписка тестовой сборки
 // вставала не туда.
 const actionRow=el('div',undefined,'button-row creation-dock');
 const stopPreparation=el('button','Отменить подготовку'),resumePreparation=el('button','Продолжить незавершённые блоки');stopPreparation.hidden=resumePreparation.hidden=true;let retryRun=null;
 const readiness=el('p','','hint'),errorHistory=el('details',undefined,'disclosure'),errorRows=el('div');readiness.id='creation-readiness';readiness.setAttribute('role','status');readiness.hidden=true;status.removeAttribute('role');status.setAttribute('aria-live','polite');errorHistory.append(el('summary','Сохранённая история ошибок подготовки'),errorRows);errorHistory.hidden=true;
 const characterChoices=el('section',undefined,'character-entry'),textCharacter=el('button','Создать героя по описанию','primary'),visualCharacter=el('button','Создать героя в конструкторе');characterChoices.hidden=true;characterChoices.append(el('h2','Кто отправится в путь?'),textCharacter,visualCharacter);
 // n13: порядок как в макете `new-375` — поле описания первым блоком после
 // заголовка, материалы (`.creation-drop`, 203 px) ниже, призыв «Подготовить
 // приключение» в первом кадре. Было: drop → поле, и на 375 поле начиналось
 // на y≈765 из 812, а кнопки в кадре не было вовсе (замечание №1 критика).
 // n16: порядок первого кадра — поле описания → призыв → материалы; служебные
 // кнопки («Назад», «Сохранить и выйти», «Мои приключения») уходят ПОД материалы
 // и больше не липнут к верху. Было: drop → поле, призыв на y=965 при кадре
 // 812, а над полем стояли три служебные кнопки (блокер №1 критика n16).
 page.append(field('Каким будет ваше приключение?',description),actionRow,drop,list,controls,readiness,status,errorHistory);actionRow.append(prepare,resumePreparation,cancel,stopPreparation);$('campaigns').after(page);
 page.append(characterChoices);// One gameplay path: both entries lead to the single Native hero wizard, and the
// same screen keeps working for a draft that already holds a hero there. The older
// screen is only reached explicitly, by name, for drafts that own a legacy hero.
 const handoffOf=()=>{const value=launchHandoff||draft?.handoff||null;return value&&value.draft_id===draft?.id?value:null;};
 // A03/F01: герой из подготовки Мастера входит в запуск тем же черновиком.
 // Проверенная глава книги — законный старт без подготовки мира; без главы и без
 // мира экран не открывает пустоту, а называет, чего не хватает.
 const openSingleHero=async()=>{
  await persist();
  const rows=await api('/chronicles');
  const row=(rows.drafts||[]).find(item=>item.id===draft.id);
  const passage=handoffOf();
  // A half-written legacy hero kept its old editor; it must not be launched that way.
  if(row&&row.character_mode&&!row.native&&!(world&&world.ready)){await window.soloOpenCharacter(draft.id,row.character_mode);return;}
  if(!world||!world.ready){
   if(!(passage&&passage.book&&passage.book.ready)){
    throw new Error('Сначала завершите проверку мира: герой выбирается для подготовленного приключения. Проверенной главы книги тоже нет, поэтому старт по книге недоступен.');
   }
   await window.soloWizard.open({preparationDraft:draft.id,heroDraft:passage.hero_draft,equipment:passage.equipment,equipmentItems:passage.equipment_items,module_id:passage.module_id,book:passage.book});
   return;
  }
  await window.soloOpenPreparedNat20({draft_id:draft.id,revision:world.revision,run_id:world.run_id,
   hero_draft:passage&&passage.hero_draft,equipment:passage&&passage.equipment,equipment_items:passage&&passage.equipment_items,module_id:passage&&passage.module_id});
 };
 textCharacter.onclick=protect(openSingleHero);visualCharacter.onclick=protect(openSingleHero);
 const progress=el('p','','hint task-status');progress.id='creation-progress';progress.setAttribute('role','status');progress.setAttribute('aria-live','polite');progress.setAttribute('aria-atomic','true');progress.hidden=true;/* n16: полоса подготовки идёт сразу за призывом, а не за материалами: иначе
   после нажатия «Подготовить приключение» игрок не видел ни заголовка
   «Подготовка книги», ни «Отменить подготовку» без прокрутки на два экрана. */actionRow.after(progress);const progressPanel=el('div');progressPanel.id='creation-progress-panel';progress.after(progressPanel);
// A03/F01: что именно пришло из подготовки Мастера — на экране, а не в догадке.
const handoffNote=el('div');handoffNote.id='creation-handoff';handoffNote.hidden=true;progressPanel.after(handoffNote);
 const worldPanel=el('section',undefined,'page-card'),worldMessage=el('p','','hint'),worldLimits=el('div'),worldPrepare=el('button','Подготовить игровой мир'),worldConfirm=el('button','Подтвердить проверенный мир'),worldHero=el('button','Выбрать героя Nat20','primary'),worldRecheck=el('button','Проверить мир заново','quiet'),bookHero=el('button','Выбрать героя Nat20 и начать по книге','primary');
 worldPanel.id='creation-world';worldPanel.hidden=true;worldMessage.setAttribute('role','status');const worldButtons=el('div',undefined,'button-row');worldButtons.append(worldPrepare,worldConfirm,worldRecheck,worldHero,bookHero);worldPanel.append(el('h2','Игровой мир из материалов'),el('p','Мир, локации и знания персонажей готовятся отдельно от механики. Старый список имён и частично обработанная книга не означают готовности мира. Для игры вы отдельно выберете правила D&D 2024.','hint'),worldMessage,worldLimits,worldButtons);page.append(worldPanel);bookHero.onclick=protect(openSingleHero);
 const worldDialog=el('dialog'),worldConfirmText=el('p'),acceptWorld=el('button','Подтвердить этот мир','primary'),cancelWorld=el('button','Вернуться');worldDialog.id='world-confirm';worldDialog.append(el('h2','Подтверждение игрового мира'),worldConfirmText,el('p','Это локальное подтверждение. Героя и правила игры вы выберете следующим шагом. Исходные таблицы, эффекты и противники автоматически не переносятся.','hint'),cancelWorld,acceptWorld);document.body.append(worldDialog);
 const mockNotice=el('section',undefined,'page-card'),mockExplanation='В этой тестовой сборке нет подключённого ИИ: он не готовит новые приключения. Материалы и описание можно сохранить в черновике. Для игры откройте готовое испытание: кампанию создаёт мастер Nat20.';
 const trial=el('button','Открыть готовое испытание'),mockLinks=el('div',undefined,'button-row');
 mockNotice.id='creation-mock-notice';mockNotice.hidden=true;mockLinks.append(trial);mockNotice.append(el('p',mockExplanation),mockLinks);/* n13: тестовая приписка — после призыва, а не перед полем описания: она занимала первые 150 px кадра. */
 progressPanel.after(mockNotice);/* n16: приписка идёт после полосы подготовки — панель стоит вплотную к призыву. */
 trial.type='button';trial.onclick=protect(async()=>{await persist();$('new-nat20-campaign').click();});
 let draft=null,dirty=false,timer=null,poll=null,queue=Promise.resolve(),xhr=null,uploading=false,mockPreparation=false,checking=false,openingConfirmation=false,world=null,worldBusy=false,worldError='',worldSelection=null,worldRequest=0,opening=false,launchHandoff=null;
 /* n16: имя файла, по которому идёт подготовка, — панель называет его в шапке:
   без него экран подготовки отвечал «Подготовка», но не говорил, что готовит. */
 function prepFileName(){const rows=(draft?.files||[]).map(row=>row.name).filter(Boolean);return rows.length>1?rows.join(', '):(rows[0]||'');}
 function drawProgress(){const run=draft?.preparation,imports=draft?.files.filter(row=>['queued','running'].includes(row.job?.status))||[],activeRun=['queued','running'].includes(run?.status);/* n16: «Подготовка: 3 / 5 блоков» больше не дублирует полосу — число готовых блоков одно, и живёт оно в панели подготовки (у неё же и часы). */let text=opening?'Открываем материалы…':uploading?'Загружаем файл…':sendingPreparation?'Запускаем подготовку…':openingConfirmation?'Проверяем параметры подготовки…':checking?'Проверяем готовность без API…':worldBusy?'Проверяем или подтверждаем игровой мир…':imports.length?'Извлечение: '+imports.map(row=>row.name+(row.job.total?' · '+row.job.done+' / '+row.job.total:'')).join(' · '):'';progress.textContent=text;progress.hidden=!text;progress.classList.toggle('is-loading',Boolean(text));page.setAttribute('aria-busy',String(Boolean(text)||activeRun));if(window.soloPrepPanel)window.soloPrepPanel(progressPanel,(activeRun||Boolean(run?.retry?.blocks?.length))?run:null,prepFileName());}
 const sameWorld=value=>Boolean(value&&world?.world_ready&&draft&&!dirty&&value.draft_id===draft.id&&value.revision===draft.revision&&value.run_id===world.run_id);
 function blockerRows(world){const groups=world&&world.blocked_groups;if(!groups||!Array.isArray(groups.shown)||!groups.shown.length)return (world&&world.blocked_operations)||[];const rows=groups.shown.map(row=>({reason:row.count+' × '+row.label+' — '+row.reason+(row.detail?' ('+row.detail+')':'')}));if(groups.total)rows.push({reason:'Всего записей: '+groups.total+'; причин: '+groups.groups+'.'+(groups.hidden?' Показаны первые '+groups.shown.length+' причин.':'')});return rows;}
function drawWorld(locked=false,playable=false){worldPanel.hidden=!window.soloPrototypeEnabled;if(worldPanel.hidden)return;characterChoices.hidden=true;const current=world&&world.revision===draft?.revision&&!dirty;/* n00: «мир можно использовать» и «мир прошёл канон» — разные вещи. Глава собрана, старт проверен: экран говорит об этом словами игрока и даёт войти, а не молчит, пока канон не сойдётся. */const partial=Boolean(current&&world.world_ready&&world.partial_usable);worldMessage.textContent=worldError||(current?(world.ready?'Мир подтверждён. Выберите героя и правила игры.':partial?(world.reasons||[]).join(' ')||'Глава собрана, можно начинать.':world.world_ready?'Структура мира проверена. Требуется ваше подтверждение.':(world.reasons||[]).join(' ')||'Мир пока не готов.'):'Проверьте готовность актуального мира без API.');worldLimits.replaceChildren(...(current?blockerRows(world):[]).map(row=>el('p',row.reason||String(row),'hint')));/* r237: глава 1 собрана («Можно играть») — остальная книга доготавливается в фоне и не запирает вход. */const startLock=Boolean(locked&&!playable);worldPrepare.disabled=Boolean(locked||worldBusy||mockPreparation);/* n13: кнопка карточки мира дублировала призыв «Подготовить приключение» (тот же путь подготовки мира). */worldPrepare.hidden=Boolean(window.soloPrototypeEnabled&&!isWorldRun());worldConfirm.textContent=partial?'Начать с этой главой':'Подтвердить проверенный мир';worldConfirm.hidden=!(current&&world.world_ready&&!world.ready);worldRecheck.hidden=!(partial&&!world.ready);worldRecheck.disabled=Boolean(worldBusy);worldConfirm.disabled=Boolean(startLock||worldBusy);worldHero.hidden=!(current&&world.ready);worldHero.disabled=Boolean(startLock||worldBusy);acceptWorld.disabled=worldBusy||!sameWorld(worldSelection);/* A03/F01: проверенная глава книги — законный старт без подготовки мира; кнопка появляется только когда эта глава действительно есть. */const passage=handoffOf();bookHero.hidden=!(passage&&passage.book&&passage.book.ready)||Boolean(world&&world.ready);bookHero.disabled=Boolean(startLock||worldBusy);}
 async function refreshWorld(){if(!window.soloPrototypeEnabled||!draft)return;const id=draft.id,revision=draft.revision,request=++worldRequest;try{const value=await api('/creation/drafts/'+id+'/world-readiness');if(request!==worldRequest||draft?.id!==id||draft.revision!==revision)return;world=value;worldError='';}catch(error){if(request!==worldRequest||draft?.id!==id)return;world=null;worldError='Не удалось проверить игровой мир: '+error.message;}drawPreparation();}
 const localRun=run=>Boolean(run&&run.status!=='stale');
 const resumable=run=>localRun(run)&&['partial','blocked','failed','interrupted','cancelled'].includes(run.status)&&(Number(run.total)>Number(run.done)||Boolean(run.retry?.assembly));
 const runMode=run=>['world_structure','source_extract'].includes(run?.preparation_mode)?run.preparation_mode:'unknown';
 const isWorldRun=()=>runMode(draft?.preparation)==='world_structure';
 function clearReadiness(){readiness.textContent='';readiness.hidden=true;world=null;worldError='';worldRequest++;}
 async function checkPreparationMode(){const health=await api('/health');mockPreparation=health.llm?.mock===true;mockNotice.hidden=!mockPreparation;drawFiles();return mockPreparation;}
 const labels={queued:'Ожидает извлечения',running:'Извлекаем материалы',complete:'Материалы извлечены',failed:'Не удалось извлечь. Можно повторить или убрать файл.',cancelled:'Импорт остановлен'};
 function merge(value){if(draft?.id===value.id&&value.revision<draft.revision)return;const text=description.value;draft=value;if(dirty)draft.description=text;else description.value=value.description;characterChoices.hidden=value.status!=='ready'||dirty;drawFiles();}
 function local(){if(draft)localStorage.setItem('solo-creation-local',JSON.stringify({id:draft.id,description:description.value,revision:draft.revision}));}
 function persist(){clearTimeout(timer);queue=queue.catch(()=>{}).then(async()=>{if(!draft||!dirty)return;const id=draft.id,text=description.value;local();const value=await api('/creation/drafts/'+id,{description:text,revision:draft.revision},'PUT');if(draft?.id!==id)return;dirty=description.value!==text;merge(value);if(dirty)local();else localStorage.removeItem('solo-creation-local');status.textContent='Черновик сохранён.';});return queue;}
 description.oninput=()=>{dirty=true;clearReadiness();characterChoices.hidden=true;drawWorld();local();clearTimeout(timer);timer=setTimeout(()=>persist().catch(e=>status.textContent='Не сохранено: '+e.message),700);};
 function drawFiles(){list.replaceChildren();if(!draft)return;for(const source of draft.files){const row=el('article',undefined,'creation-file'),job=source.job;row.append(el('strong',source.name),el('small',(source.bytes/1048576).toFixed(1)+' МиБ · '+(labels[job?.status]||'Не обработан')));if(job?.status==='failed'&&job.message)row.append(el('small',job.message,'creation-file-reason'));if(job?.total)row.append(el('small',job.done+' / '+job.total));const remove=el('button','Убрать файл');remove.disabled=uploading;remove.onclick=protect(async()=>{await persist();merge(await api('/creation/drafts/'+draft.id+'/files/'+source.document,undefined,'DELETE'));});row.append(remove);if(['failed','cancelled'].includes(job?.status)){const retry=el('button','Повторить');retry.onclick=protect(async()=>{await persist();merge(await api('/creation/drafts/'+draft.id+'/files/'+source.document+'/retry',{}));});row.append(retry);}if(['queued','running'].includes(job?.status)){const stop=el('button','Остановить импорт');stop.onclick=protect(async()=>{await api('/adventure/jobs/'+job.id+'/cancel',{});await update();});row.append(stop);}list.append(row);}drawPreparation();}
 function drawPreparation(){const run=draft?.preparation,active=run&&['queued','running'].includes(run.status),locked=uploading||checking||openingConfirmation||beginPreparation.disabled||confirmation.open||active||draft?.files.some(s=>s.job&&['queued','running'].includes(s.job.status));
  drawProgress();
  prepare.textContent=localRun(run)?'Проверить готовность (без API)':'Подготовить приключение';prepare.disabled=Boolean(locked||(mockPreparation&&!localRun(run)));
  // n13: призыв «Подготовить приключение» — единственный и в первом кадре.
  // Было: в сборке с прототипом он прятался ВСЕГДА (`soloPrototypeEnabled &&
  // (!localRun || isWorldRun)`), а призыв жил внизу, в карточке «Игровой мир
  // из материалов», — на 375 его не было в кадре вовсе (замечание №1 критика).
  // Оставляем его скрытым только там, где состоянием владеет сама карточка
  // мира (идёт подготовка структуры мира).
  prepare.hidden=Boolean(isWorldRun());
  /* n16: «Отменить подготовку» — ОДНО действие экрана. Панель подготовки рисует
     свою кнопку отмены в шапке (она же рядом с именем файла и часами), поэтому
     кнопка ряда призыва при живой панели прячется: было две одинаковых на одном
     экране. Без панели (скрипт не загрузился) кнопка ряда остаётся. */
  resumePreparation.hidden=!resumable(run);resumePreparation.disabled=Boolean(locked||mockPreparation);stopPreparation.hidden=!active||Boolean(window.soloPrepPanel);
  // r238: не разобравшиеся блоки называются числом, а кнопка честно говорит,
  // что именно повторит: «добить эти три», а не «продолжить всё заново».
  const broken=run?.retry?.blocks?.length||0;const assembly=Boolean(run?.retry?.assembly);retryRun=run&&(broken||assembly)?run.id:null;
  resumePreparation.textContent=broken?'Добить '+broken+' '+(broken===1?'блок':broken<5?'блока':'блоков'):assembly?'Собрать мир из готовых блоков':'Продолжить незавершённые блоки';
  const errors=[...(run?.errors||[]).map(e=>'Блок '+e.block+', '+e.field+': '+e.message),...(run?.canonical_issues||[]).map(e=>e.count+' × '+e.reason)];
  errorRows.replaceChildren(...errors.map(text=>el('p',text,'hint')));errorHistory.hidden=!errors.length;
  /* r246: цена прогона — той же строкой, что и итог подготовки: числа уже
     посчитаны сервером по леджеру расходов, экран их не додумывает.
     n16: счётчика блоков здесь больше нет. «Готово N из M» живёт только в панели
     подготовки; дубль строкой экрана давал два числа на одном кадре и «3 / 5»
     рядом с «Готово 3 из 5». */
  if(run)status.textContent=run.message+(run.usage&&run.usage.text?' · '+run.usage.text:'');else if(draft?.files.length&&draft.files.every(f=>f.job?.status==='complete'))status.textContent='Материалы извлечены. Можно подготовить приключение.';
  drawWorld(locked,Boolean(run?.playable));drawHandoff();
 }
 async function update(){await queue;if(!draft||uploading||dirty)return;const id=draft.id,value=await api('/creation/drafts/'+id);if(draft?.id===id){merge(value);await refreshWorld();}}
 async function open(id=null,handoff=null){if(uploading)throw new Error('Завершите или отмените текущую загрузку, затем откройте другой черновик.');await persist();clearInterval(poll);clearReadiness();
 // A03/F01: запуск открывает ТОТ ЖЕ черновик, который закончил мастер. Если он
 // ещё не дозаполнен, экран называет недостающее и ведёт обратно в шаг, а не
 // показывает пустое «Новое приключение».
 let value;try{value=id?await api('/creation/drafts/'+id):await api('/creation/drafts',{});}catch(error){if(!id)throw error;showIncomplete(id,error);return;}
 draft=value;launchHandoff=handoff||value.handoff||null;
 dirty=false;description.value=draft.description;let pending;try{pending=JSON.parse(localStorage.getItem('solo-creation-local'));}catch{}if(pending?.id===draft.id){description.value=pending.description;dirty=true;}localStorage.setItem('solo-creation-draft',draft.id);view('creation');characterChoices.hidden=draft.status!=='ready'||dirty;drawHandoff();await checkPreparationMode();await refreshWorld();status.textContent='Файлы и описание сохраняются на этом '+hereWord()+'.';poll=setInterval(()=>update().catch(()=>{}),1200);}
function drawHandoff(){const value=launchHandoff;if(!value||!draft||value.draft_id!==draft.id){handoffNote.hidden=true;return;}const hero=value.hero_draft||{},items=value.equipment_items||[],named=String(hero.name||'').trim(),start=String(value.start||'').trim();/* n16: в тексте для игрока — только то, что он сам выбирал. UUID черновика
   (36 знаков), «книга без отдельного модуля», «Старт: «не назван» · предметов: 0»
   убраны: это внутренние слова, а не события истории. Номер черновика — короткий
   и в «Подробностях», куда игрок не обязан смотреть. */
 const facts=[];facts.push(named?'Герой: '+named:'Герой ещё не назван');if(start)facts.push('Старт: «'+start+'»');if(items.length)facts.push('Предметов: '+items.length);
 const details=el('details',undefined,'disclosure');details.append(el('summary','Подробности'),el('p','Номер черновика: '+String(draft.id||'').slice(0,8),'hint'));
 handoffNote.hidden=false;handoffNote.replaceChildren(el('h3','Продолжаем подготовленное приключение'),el('p',facts.join(' · '),'hint'),el('p',value.book&&value.book.ready?'Книга разрезана на проверенную главу: старт по книге откроется на её первой странице.':'Проверенной главы нет: чтобы начать по книге, подготовьте мир из материалов.','hint'),details);}
function showIncomplete(id,error){draft=null;launchHandoff=null;clearInterval(poll);view('creation');characterChoices.hidden=true;worldPanel.hidden=true;status.textContent='';readiness.hidden=true;dirty=false;description.value='';handoffNote.hidden=false;const back=el('button','Вернуться в подготовку');back.type='button';back.onclick=protect(()=>window.soloResumePreparation?window.soloResumePreparation(id):window.soloOpenLegacyPreparation());handoffNote.replaceChildren(el('h3','Этот черновик ещё не готов к игре'),el('p',error.message),back);localStorage.setItem('solo-creation-draft',id);}
 function uploadOne(selected){return new Promise((resolve,reject)=>{xhr=new XMLHttpRequest();xhr.open('POST','/api/creation/drafts/'+draft.id+'/files');xhr.upload.onprogress=e=>{status.textContent='Загрузка «'+selected.name+'»'+(e.lengthComputable?' · '+Math.round(e.loaded/e.total*100)+'%':'');};xhr.onload=()=>{try{const value=JSON.parse(xhr.responseText);if(xhr.status>=400)throw new Error(value.detail||'Не удалось загрузить файл');resolve(value);}catch(e){reject(e);}};xhr.onerror=()=>reject(new Error('Связь прервана. Файл можно загрузить повторно.'));xhr.onabort=()=>reject(new Error('Загрузка отменена. Остальные материалы сохранены.'));const data=new FormData();data.append('file',selected);xhr.send(data);});}
 async function upload(files){if(uploading)return;await persist();uploading=true;cancel.hidden=false;choose.disabled=true;try{for(const selected of files){if(selected.size>384*1048576)throw new Error('«'+selected.name+'» превышает предел 384 МиБ');if(draft.files.length>=20)throw new Error('Можно выбрать до 20 исходных файлов');merge(await uploadOne(selected));}status.textContent='Файлы сохранены. Извлечение продолжается локально.';}finally{uploading=false;xhr=null;cancel.hidden=true;choose.disabled=false;file.value='';drawFiles();}}
 choose.onclick=()=>file.click();file.onchange=protect(()=>upload([...file.files]));cancel.onclick=()=>xhr?.abort();
 for(const name of ['dragenter','dragover'])drop.addEventListener(name,e=>{e.preventDefault();drop.classList.add('dragging');});for(const name of ['dragleave','drop'])drop.addEventListener(name,e=>{e.preventDefault();drop.classList.remove('dragging');});drop.addEventListener('drop',protect(e=>upload([...e.dataTransfer.files])));
 async function leave(){if(xhr)xhr.abort();await persist();clearInterval(poll);view('campaigns');await renderDrafts();}
 back.onclick=save.onclick=mine.onclick=protect(leave);
 const confirmation=el('dialog'),edition=el('select'),beginPreparation=el('button','Подготовить через API','primary'),dismissPreparation=el('button','Вернуться');confirmation.id='preparation-confirm';
 for(const[value,label]of [['','Выберите редакцию'],['dnd2014','D&D 2014'],['dnd2024','D&D 2024']]){const option=el('option',label);option.value=value;edition.append(option);}
 const worldConsent=el('p','Это отдельная подготовка полной структуры мира. Старые списки имён не заменяют её; новый режим может потребовать новых платных запросов. Сохранённые блоки не удаляются. Редакция источника не определяет правила будущего героя.','hint');worldConsent.hidden=true;
 const modeChoice=el('select'),modeField=field('Как продолжить подготовку?',modeChoice);modeField.hidden=true;modeChoice.setAttribute('aria-label','Как продолжить подготовку?');for(const [value,label] of [['','Выберите режим явно'],['source_extract','Прежний режим: список имён'],['world_structure','Полная структура игрового мира']]){const option=el('option',label);option.value=value;modeChoice.append(option);}modeChoice.onchange=()=>{if(sendingPreparation)return;worldConsent.hidden=modeChoice.value!=='world_structure';beginPreparation.disabled=!modeChoice.value;};
 confirmation.append(el('h2','Какие правила использовать?'),field('Редакция нового приключения',edition),modeField,el('p','Выбранный текст и описание будут отправлены настроенному AI-провайдеру. Запрос выполняется по тарифу и в пределах лимитов из настроек. Оригинальные кампании не меняются.','hint'),worldConsent,dismissPreparation,beginPreparation);document.body.append(confirmation);
 let confirmationDraft=null,confirmationRevision=null,confirmationRun=null,confirmationResume=false,preparingWorld=false,sendingPreparation=false;
 async function confirmPreparation(resume=false,structure=false){if(checking||openingConfirmation||confirmation.open)return;openingConfirmation=true;drawFiles();try{await persist();const id=draft.id,value=await api('/creation/drafts/'+id);if(draft?.id!==id)return;merge(value);if(resume&&!resumable(value.preparation)){status.textContent='Незавершённых блоков для продолжения нет. Проверьте готовность без API.';return;}if(await checkPreparationMode()){status.textContent=mockExplanation;return;}
  const mode=resume?runMode(value.preparation):structure?'world_structure':'source_extract';preparingWorld=mode==='world_structure';confirmationResume=resume;confirmationRun=resume?{id:value.preparation.id,mode,source_ruleset:value.preparation.source_ruleset}:null;
  modeField.hidden=mode!=='unknown';modeChoice.value=mode==='unknown'?'':mode;beginPreparation.disabled=mode==='unknown';worldConsent.hidden=!preparingWorld;
  confirmation.querySelector('h2').textContent=mode==='unknown'?'Режим старой подготовки неизвестен':preparingWorld?'Подготовить игровой мир из источника?':'Какие правила использовать?';confirmation.querySelector('label').firstChild.textContent=preparingWorld||resume?'Редакция исходных материалов':'Редакция нового приключения';
  const source=value.preparation?.source_ruleset||world?.source_ruleset;edition.value=['dnd2014','dnd2024'].includes(source)?source:'';edition.disabled=Boolean(edition.value&&(resume||structure));confirmationDraft=id;confirmationRevision=value.revision;confirmation.showModal();
 }finally{openingConfirmation=false;drawFiles();}}
 dismissPreparation.onclick=()=>confirmation.close();confirmation.addEventListener('close',()=>{confirmationDraft=null;beginPreparation.disabled=false;drawFiles();});
 prepare.onclick=protect(async()=>{if(checking||openingConfirmation||confirmation.open)return;await persist();const id=draft.id,value=await api('/creation/drafts/'+id);if(draft?.id!==id)return;merge(value);/* n13: в сборке с прототипом приключение готовится как структура мира — тот же путь, что у кнопки карточки мира; иначе кнопка вела бы в снятый режим списка имён. */if(!localRun(value.preparation)){await confirmPreparation(false,Boolean(window.soloPrototypeEnabled));return;}
  checking=true;drawFiles();readiness.hidden=false;readiness.textContent='Проверяем готовность локально, без API…';
  try{const checked=await api('/creation/drafts/'+id+'/readiness',{});if(draft?.id!==id)return;readiness.textContent=checked.message;try{await update();}catch(error){status.textContent='Не удалось обновить историю подготовки: '+error.message;}}
  catch(error){if(draft?.id===id)readiness.textContent='Не удалось проверить готовность: '+error.message;}
  finally{checking=false;drawFiles();}
 });
 resumePreparation.onclick=protect(async()=>{if(retryRun){await api('/creation/runs/'+retryRun+'/retry',{});await update();return;}return confirmPreparation(true,isWorldRun());});
 // r241: «Добить» живёт в самой полосе подготовки — панель сообщает событием,
 // экран обновляет черновик по своему обычному опросу.
 window.addEventListener('solo-prep-retry',()=>{update().catch(()=>{})});
 beginPreparation.onclick=protect(async()=>{if(sendingPreparation||beginPreparation.disabled||!confirmation.open||confirmationDraft!==draft?.id)return;const mode=modeChoice.value,ruleset=edition.value,editionDisabled=edition.disabled;sendingPreparation=true;modeChoice.disabled=true;edition.disabled=true;beginPreparation.disabled=true;drawFiles();const id=confirmationDraft;try{if(await checkPreparationMode()){confirmation.close();status.textContent=mockExplanation;return;}if(!['world_structure','source_extract'].includes(mode))throw new Error('Выберите режим подготовки явно.');if(!ruleset)throw new Error('Выберите редакцию исходных материалов');const current=await api('/creation/drafts/'+id);if(current.revision!==confirmationRevision)throw new Error('Черновик изменился. Закройте окно и проверьте актуальные материалы.');if(confirmationResume){const run=current.preparation;if(run?.id!==confirmationRun.id||runMode(run)!==confirmationRun.mode||run.source_ruleset!==confirmationRun.source_ruleset||!resumable(run))throw new Error('Подготовка изменилась. Закройте окно и выберите актуальное продолжение.');}const run=await api('/creation/drafts/'+id+'/prepare',{request_id:crypto.randomUUID(),ruleset,consent_to_send:true,world_structure:mode==='world_structure'});confirmation.close();if(draft?.id!==id)return;clearReadiness();status.textContent=run.message||'Подготовка запущена.';await update();}finally{sendingPreparation=false;modeChoice.disabled=false;edition.disabled=editionDisabled;beginPreparation.disabled=false;drawFiles();}});
 // The world is validated automatically after preparation, after a restore and before a campaign is created, so the player reads the result instead of pressing a diagnostic button.
 worldPrepare.onclick=protect(()=>confirmPreparation(false,true));
 worldConfirm.onclick=()=>{if(worldBusy||!world?.world_ready||world.revision!==draft.revision||dirty)return;worldSelection={draft_id:draft.id,revision:world.revision,run_id:world.run_id};worldConfirmText.textContent='Подтверждаю проверенную структуру и открытый старт. Редакция источника: '+(world.source_ruleset||'не указана')+'.';drawPreparation();worldDialog.showModal();};
 cancelWorld.onclick=()=>worldDialog.close();worldDialog.addEventListener('close',()=>{worldSelection=null;});
 acceptWorld.onclick=async()=>{if(worldBusy||!sameWorld(worldSelection))return;const selected={...worldSelection};worldBusy=true;drawPreparation();try{const current=await api('/creation/drafts/'+selected.draft_id+'/world-readiness');if(!current.world_ready||current.revision!==selected.revision||current.run_id!==selected.run_id||!sameWorld(selected))throw new Error('Мир изменился. Проверьте его заново перед подтверждением.');await api('/creation/drafts/'+selected.draft_id+'/confirm-world',{expected_revision:selected.revision,run_id:selected.run_id,confirm:true});worldDialog.close();await update();}catch(error){worldDialog.close();world=null;worldError='Мир не подтверждён: '+error.message;}finally{worldBusy=false;drawPreparation();}};
 /* Вторичная ссылка: та же локальная проверка, без вызовов модели. */worldRecheck.onclick=protect(()=>refreshWorld());
 worldHero.onclick=protect(async()=>{if(worldBusy||dirty||!world?.ready||world.revision!==draft.revision)return;const passage=handoffOf();await window.soloOpenPreparedNat20({draft_id:draft.id,revision:world.revision,run_id:world.run_id,hero_draft:passage&&passage.hero_draft,equipment:passage&&passage.equipment,equipment_items:passage&&passage.equipment_items,module_id:passage&&passage.module_id});});
 stopPreparation.onclick=protect(async()=>{if(draft?.preparation)await api('/creation/runs/'+draft.preparation.id+'/cancel',{});await update();});
 const drafts=el('div');drafts.id='creation-drafts';$('campaigns').append(drafts);
 async function renderDrafts(){if(window.soloUnifiedChronicles)return;const rows=await api('/creation/drafts');drafts.replaceChildren();const unfinished=rows.filter(d=>!d.campaign);if(unfinished.length)drafts.append(el('h2','Незавершённые приключения'));for(const d of unfinished){const card=el('article',undefined,'page-card'),resume=el('button','Продолжить');card.append(el('h3',d.name),el('p',(d.status==='importing'?'Извлечение материалов':'Черновик')+' · '+d.files.length+' файлов'),el('small',new Date(d.updated*1000).toLocaleString('ru-RU')),resume);resume.onclick=protect(()=>open(d.id));drafts.append(card);}}
 const legacyResume=$('new-campaign').nextElementSibling;if(legacyResume?.textContent==='Незавершённые приключения')$('tabletop-advanced').append(legacyResume);

 window.soloOpenPreparation=protect(()=>open());window.soloOpenCreation=protect(open);$('new-campaign').textContent='Новое приключение';$('new-campaign').onclick=window.soloOpenPreparation;if($('begin')){$('begin').textContent='Новое приключение';$('begin').onclick=window.soloOpenPreparation;}
  // A prepared adventure continues in the single Native wizard. Its world is
  // read again from the server here because a library card is a stale snapshot:
  // confirming a world the draft no longer has would start a hero in a world
  // the player never approved.
  window.soloOpenPreparedWorkflow=protect(async row=>{
    const id=row.draft_id||row.id,readiness=await api('/creation/drafts/'+id+'/world-readiness');
    if(!readiness.ready&&!readiness.world_ready){
      // A03/F01: проверенная глава книги — законный вход без подготовленного мира.
      // Карточка книги открывает тот же черновик в запуске, где уже названы герой,
      // старт и предметы Мастера, а кнопка старта по книге ведёт в редактор героя.
      const passage=await api('/creation/drafts/'+id+'/handoff').catch(()=>null);
      if(!passage||!passage.book||!passage.book.ready)throw new Error('Мир этого приключения ещё не подтверждён. Откройте подготовку и завершите проверку.');
      await open(id,passage);return;
    }
    await window.soloOpenPreparedNat20({draft_id:id,revision:readiness.revision,run_id:readiness.run_id});
  });
 window.soloChooseAdventure=async (bundle,module=null)=>{if(module)return choosePreparedModule(bundle,module);const selected=await api('/creation/from-library/'+bundle,{});await open(selected.id);};
 const prior=view;view=function(name){if(page.classList.contains('active')&&name!=='creation'){clearInterval(poll);persist().catch(e=>toast(e.message));}prior(name);if(name==='campaigns')protect(renderDrafts)();};
})();

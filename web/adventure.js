'use strict';
(()=>{
 const library=el('section',undefined,'view');library.id='adventure-library';library.append(el('p','ВАША БИБЛИОТЕКА','eyebrow'),el('h1','Приключения и материалы'));
 const body=el('div');library.append(body);$('campaigns').after(library);
 const nav=el('button',undefined,'nav');nav.dataset.view=library.id;nav.append(window.soloIcon('book'),el('span','Библиотека приключений','nav-text'));document.querySelector('[data-view="master-library"]').before(nav);
 let selected=null,reviewData=null,reviewMode=false;
 const statuses={catalog_only:'Нет локальных материалов',import_incomplete:'Импорт не завершён',documents_imported:'Материалы извлечены · инструкции требуют проверки',requires_review:'Требуется проверка сценария',instructions_reviewed:'Инструкции проверены'};
 const button=(label,fn,cls='')=>{const b=el('button',label,cls);b.type='button';b.onclick=protect(fn);return b;};
 // F11: одно состояние книги на всех экранах. Карточка, комплект и подготовка
 // печатают один и тот же агрегат: шаги, счётчики одной ревизии и два разных
 // «готово». Число модулей нигде не считается на клиенте — только приходит с
 // сервера, иначе на одном экране снова окажутся 12 и 11.
 const stepStates={done:'готово',current:'сейчас',pending:'ждёт'};
 const actionLabels={fix:'Исправить',retry:'Повторить',details:'Подробности'};
 function countsLine(counts){const parts=['Источники: '+counts.documents,'Страницы: '+counts.pages,'Эпизоды: '+counts.episodes];
  if(counts.appendices)parts.push('Приложения: '+counts.appendices);
  return parts.join(' · ');}
 function readinessPanel(readiness,options={}){
  const full=Boolean(options.full),book=options.id||'';
  const box=el('section',undefined,'readiness'),head=el('div',undefined,'readiness-head');
  head.append(el('p','СОСТОЯНИЕ КНИГИ','eyebrow'),el('p',readiness.headline,'readiness-headline'),
   el('p',countsLine(readiness.counts)+' · ревизия '+readiness.revision,'hint'));
  box.append(head);
  const steps=el('ol',undefined,'readiness-steps');
  for(const step of readiness.steps||[]){const line=el('li',undefined,'readiness-step');line.dataset.state=step.state;
   line.append(el('span',stepStates[step.state]||step.state,'readiness-step__state'),el('span',step.title,'readiness-step__title'),
    el('span',step.detail,'readiness-step__detail'));steps.append(line);}
  box.append(steps);
  const flags=el('p',undefined,'readiness-flags');
  flags.append(el('span',readiness.start_ready?'Старт готов':'Старт не готов','flag'+(readiness.start_ready?' flag--ok':' flag--no')),
   el('span',readiness.book_ready?'Вся книга готова':'Вся книга не готова','flag'+(readiness.book_ready?' flag--ok':' flag--no')),
   el('span',readiness.instructions_checked?'Строгие инструкции проверены':'Строгие инструкции ждут проверки','flag'));
  if(readiness.start)flags.append(el('span','Старт: «'+(readiness.start.title||readiness.start.id)+'», стр. '+readiness.start.page,'hint'));
  box.append(flags);
  if(full&&readiness.warnings?.length){const list=el('ul',undefined,'readiness-warnings');
   for(const warning of readiness.warnings){const line=el('li',undefined,'warning warning--'+(warning.severity||'info')),wrap=el('span',undefined,'warning__body');
    wrap.append(el('span',warning.text,'warning__text'));
    // У каждого предупреждения есть действие: без него игрок не знает, что делать.
    line.append(wrap,button(actionLabels[warning.action]||'Подробности',()=>runWarning(warning,book)));list.append(line);}
   box.append(list);}
  return box;
 }
 function openSection(label){const found=[...body.querySelectorAll('details')].find(row=>row.querySelector('summary')?.textContent.includes(label));if(!found)return false;found.open=true;found.scrollIntoView({block:'center'});return true;}
 function runWarning(warning,book){
  if(warning.action==='retry'){const input=body.querySelector('input[type=file]');if(input){input.click();toast('Выберите тот же файл: оригинал сохранён, повторная загрузка начнёт импорт заново.');return;}toast('Загрузите книгу заново на экране комплекта.');return;}
  if(warning.code==='level_requirement'||warning.code==='instructions_missing'||warning.code==='module_unsupported'||warning.code==='boundaries_unchecked'){if(openSection('Проверенные инструкции'))return;}
  if(warning.code==='appendix'||warning.code==='blank_pages'){if(openSection('Документы и качество'))return;}
  if(['modules_missing','scenes_missing','start_invalid'].includes(warning.code)){const sheet=body.querySelector('.booksheet');if(sheet){sheet.scrollIntoView({block:'start'});return;}if(openSection('Разделить сборник'))return;}
  if(openSection('Проверенные инструкции'))return;
  toast('Подробности — в разделах ниже: «Разбор книги», «Документы и качество», «Проверенные инструкции».');
 }
 window.soloOpenAdventureLibrary=()=>protect(showLibrary)();nav.onclick=window.soloOpenAdventureLibrary;
 async function showLibrary(){
  if(reviewMode){await api('/adventure/review/session',undefined,'DELETE');reviewMode=false;}
  view(library.id);body.replaceChildren(el('p','Книги и изображения хранятся локально. Импорт не делает сценарий автоматически готовым к прохождению.','hint'));
  const create=el('form',undefined,'inline-form'),name=el('input');name.placeholder='Название комплекта';name.required=true;name.maxLength=150;name.setAttribute('aria-label','Название комплекта');const submit=el('button','Новый комплект');create.append(name,submit);create.onsubmit=protect(async e=>{e.preventDefault();selected=await api('/adventure/library',{title:name.value,metadata:{rights:'Личные материалы пользователя'}});await showBundle(selected.id);});body.append(create);
  const list=await api('/adventure/library');
  // Every bundle is created with the same default title, so the heading alone
  // cannot tell thirteen cards apart. The card names its position, status and size.
  list.forEach((item,index)=>{const card=el('article',undefined,'page-card');
   const head=el('h2',item.title);head.append(el('span',' · комплект '+(index+1)+' из '+list.length,'hint'));card.append(head);
   // Карточка печатает статус агрегата, а не слово из старого поля: два разных
   // источника статуса на одном экране — это и были «12 модулей» против «11».
   const state=item.readiness||null;
   card.append(el('p',state?state.headline:(statuses[item.status]||item.status),'hint'),
    el('p',state?countsLine(state.counts):('Источники: '+item.documents+' · Страницы: '+item.pages),'hint'),
    el('p',state?(state.start_ready?'Старт готов':'Старт не готов')+' · '+(state.book_ready?'вся книга готова':'вся книга не готова'):'','hint'),
    button('Открыть комплект',()=>showBundle(item.id)));body.append(card);});
  if(!list.length)body.append(el('p','Добавьте книгу, сборник или собственный сценарий. Приключение начинается после проверки источников и создания героя.','prose'));
 }
 async function showBundle(id){
  selected=(await api('/adventure/library')).find(b=>b.id===id);if(!selected)throw new Error('Комплект не найден');view(library.id);
  body.replaceChildren(button('← Библиотека',showLibrary),el('h2',selected.title));
  // Один статус сверху: шаги, счётчики одной ревизии, два «готово» и
  // предупреждения с действиями. Ниже — только работа с книгой.
  if(selected.readiness)body.append(readinessPanel(selected.readiness,{full:true,id:selected.id}));
  else body.append(el('p',statuses[selected.status]||selected.status,'hint'));
  const purpose=el('select');for(const[value,title]of[['book','Книга / сценарий'],['maps','Карты (включая PDF в ZIP)'],['handouts','Раздаточные материалы'],['tokens','Портреты и токены'],['patch','Исправления']]){const o=el('option',title);o.value=value;purpose.append(o);}purpose.setAttribute('aria-label','Назначение материалов');
  const input=el('input');input.type='file';input.multiple=true;input.accept='.pdf,.zip,.txt,.md,.json,.png,.jpg,.jpeg,.webp,.svg';input.setAttribute('aria-label','Добавить материалы приключения');
  const progress=el('progress');progress.max=100;progress.value=0;progress.setAttribute('aria-label','Прогресс импорта');const status=el('p',undefined,'hint');status.setAttribute('role','status');body.append(field('Назначение',purpose),field('Добавить файлы (до 256 МБ каждый)',input),progress,status);
  input.onchange=protect(async()=>{input.disabled=true;try{for(const file of input.files){if(file.size>256*1024*1024)throw new Error('Файл больше 256 МБ');status.textContent='Загрузка локального файла…';const data=new FormData();data.append('purpose',purpose.value);data.append('file',file);
    const job=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/adventure/library/'+id+'/files');xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.value=Math.round(e.loaded/e.total*100);};xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status>=400)reject(new Error(result.detail||'Импорт не начат'));else resolve(result);}catch(e){reject(e);}};xhr.onerror=()=>reject(new Error('Локальное соединение прервано'));xhr.send(data);});
    while(true){const state=await api('/adventure/jobs/'+job.id);progress.value=state.total?state.done/state.total*100:0;status.textContent=state.message+' '+state.done+' / '+state.total;if(!['queued','running'].includes(state.status))break;await new Promise(resolve=>setTimeout(resolve,600));}
   }}finally{input.disabled=false;input.value='';}await showBundle(id);});
  // Сообщение прошлой операции — история, а не статус: в нём число модулей на
  // момент импорта. Здесь оно и лежит, закрытым, с датой и своей ревизией.
  if(selected.jobs?.length){const history=el('details',undefined,'disclosure'),listing=el('div');
   history.append(el('summary','История операций ('+selected.jobs.length+')'),
    el('p','Записи о прошлых операциях. Числа в них относятся к моменту импорта и не описывают текущее состояние книги.','hint'));
   for(const job of selected.jobs)listing.append(el('p',new Date((job.created||0)*1000).toLocaleString('ru-RU')+' · '+job.status+' · '+job.done+'/'+job.total+' · '+job.message,'hint'));
   history.append(listing);body.append(history);}
  body.append(button('Подготовка Мастера — содержит спойлеры',async()=>{await api('/adventure/review/session',{confirm_spoilers:true});reviewMode=true;await showReview(id);}));
  if(selected.status==='instructions_reviewed'&&window.soloChooseAdventure)body.append(button('Создать героя для этого приключения',()=>window.soloChooseAdventure(id),'primary'));
 }
 async function showReview(id,offset=0){
  reviewData=await api('/adventure/review/library/'+id);body.replaceChildren(button('Закрыть подготовку',()=>showLibrary()),el('p','ЗАКРЫТАЯ ПОДГОТОВКА МАСТЕРА','eyebrow'),el('h2',reviewData.bundle.title),el('p','Все карты закрыты по умолчанию. Не публикуйте страницу с комнатами, надписями или заметками Мастера. Открывайте отдельно проверенную версию для игрока.','hint'));
  // Тот же агрегат, что на карточке и на экране комплекта: подготовка не
  // заводит собственного счёта готовности.
  if(reviewData.readiness)body.append(readinessPanel(reviewData.readiness,{full:true,id}));
  const metadata=el('details',undefined,'disclosure');metadata.append(el('summary','Название, серия и редакция'));const title=el('input');title.value=reviewData.bundle.title;const edition=el('select');for(const value of ['', '2014','2024']){const o=el('option',value||'Не подтверждена');o.value=value;edition.append(o);}edition.value=reviewData.bundle.metadata.edition||'';metadata.append(field('Название на русском',title),field('Редакция источника',edition),button('Сохранить сведения',async()=>{await api('/adventure/library/'+id,{title:title.value,metadata:{...reviewData.bundle.metadata,edition:edition.value}},'PUT');await showReview(id);}));body.append(metadata);
  // Разбор модулей нужен карточкам: он один говорит, приложение это или
  // эпизод, какая сцена у модуля стартовая и можно ли с него начинать.
  let scenes=[];
  try{scenes=await api('/adventure/library/'+id+'/modules');}catch(error){scenes=null;}
  const parsed=new Map((scenes||[]).map(row=>[row.id,row]));
  if(reviewData.modules?.length){const modules=el('div',undefined,'cards-list');
   // Список модулей говорит те же слова, что агрегат: «границы сверены» — факт
   // нарезки, «старт возможен» — факт сцены. Слова «инструкции проверены» здесь
   // больше нет: одна фраза не может значить две разные вещи.
   const rows=new Map((reviewData.readiness?.modules||[]).map(row=>[row.id,row]));
   modules.append(el('h3','Эпизоды и приложения'));
   const requireWords=need=>need.kind==='minimum_level'?('уровень героя от '+need.value+'')
    :need.kind==='maximum_level'?('уровень героя до '+need.value)
    :need.kind==='recommended_party'?('группа из '+need.value+(need.maximum&&need.maximum!==need.value?'–'+need.maximum:'')+' героев')
    :need.kind==='continuation'?(need.text||'продолжение другого приключения'):'';
   for(const item of reviewData.modules){const state=rows.get(item.id)||{},known=parsed.get(item.id)||{},appendix=state.kind==='appendix'||known.kind==='appendix';
    // Кнопку старта рисует серверное поле, а не догадка клиента: приложение
    // книги это справочник, и старт с него не начинается никогда.
    const startable=known.startable!==undefined?Boolean(known.startable):!appendix;
    const card=el('article',undefined,'page-card');
    card.append(el('h3',item.body.title+' · '+item.id+(appendix?' · приложение':'')),
     el('p',appendix?'Приложение книги: справочные страницы. Места приложения перенесены в мир эпизодов, но старт отсюда не начинается.'
      :'Границы сверены: '+(state.boundaries?'да':'нет')+' · эпизод готов открыть старт: '+(state.ready?'да, если сцена цела':'нет'),'hint'));
    const entry=known.entry_scene||{};
    // Стартовая сцена — у того, с чего начинают. У приложения её нет: строка
    // «Стартовая сцена: “Приложение А: Монстры”» над «старт отсюда не начинается»
    // противоречила сама себе.
    if(startable&&entry.name){const ground=entry.place&&entry.place!==entry.name?'«'+entry.place+'»':'';
     const mark=entry.confidence==='check'?'название модуля не напечатано':'название напечатано в книге';
     const groundMark=entry.place_confidence==='printed'?'место напечатано':(entry.place?'место входа — догадка, стоит проверить':'');
     card.append(el('p','Стартовая сцена: «'+entry.name+'»'+(ground?', место входа: '+ground:'')+
      (entry.page?', стр. '+entry.page:'')+' · '+mark+(groundMark?' · '+groundMark:''),'hint'));}
    // Раздел приложения книга называет во множественном числе («Монстры»), а
    // строка — в единственном; на экране оба слова русские.
    if(appendix)card.append(el('p','Справочник: '+({monsters:'монстр',items:'предмет',tables:'таблица'}[known.reference]||'приложение')+'; входов '+Number(known.reference_entries||0)+'.',
     'hint'));
    for(const need of state.requirements||[]){const words=requireWords(need);if(!words)continue;
     const line=el('p','Книга просит: '+words+(need.page?', стр. '+need.page:''),'hint');if(need.quote)line.title=need.quote;card.append(line);}
    card.append(button('Открыть инструкции модуля',()=>{const box=body.querySelector('textarea[aria-label="Проверенные инструкции приключения"]');box.value=JSON.stringify(item.body,null,2);box.closest('details').open=true;}));
    if(startable&&window.soloChooseAdventure)card.append(button('Создать героя',()=>window.soloChooseAdventure(id,item.id)));
    if(startable&&state.ready&&current){const alone=el('input');alone.type='checkbox';
     card.append(field('Самостоятельный старт вместо переноса предыдущей книги',alone),button('Продолжить с текущим героем',async()=>{await api('/adventure/review/campaigns/'+campaignId+'/continue',{bundle:id,module:item.id,request_id:crypto.randomUUID(),confirm:true,standalone:alone.checked});await refresh();await showReview(id);toast('Переход выполнен. Герой и последствия сохранены.');}));
     card.append(button('Подключить сценарий к кампании',async()=>{await api('/adventure/review/campaigns/'+campaignId+'/attach',{bundle:id,module:item.id,request_id:crypto.randomUUID(),confirm:true});await refresh();toast('Сценарий подключён к кампании: карты и раздатки доступны в игре.');}));}
    modules.append(card);}
   body.append(modules);}
  const collection=el('details',undefined,'disclosure'),segments=el('textarea');segments.rows=5;segments.setAttribute('aria-label','Границы приключений сборника');segments.placeholder='[{"id":"DDEX2-1","title":"Название","first_page":1,"last_page":10}]';const docSelect=el('select');for(const d of reviewData.documents.filter(d=>d.format==='.pdf')){const o=el('option',d.original_name);o.value=d.id;docSelect.append(o);}collection.append(el('summary','Разделить сборник по проверенным границам'),field('Исходный документ',docSelect),segments,button('Сохранить границы модулей',async()=>{await api('/adventure/review/library/'+id+'/collection',{document:docSelect.value,entries:JSON.parse(segments.value),confirmed:true});await showReview(id);}));body.append(collection);
  const documents=el('details',undefined,'disclosure');documents.append(el('summary','Документы и качество извлечения'));for(const d of reviewData.documents)documents.append(el('p',d.original_name+' · '+d.pages+' страниц · '+d.status),el('p',(d.quality.blank_pages||0)+' страниц без уверенного текста · '+(d.quality.partial_pages?.length||0)+' частично извлечённых; правила требуют проверки','hint'));body.append(documents);
    // r221: разбор книги — «Места», «Люди», «Проверки», как в макете 05-book.
  // Сцена модуля уже хранит находки вместе с напечатанной страницей, а строка,
  // которой нет на её странице, отброшена импортом. Здесь только разметка:
  // названия, числа и страницы приходят с сервера, ничего не угадывается.
  const abilities={strength:'Сила',dexterity:'Ловкость',constitution:'Телосложение',intelligence:'Интеллект',wisdom:'Мудрость',charisma:'Харизма'};
  const skills={acrobatics:'Акробатика',animal_handling:'Обращение с животными',arcana:'Магия',athletics:'Атлетика',deception:'Обман',history:'История',insight:'Проницательность',intimidation:'Запугивание',investigation:'Расследование',medicine:'Медицина',nature:'Природа',perception:'Внимательность',performance:'Выступление',persuasion:'Убеждение',religion:'Религия',sleight_of_hand:'Ловкость рук',stealth:'Скрытность',survival:'Выживание'};
  // Уверенность не выдумывается: страница, которую извлекатель пометил
  // частично прочитанной, даёт метку «стоит проверить», остальные — «точно».
  const partial=new Set();for(const d of reviewData.documents||[])for(const page of ((d.quality||{}).partial_pages||[]))partial.add(d.id+':'+page);
  const brief=text=>String(text||'').replace(/\s+/g,' ').trim().slice(0,180);
  // «3 страниц» — не по-русски: число страниц склоняется как обычное.
  const pagesWord=n=>{const tail=n%100,last=n%10;return last===1&&tail!==11?'страница':last>=2&&last<=4&&(tail<10||tail>=20)?'страницы':'страниц';};
  // Ключ проверки включает место: две одинаковые проверки в разных комнатах —
  // две строки, и править их надо по отдельности (тот же ключ у сервера).
  const checkKey=row=>'check:'+(row.location||'')+':'+(row.skill||row.ability||'')+':'+row.dc;
  const typeLabel={place:'место',npc:'человек',monster:'монстр',item:'предмет',table:'таблица',check:'проверка',
   monsters:'монстр',items:'предмет',tables:'таблица',reference:'справочник'};
  const rows={places:[],people:[],checks:[],reference:[],left:[]};
  for(const module of scenes||[]){const scene=module.scene||{};
   for(const row of scene.locations||[])rows.places.push({name:row.name,label:row.name,note:row.gloss||brief(row.quote),page:row.page,document:row.document||scene.document,module:module.id,entity:'location',type:row.entity_type||'place',quote:row.quote,edited:row.edited});
   for(const row of scene.npcs||[])rows.people.push({name:row.name,label:row.name,note:row.gloss||brief(row.quote),page:row.page,document:row.document||scene.document,module:module.id,entity:'npc',type:'npc',quote:row.quote,edited:row.edited});
   for(const row of scene.checks||[])rows.checks.push({name:checkKey(row),label:(row.saving_throw?'Спасбросок · '+abilities[row.ability]:skills[row.skill]||abilities[row.ability]||'Проверка')+' · нужно '+row.dc+' на d20',note:brief(row.trigger),page:row.page,document:row.document||scene.document,module:module.id,entity:'check',type:'check',quote:row.quote,edited:row.edited});
   for(const row of scene.reference||[])rows.reference.push({name:row.name,label:row.name,note:(typeLabel[row.entity_type||row.kind]||'справочник')+(row.edited?' · исправлено человеком':''),page:row.page,document:row.document||scene.document,module:module.id,entity:'reference',type:row.entity_type||row.kind||'monster',quote:row.quote,edited:row.edited});
   // Строку справочника, чьей цитаты нет на названной странице, импорт тоже
   // отбросил: она обязана попасть в счёт «не разобрано», а не исчезнуть.
   for(const row of scene.rejected||[])rows.left.push(row);
   for(const row of scene.reference_rejected||[])rows.left.push(row);
  }
  const fromScene=rows.places.length+rows.people.length+rows.checks.length;
  // Книгу без глав импорт не режет на модули, и сцены у неё нет. Тогда в
  // «Местах» стоит то, что он действительно нашёл — заголовки глав; пустые
  // разделы так и говорят «пока пусто», а не показывают выдуманные имена.
  if(!fromScene)for(const c of reviewData.candidates||[])if(['location','scene','chapter','adventure'].includes(c.kind))rows.places.push({name:c.title,note:'',page:c.page,document:c.document});
  const leftKinds=['location','scene','chapter','adventure','npc','opponent','check'];
  const rest=fromScene?[]:(reviewData.candidates||[]).filter(c=>!leftKinds.includes(c.kind));
  const sheet=el('div',undefined,'booksheet'),top=el('div',undefined,'parse-head');
  top.append(el('p','разбор книги','eyebrow'),el('h2','Что мастер понял','parse-title'),el('p','«'+reviewData.bundle.title+'» · '+(reviewData.documents||[]).map(d=>d.pages+' '+pagesWord(d.pages)).join(' + ')+' · находок '+(rows.places.length+rows.people.length+rows.checks.length+rows.reference.length)+(fromScene?'':' · книга не разрезана на главы'),'parse-sub'));
  const legend=el('ul',undefined,'conf');
  for(const [mark,label] of [['ok','прочитано точно'],['check','стоит проверить'],['bad','не разобрано']]){const line=el('li'),dot=el('span',undefined,'dot');dot.dataset.conf=mark;line.append(dot,document.createTextNode(label));legend.append(line);}
  top.append(legend);sheet.append(top);
  const typeOptions={location:[['place','Место'],['npc','Человек'],['monster','Монстр (справочник)'],['item','Предмет (справочник)'],['table','Таблица (справочник)'],['ignore','Не сущность']],
   npc:[['npc','Человек'],['place','Место'],['monster','Монстр (справочник)'],['item','Предмет (справочник)'],['table','Таблица (справочник)'],['ignore','Не сущность']],
   check:[['check','Проверка'],['ignore','Не сущность']],
   reference:[['monster','Монстр'],['item','Предмет'],['table','Таблица'],['place','Место'],['npc','Человек'],['ignore','Не сущность']]};
  const editControl=item=>{const select=el('select');select.setAttribute('aria-label','Тип сущности: '+(item.label||item.name));for(const[value,text]of typeOptions[item.entity]||[]){const o=el('option',text);o.value=value;select.append(o);}select.value=item.type||'';select.onchange=protect(async()=>{select.disabled=true;try{await api('/adventure/review/library/'+id+'/entities',{module:item.module,document:item.document,page:item.page,entity:item.entity,name:item.name,value:select.value},'PUT');toast('Тип сущности сохранён: правка переживёт повторный разбор.');await showReview(id);}finally{select.disabled=false;}});return select;};
  const parseSection=(title,items,empty)=>{const box=el('section',undefined,'bsec'),heading=el('div',undefined,'bsec__head');
   heading.append(el('h3',title,'bsec__title'),el('span',String(items.length),'bsec__count'));box.append(heading);
   if(!items.length)box.append(el('p',empty,'bsec__note'));
   else{const list=el('ul',undefined,'items');
    for(const item of items.slice(0,60)){const conf=partial.has(item.document+':'+item.page)?'check':'ok';const line=el('li',undefined,'item'+(conf==='check'?' item--check':'')),dot=el('span',undefined,'dot'),content=el('span',undefined,'item__body');
     dot.dataset.conf=conf;dot.setAttribute('aria-label',conf==='check'?'стоит проверить':'прочитано точно');
     content.append(el('span',item.label||item.name,'item__name'));if(item.note)content.append(el('span',item.note,'item__note'));
     if(item.quote){const quote=el('details',undefined,'item__quote');quote.append(el('summary','Цитата со страницы'),el('p','«'+brief(item.quote)+'»','hint'),el('p','стр. '+item.page+' · '+(partial.has(item.document+':'+item.page)?'прочитано частично':'прочитано целиком'),'hint'));content.append(quote);}
     if(item.module&&item.entity)content.append(editControl(item));
     line.append(dot,content,el('span','стр. '+item.page,'item__page'));list.append(line);}
    box.append(list);if(items.length>60)box.append(el('p','Показаны первые 60 из '+items.length+'.','bsec__note'));}
   return box;};
  sheet.append(parseSection('Места',rows.places,'Пока пусто: в этой книге мастер не нашёл названных мест.'),
               parseSection('Люди',rows.people,'Пока пусто: в этой книге мастер не встретил людей по имени.'),
               parseSection('Проверки',rows.checks,'Пока пусто: книга не печатает проверок с числом.'),
               parseSection('Справочник',rows.reference,'Пока пусто: в этой книге нет приложений-справочников.'));
  const gaps=[];
  for(const d of reviewData.documents||[]){const quality=d.quality||{};if(quality.blank_pages)gaps.push([d.original_name+': '+quality.blank_pages+' страниц без уверенного текста','Мастер их не прочитал. Если там что-то важное — распознайте текст и загрузите снова.']);}
  const skipped=[];for(const module of scenes||[])for(const row of (module.scene||{}).edits_rejected||[])skipped.push(row);
  if(skipped.length)gaps.push([skipped.length+' правок человека не применилось',
   'Правка сохранена, но строки с таким именем и страницей в сцене больше нет: книга перечитана иначе. Проверьте строку вручную.']);
  if(rows.left.length)gaps.push([rows.left.length+' находок отброшено при сверке','Строка не нашлась на своей странице, поэтому в мир она не попала. Откройте страницу и проверьте её вручную.']);
  const gapSection=el('section',undefined,'bsec'),gapHead=el('div',undefined,'bsec__head');gapHead.append(el('h3','Чего не хватило','bsec__title'),el('span',String(gaps.length),'bsec__count'));gapSection.append(gapHead);
  if(!gaps.length)gapSection.append(el('p','Все страницы прочитаны уверенно.','bsec__note'));
  else{const list=el('ul',undefined,'gaps');for(const [title,note] of gaps){const line=el('li',undefined,'gap gap--error'),box=el('span',undefined,'gap__body');box.append(el('span',title,'gap__title'),el('span',note,'gap__note'));line.append(el('span',undefined,'gap__mark'),box);list.append(line);}gapSection.append(list);}
  sheet.append(gapSection);
  if(rest.length){const more=el('details',undefined,'disclosure');more.append(el('summary','Прочие находки для сверки ('+rest.length+')'));for(const c of rest.slice(0,100))more.append(el('p',c.title+' · PDF '+c.page+' · '+c.kind,'prose'));sheet.append(more);}
  // Лист разбора стоит первым: макет 05-book открывается им, а служебные
  // блоки (название, границы, качество) уходят под него.
  body.insertBefore(sheet,body.querySelector('.disclosure'));
  const gallery=el('div',undefined,'adventure-gallery');body.append(el('h3','Карты и страницы'),gallery);
  const filter=el('select');for(const[value,title]of[['all','Все страницы'],['map','Отдельные карты'],['handout','Раздатки']]){const o=el('option',title);o.value=value;filter.append(o);}filter.setAttribute('aria-label','Фильтр материалов');body.insertBefore(field('Материалы',filter),gallery);
  // A picture cut out of a book page is a candidate of a kind, not a page: the
  // card says which one so a person can confirm or reject it without guessing.
  const labels={map:'карта',handout:'раздатка',illustration:'иллюстрация'};
  function drawGallery(){gallery.replaceChildren();const rows=reviewData.materials.filter(m=>filter.value==='all'||m.kind===filter.value);for(const m of rows.slice(offset,offset+24)){const card=el('article',undefined,'page-card'),image=el('img');image.loading='lazy';image.alt='Страница '+m.page;image.src='/api/adventure/review/materials/'+m.id+'?width=480';const candidate=m.status==='candidate';const heading=m.public_name||(candidate?'Кандидат · '+(labels[m.kind]||m.kind)+' · страница '+m.page:(m.source_name?m.source_name+' · ':'')+'Страница '+m.page);card.append(image,el('h3',heading),el('p',candidate?'Вырезано со страницы. Подтвердите, что это карта или раздатка, а не буквица и не фон':m.scope==='gm'?'Закрытый материал':'Проверенная открытая версия','hint'),button('Проверить страницу',()=>inspectMedia(m)));gallery.append(card);}if(rows.length>24)gallery.append(button('Следующие страницы',()=>showReview(id,(offset+24)%rows.length)));}
  filter.onchange=drawGallery;if(reviewData.materials.some(m=>m.kind==='map'))filter.value='map';drawGallery();
  const analysis=el('details',undefined,'disclosure');analysis.append(el('summary','Помощник разбора выбранных страниц'),el('p','Облачный анализ необязателен. По умолчанию отправляется только извлечённый текст. Для Gemini можно отдельно приложить до двух небольших изображений страниц. Результат и распознавание остаются черновиком для ручной сверки.','hint'));
  const source=el('select');for(const d of reviewData.documents.filter(d=>d.pages)){const o=el('option',d.id.slice(0,12)+' · '+d.pages+' стр.');o.value=d.id;source.append(o);}const pages=el('input');pages.placeholder='1, 2';pages.maxLength=35;const role=el('select');for(const[value,title]of[['qwen','Qwen — обычный разбор'],['gemini','Gemini — сложные страницы после локальной проверки']]){const o=el('option',title);o.value=value;role.append(o);}const problem=el('textarea');problem.rows=2;problem.maxLength=1000;const consent=el('input');consent.type='checkbox';const images=el('input');images.type='checkbox';images.disabled=true;role.onchange=()=>{images.disabled=role.value!=='gemini';if(images.disabled)images.checked=false;};const result=el('pre');result.className='analysis-result';
  analysis.append(field('Источник',source),field('Страницы — максимум четыре',pages),field('Помощник',role),field('Какая проблема требует сверки',problem),field('Приложить изображения страниц — только Gemini, максимум две; нужен подтверждённый тариф',images),field('Разрешаю отправить эти страницы провайдеру с учётом настроенных лимитов расходов',consent),button('Разобрать страницы',async()=>{if(!consent.checked)throw new Error('Подтвердите отправку выбранных страниц');const numbers=pages.value.split(',').map(s=>Number(s.trim()));if(images.checked&&numbers.length>2)throw new Error('С изображениями выберите максимум две страницы');if(numbers.some(n=>!Number.isInteger(n)||n<1)||numbers.length>4)throw new Error('Укажите от одной до четырёх страниц');const value=await api('/adventure/review/library/'+id+'/analysis',{document:source.value,pages:numbers,role:role.value,problem:problem.value,include_images:images.checked,consent_to_send:true,request_id:crypto.randomUUID()});result.textContent=JSON.stringify({cached:value.cached,status:value.status,result:value.result,cost:value.metric?.cost,currency:value.metric?.currency},null,2);}),result);body.append(analysis);
  const advanced=el('details',undefined,'disclosure');advanced.append(el('summary','Проверенные инструкции — расширенная подготовка'),el('p','Только сверенные по страницам декларативные события. Неподдержанная механика блокирует строгий запуск. Это инструмент подготовки, не игровой экран.','hint'));
  const textarea=el('textarea');textarea.rows=12;textarea.setAttribute('aria-label','Проверенные инструкции приключения');textarea.value=JSON.stringify(reviewData.definition||{version:1,id:'adventure',title:reviewData.bundle.title,ruleset:'dnd2014',ruleset_version:1,events:[],tables:[],trackers:[],media:[],unsupported:['Сценарные инструкции ещё не проверены'],reviewed:false},null,2);const confirmation=el('input');confirmation.type='checkbox';advanced.append(textarea,field('Я сверил указанные инструкции и происхождение с первоисточником',confirmation),button('Сохранить проверку',async()=>{if(!confirmation.checked)throw new Error('Сначала сверьте сценарий');await api('/adventure/review/library/'+id+'/definition',{definition:JSON.parse(textarea.value),confirmed:true},'PUT');await showReview(id);toast('Проверка сохранена. Неподдержанные механики продолжают блокировать строгий запуск.');}));body.append(advanced);
 }
 async function inspectMedia(material){
  const dialog=el('dialog',undefined,'media-viewer'),image=el('img');image.src='/api/adventure/review/materials/'+material.id+'?width=2048';image.alt='Страница '+material.page;
  const viewport=el('div',undefined,'media-viewport');viewport.append(image);dialog.append(el('h2','Проверка страницы '+material.page),viewport);
  const zoom=el('input');zoom.type='range';zoom.min=50;zoom.max=300;zoom.value=100;zoom.oninput=()=>image.style.width=zoom.value+'%';dialog.append(field('Масштаб страницы',zoom));
  const name=el('input');name.value=material.public_name;name.maxLength=120;const scope=el('select');for(const[value,title]of[['gm','Только Мастер'],['player','Открытая версия для игрока'],['character','Личный материал героя']]){const option=el('option',title);option.value=value;scope.append(option);}scope.value=material.scope;
  const handoutText=el('textarea');handoutText.rows=5;handoutText.maxLength=8000;handoutText.value=material.public_text||'';dialog.append(field('Проверенный текст открытой раздатки',handoutText),button('Показать извлечённый текст для сверки',async()=>{const result=await api('/adventure/review/materials/'+material.id+'/text');handoutText.value=result.text.slice(0,8000);toast(result.warning);}));const checked=el('input');checked.type='checkbox';checked.checked=!!material.sanitized;dialog.append(field('Открытое название',name),field('Доступ',scope),field('В этой версии нет скрытых сведений Мастера',checked));
  dialog.append(button('Сохранить доступ',async()=>{await api('/adventure/review/materials/'+material.id,{scope:scope.value,name:name.value,confirmed:true,sanitized:checked.checked,geometry:material.geometry?JSON.parse(material.geometry):null,variant:material.variant,public_text:handoutText.value},'PUT');dialog.close();await showReview(reviewData.bundle.id);}),button('Подготовить геометрию',()=>window.soloPrepareGeometry(material,dialog)),button('Закрыть',()=>dialog.close()));dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
 }
 const media=el('section',undefined,'view');media.id='adventure-handouts';media.append(el('p','НАЙДЕННЫЕ МАТЕРИАЛЫ','eyebrow'),el('h1','Раздатки и карты'));const mediaBody=el('div',undefined,'adventure-gallery');media.append(mediaBody);$('journal').after(media);
 const mediaNav=el('button',undefined,'nav');mediaNav.dataset.view=media.id;mediaNav.append(window.soloIcon('map'),el('span','Раздатки и карты','nav-text'));document.querySelector('.campaign-nav .nav-submenu').append(mediaNav);
 function renderMedia(){mediaBody.replaceChildren();for(const item of current?.state.adventure?.materials||[]){const card=el('article',undefined,'page-card');card.append(el('h2',item.name),button('Рассмотреть',()=>{const dialog=el('dialog',undefined,'media-viewer'),viewport=el('div',undefined,'media-viewport'),image=el('img');image.alt=item.name;image.src='/api/campaigns/'+campaignId+'/adventure/materials/'+item.id+'?width=2048';viewport.append(image);const zoom=el('input');zoom.type='range';zoom.min=50;zoom.max=300;zoom.value=100;zoom.oninput=()=>image.style.width=zoom.value+'%';dialog.append(el('h2',item.name),viewport,field('Масштаб',zoom),button('Выбрать для реплики',()=>{window.soloSelect({kind:'handout',id:item.id},item.name);dialog.close();view('play');}),button('Закрыть',()=>dialog.close()));document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();}));mediaBody.append(card);}if(!mediaBody.childElementCount)mediaBody.append(el('p','Герой пока не обнаружил открытых карт или раздаток.','hint'));}
 mediaNav.onclick=protect(()=>{needCampaign();view(media.id);renderMedia();});document.addEventListener('solo:render',renderMedia);
})();

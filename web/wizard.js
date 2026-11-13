/* Step wizard: every option, count and verdict comes from the server contract. */
(()=>{
 'use strict';
 const KEY='solo-wizard-draft',PENDING='solo-wizard-pending';
 const STEP_TITLES={species:'Вид',lineage:'Наследие',class:'Класс',subclass:'Подкласс',background:'Предыстория',scores:'Характеристики',skills:'Выборы',magic:'Магия',equipment:'Снаряжение',final:'Итог и имя'};
 const ABILITY_ORDER=['strength','dexterity','constitution','intelligence','wisdom','charisma'];
 const ABILITY_TITLES={strength:'Сила',dexterity:'Ловкость',constitution:'Телосложение',intelligence:'Интеллект',wisdom:'Мудрость',charisma:'Харизма'};
 const state={open:false,draft:null,answers:{},contract:null,seq:0,busy:false,error:'',mode:'create',prepared:null,levelUp:null,applied:false,advance:false,import:null,source:'',sourceDraft:'',edited:0,retry:null,errorSource:'',screen:0,drawing:false,queued:false,world:null,fitting:null,heroDraft:null,carried:[],carriedItems:[],moduleId:'',book:null};
 let ui=null,restoreFocus=null,shownScreen=-1;
 function blank(){return {species_slug:null,species_base:null,class_slug:null,subclass_slug:null,background_slug:null,level:1,ability_score_method:null,ability_scores:{},selected_choices:[],equipment_kits:{},kit_picks:{},prepared_spells:[],name:'',backstory:'',choices:{},advancement:false};}
 function merge(saved){const base=blank();if(!saved||typeof saved!=='object')return base;for(const key of Object.keys(base))if(Object.hasOwn(saved,key)&&saved[key]!==null&&saved[key]!==undefined)base[key]=saved[key];if(!Array.isArray(base.prepared_spells))base.prepared_spells=[];return base;}
 // A draft belongs to one adventure and one mode. The old key held a single
 // unnamed blob, so starting a new hero restored the previous one, complete with
 // its answers and step number. The record now names its owner; a record whose
 // owner, mode or schema version does not match is not this hero and is offered
 // for an explicit restore instead of being applied.
 const SCHEMA=3;
 function scope(){return {adventure:state.prepared?('prepared:'+state.prepared.draft_id):'sandbox',mode:state.mode,schema:SCHEMA};}
 function persist(){if(!state.open)return;try{localStorage.setItem(KEY,JSON.stringify(Object.assign(scope(),{draft:state.draft,answers:state.answers,screen:state.screen})));}catch(e){}}
 function stored(){try{const row=JSON.parse(localStorage.getItem(KEY));return row&&row.draft?row:null;}catch(e){return null;}}
 function recall(){const row=stored();if(!row)return null;const now=scope();if(row.adventure!==now.adventure||row.mode!==now.mode||row.schema!==now.schema)return null;return row;}
 // A draft made in another adventure is kept, not destroyed: the player chooses
 // whether to bring its answers here.
 function otherDraft(){const row=stored();if(!row||!row.draft)return null;const now=scope();if(row.adventure===now.adventure&&row.mode===now.mode&&row.schema===now.schema)return null;const filled=JSON.stringify(row.draft||{})!==JSON.stringify(blank());return filled?row:null;}
 function forget(){try{localStorage.removeItem(KEY);}catch(e){}}
 // Сервер ждёт словарь `answers`: список в теле — 422 `dict_type` и «Не удалось
 // обработать запрос» без причины (такой отказ ловился в журнале живьём).
 function contractPayload(){const answers=state.answers&&typeof state.answers==='object'&&!Array.isArray(state.answers)?state.answers:{};const out={draft:state.draft,answers};const was=Number(state.fitting);if(Number.isInteger(was)&&was>Number(state.draft.level))out.from_level=was;return out;}
// A suggestion the server is slow to build must not leave the wizard waiting
// forever: the footer stays busy-flagged and every other button stays disabled.
// The deadline turns that dead end into a visible error the player can retry.
function deadline(promise,seconds,message){return Promise.race([promise,new Promise((resolve,reject)=>setTimeout(()=>reject(new Error(message)),seconds*1000))]);}
 function steps(){return (state.contract&&state.contract.steps)||[];}
 // r249: выбранный шаг подписывается так же, как варианты шага, — русским
 // именем; slug остаётся ключом и на экран не попадает.
 function chosenName(id){const row=steps().find(step=>step.id===id);const chosen=(row&&(row.chosen||[]).slice(0,1)[0])||'';if(!chosen)return '';const option=((row&&row.options)||[]).find(item=>String(item.id)===String(chosen));return option&&option.name?option.name:String(chosen);}
 function blockingIds(){return new Set((state.contract&&state.contract.blocking)||[]);}
 // Один вопрос — один экран. Договор присылает шаги по секциям; телефон
 // собирает их в шесть экранов по смыслу: раса → класс → прошлое →
 // характеристики → магия → снаряжение и имя. Больше шести не бывает, и ни
 // одна обязательная секция не выбрасывается ради краткости: навыки живут на
 // экране своего источника, снаряжение и имя — на последнем.
 const SCREEN_DEFS=[
  {id:'origin',title:'Раса',question:'Кем вы родились?',ids:['species','lineage'],hint:'Вид — то, с чем герой родился: рост, чувства, наследие.'},
  {id:'class',title:'Класс',question:'Чем вы занимаетесь?',ids:['class','subclass'],hint:'Класс — ремесло героя: чем он бьётся и что умеет.'},
  {id:'background',title:'Прошлое',question:'Откуда вы пришли?',ids:['background'],hint:'Прошлое даёт знания и прибавки к характеристикам.'},
  {id:'scores',title:'Характеристики',question:'Насколько вы сильны и ловки?',ids:['scores'],hint:'Прибавка «на d20» — то, что вы прибавите к броску кубика.'},
  {id:'magic',title:'Магия',question:'Что вы умеете колдовать?',ids:['magic'],hint:'Заговоры работают всегда, заклинания тратят ячейки на день.'},
  {id:'gear',title:'Готово',question:'Имя и снаряжение',ids:['equipment','final'],hint:'Что герой берёт с собой и как его зовут.'},
 ];
 // Пояснение под названием — одна строка для человека, который правил не
 // читал. Названия и числа по-прежнему приходят из каталога и Nat20; здесь
 // только подпись, поэтому английское имя движка на экран не попадает.
 const ABOUT={
  Dragonborn:'Драконья кровь: дыхание стихии, крепкая шкура',Dwarf:'Камень и упрямство: крепкое здоровье, тёмное зрение',
  Elf:'Долгая жизнь и острый слух, три наследия на выбор',Gnome:'Мелкие и хитрые: острый ум и цепкая память',
  Goliath:'Рост выше двух метров: сила и выносливость',Halfling:'Маленький и вёрткий: удача и бесстрашие',
  Human:'Гибкий: подходит любому классу',Orc:'Ярость и напор: бьёт сильнее и встаёт после удара',
  Tiefling:'Наследие преисподней: огонь и тёмное зрение',
  'elf-drow':'Подземный эльф: тёмное зрение и магия тьмы','elf-high':'Высший эльф: лишний заговор и острый ум','elf-wood':'Лесной эльф: быстрые ноги и скрытность',
  'gnome-forest':'Лесной гном: мелкая иллюзия и разговор со зверями','gnome-rock':'Скальный гном: твёрдость и знание механизмов',
  'tiefling-abyssal':'Бездна: яд и стойкость к нему','tiefling-chthonic':'Хтонь: холод и чужие мысли','tiefling-infernal':'Инферно: огонь и тёмное зрение',
  barbarian:'Ярость вместо доспеха: много здоровья, тяжёлое оружие',bard:'Песня и обаяние: лечит, убеждает, знает всё понемногу',
  cleric:'Молитва и доспех: лечит своих и карает чужих',druid:'Природа и зверь: превращения и заклинания земли',
  fighter:'Оружие и доспех: самый крепкий в бою',monk:'Тело как оружие: скорость и удары без доспеха',
  paladin:'Клятва и доспех: защищает своих и бьёт светом',ranger:'Охотник: лук, следы и зверь-спутник',
  rogue:'Скрытность и точный удар: ворует и не попадается',sorcerer:'Магия в крови: колдует без книги',
  warlock:'Договор с покровителем: чужая сила за службу',wizard:'Книга заклинаний: самая большая магия, самое хрупкое здоровье',
  acolyte:'Храм: вера, обряды и книги',criminal:'Улица: скрытность, ловкость рук и знакомства',
  sage:'Книжник: история, магия и старые рукописи',soldier:'Служба: дисциплина, оружие и выносливость',
  standard_array:'Шесть готовых чисел: 15, 14, 13, 12, 10, 8',point_buy:'27 очков на покупку значений',
  acrobatics:'Ловкость: прыгать, кувыркаться, держать равновесие',animal_handling:'Мудрость: успокоить и выездить зверя',
  arcana:'Интеллект: вспомнить про магию и её следы',athletics:'Сила: лазать, плавать, толкать',
  deception:'Харизма: соврать так, чтобы поверили',history:'Интеллект: вспомнить событие или род',
  insight:'Мудрость: понять, врёт ли собеседник',intimidation:'Харизма: заставить отступить',
  investigation:'Интеллект: найти след и собрать картину',medicine:'Мудрость: остановить кровь и понять болезнь',
  nature:'Интеллект: растения, звери, погода',perception:'Мудрость: заметить то, что прячут',
  performance:'Харизма: сыграть так, чтобы слушали',persuasion:'Харизма: договориться без угроз',
  religion:'Интеллект: боги, обряды, нежить',sleight_of_hand:'Ловкость: украсть незаметно',
  stealth:'Ловкость: пройти так, чтобы не услышали',survival:'Мудрость: следы, еда, дорога',
 };
// Выборы-особенности (черты происхождения, стили боя) договор пока присылает
 // slug'ом: alert, magic-initiate, great-weapon-fighting. Русские имена этих же
 // вариантов уже есть в каталоге (solo/character_catalog.py: FEATURES,
 // FIGHTING_STYLES) — здесь они для экрана, пока сервер не отдаёт подпись сам.
 // Остальные особенности (воззвания колдуна, ордена жреца и друида, наследие
 // голиафа, сопротивления драконорождённого) перевода не имеют и остаются
 // английскими: это данные SRD, как и имена заклинаний.
 const FEAT_ABOUT={
  alert:['Бдительный','Внимательный: редко застают врасплох'],
  'magic-initiate':['Посвящённый в магию','Два заговора и одно заклинание первого круга'],
  'savage-attacker':['Дикий атакующий','Бьёт жёстче: второй бросок урона'],
  skilled:['Умелец','Три любых навыка на выбор'],
  archery:['Стрельба','+2 к атаке из дальнего оружия'],
  defense:['Защита','+1 к защите, пока надет доспех'],
  'great-weapon-fighting':['Бой большим оружием','Перебрасывает 1 и 2 на уроне'],
  'two-weapon-fighting':['Бой двумя оружиями','Второе оружие бьёт с прибавкой'],
 };
 function featAbout(id){const key=String(id||'').replace(/_/g,'-').toLowerCase();return FEAT_ABOUT[key]||null;}
 function screenOf(step){const id=step.id;const def=SCREEN_DEFS.find(row=>row.ids.includes(id));if(def)return def.id;const source=String(step.choice_key||'').split(':')[0];if(source==='species'||source==='lineage')return 'origin';if(source==='class'||source==='subclass')return 'class';if(source==='background')return 'background';return 'scores';}
// Повышение не пересобирает героя: показывается только то, что новый уровень
// оставил ответить — подкласс на своём уровне, заклинание нового круга,
// положенная черта, повышение характеристик. Вид, класс, характеристики и
// снаряжение уже решены и здесь не переспрашиваются: договор уровня называет
// недоделанным ровно то, что движок потребует до подтверждения.
const CREATION_ONLY=new Set(['species','lineage','class','background','scores','equipment']);
function owedSteps(rows){return rows.filter(step=>step.id==='final'||
 (!CREATION_ONLY.has(step.id)&&step.required&&step.status!=='complete'));}
 function screens(){const rows=steps();const shown=state.mode==='level'&&state.levelUp?owedSteps(rows):rows;
 return SCREEN_DEFS.map(def=>Object.assign({},def,{steps:shown.filter(step=>screenOf(step)===def.id)})).filter(screen=>screen.steps.length)
  .map(screen=>{if(state.mode!=='level'||!state.levelUp)return screen;screen.hint='';if(screen.id==='gear'){screen.title='Готово';screen.question='Подтвердите новый уровень';return screen;}const first=screen.steps.find(step=>step.choice_key)||screen.steps[0];if(first&&first.title){screen.title=first.title;screen.question=first.title;}return screen;});}
 function screenIndex(){const list=screens();return Math.min(Math.max(Number(state.screen)||0,0),Math.max(list.length-1,0));}
 function activeScreen(){return screens()[screenIndex()]||null;}
 function isLastScreen(){return screenIndex()>=screens().length-1;}
 // Выбор класса может заново открыть выбор вида, прошлое — прибавку. Экран,
 // который игрок уже прошёл, тогда просит вернуться; молча этого не бывает.
 function reopened(){const list=screens(),index=screenIndex(),rows=[];for(let i=0;i<index;i++)for(const step of list[i].steps)if(step.required&&step.status!=='complete'){rows.push({screen:list[i],step,index:i});break;}return rows;}
 function about(step,row){const id=String(row.id),slug=id.toLowerCase();if(ABOUT[id])return ABOUT[id];if(ABOUT[slug])return ABOUT[slug];if(step.id==='magic'){const level=String(row.detail||'');if(/уровень\s*0/.test(level))return 'Заговор: работает без ячеек';const match=level.match(/(\d+)/);return match?'Заклинание '+match[1]+' уровня: тратит ячейку':'Заклинание';}if(step.id==='equipment')return String(row.detail||'');return row.detail&&row.detail!==row.name?String(row.detail):'';}
 function humanReason(text){const value=String(text).replace(/\.$/,'');return value?value.charAt(0).toUpperCase()+value.slice(1):'';}
 // «Дальше» не уходит молча: фраза называет, чего не хватает на этом экране.
 function stepNeed(step){if(!step||step.status==='complete')return '';const id=step.id;
  if(id==='species')return 'Выберите вид';
  if(id==='lineage')return 'Выберите наследие: этот вид делится на несколько';
  if(id==='class')return 'Выберите класс';
  if(id==='subclass')return 'Выберите подкласс';
  if(id==='background')return 'Выберите прошлое';
  if(id==='scores'){const budget=step.budget||{};if(!step.chosen_method)return 'Выберите способ: готовый набор или покупка за очки';if(budget.kind==='point_buy')return 'Разложите очки: осталось '+budget.left;if(budget.kind==='standard_array')return 'Разложите значения: осталось '+(budget.left||[]).length;return 'Заполните все шесть характеристик';}
  if(id==='magic'){const reason=(step.reasons||[])[0];return reason?humanReason(reason):'Выберите заговоры и заклинания';}
  if(id==='equipment')return (step.groups||[]).length?'Выберите комплект':'Комплекты появятся после выбора класса и прошлого';
  if(id==='final')return 'Впишите имя героя';
  const title=String(step.title||'');return (title?title+' — ':'')+'отметьте ещё '+Number(step.missing||0);}
 // Повышение уровня спрашивает только то, что открылось, поэтому и блокировать
 // «Дальше» оно должно своим экраном, а не всем договором.
 function levelNeed(screen){if(!state.contract)return 'Загружаем договор…';if(!screen)return '';
  // Показаны только недоделанные шаги нового уровня, поэтому «Дальше» упирается
  // в первый из них — и на последнем экране тоже.
  const open=step=>step.id!=='final'&&Number(step.missing||0)>0;
  for(const step of screen.steps)if(open(step))return stepNeed(step);
  if(isLastScreen())for(const step of steps())if(!CREATION_ONLY.has(step.id)&&open(step))return stepNeed(step);
  return '';}
 function screenNeed(screen){if(!state.contract)return 'Загружаем договор…';if(state.busy)return 'Проверяем шаг…';if(!screen)return 'Шагов пока нет';
  // На последнем экране сначала называют то, что осталось на других шагах:
  // выбор класса заново открывает выбор вида, а прошлое — прибавку.
  const first=screen.id==='gear'?screens().filter(row=>row.id!==screen.id):[];
  for(const row of first.concat([screen]))for(const step of row.steps)if(step.required&&step.status!=='complete')return row.id===screen.id?stepNeed(step):('Вернитесь на шаг «'+row.title+'» — '+stepNeed(step).toLowerCase());if(screen.id==='gear'&&!confirmReady()){const issues=state.contract.confirm_issues||[];if(issues.length)return 'Впишите имя героя: без него герой не создаётся';return 'Не всё обязательное выбрано — вернитесь к шагам выше';}return '';}
 function settle(){const contract=state.contract;if(!contract)return false;let changed=false;for(const step of contract.steps||[]){if(step.id!=='equipment')continue;for(const group of step.groups||[]){if(!state.draft.equipment_kits[group.id]&&group.recommended){state.draft.equipment_kits[group.id]=group.recommended;changed=true;}}}const live=new Set((contract.steps||[]).filter(step=>step.id==='equipment').flatMap(step=>(step.groups||[]).map(group=>group.id)));for(const key of Object.keys(state.draft.equipment_kits))if(!live.has(key)){delete state.draft.equipment_kits[key];delete state.draft.kit_picks[key];changed=true;}return changed;}
 // Ответ приходит на черновик, каким он был ДО набора: слияние его поверх
 // локального стирало имя и числа, которые игрок только что ввёл (на телефоне
 // запрос идёт секундами). Поэтому набранное за время запроса возвращается в
 // черновик, и шаг перезапрашивается уже с ним.
 function typedValues(remote){const kept={},root=ui&&ui.stage;if(!root)return kept;const name=root.querySelector('.wizard-field input[type=text]');const localName=name?name.value:(state.draft.name||'');if(localName!==(remote.name||''))kept.name=localName;const story=root.querySelector('.wizard-field textarea');const localStory=story?story.value:(state.draft.backstory||'');if(localStory!==(remote.backstory||''))kept.backstory=localStory;const inputs=[...root.querySelectorAll('.wizard-scores input[type=number]')];if(inputs.length===ABILITY_ORDER.length){const scores={},answered=remote.ability_scores||{};let changed=false;for(const [index,key] of ABILITY_ORDER.entries()){const raw=(inputs[index].value||'').trim(),local=raw===''?undefined:Number(raw);if(raw!=='')scores[key]=local;if(local!==answered[key])changed=true;}if(changed)kept.ability_scores=scores;}return kept;}
 let syncTimer=null;
 function queueSync(){if(syncTimer)clearTimeout(syncTimer);syncTimer=setTimeout(()=>{syncTimer=null;syncContract();},400);}
 async function syncContract(){const seq=++state.seq,typed=state.edited;state.busy=true;state.retry=null;draw();try{const value=await api('/prototype/wizard/contract',contractPayload());if(seq!==state.seq)return;const kept=typed===state.edited?{}:typedValues(value.draft||{});state.contract=value;if(!((value.dropped||[]).length))state.fitting=null;state.draft=Object.assign(merge(value.draft),kept);state.answers=Object.assign({},value.answers||state.answers);if(state.errorSource!=='submit'){state.error='';state.errorSource='';}if(Object.keys(kept).length){state.busy=false;await syncContract();return;}if(settle()){await syncContract();return;}persist();}catch(error){if(seq!==state.seq)return;state.error=error.message;state.errorSource='sync';state.retry=()=>syncContract();}finally{if(seq===state.seq){state.busy=false;draw();}}}
 function shell(){if(ui)return ui;const root=el('div');root.id='wizard';root.hidden=true;root.setAttribute('role','dialog');root.setAttribute('aria-modal','true');root.setAttribute('aria-labelledby','wizard-title');
  const box=el('div','','wizard-shell'),head=el('header','','wizard-head'),title=el('h1');title.id='wizard-title';
  // На 375 длинное имя экрана обрезалось многоточием: короткая подпись — «Герой».
  const titleLong=el('span','Создание героя','wizard-title-long'),titleShort=el('span','Герой','wizard-title-short');title.append(titleLong,titleShort);
  const eyebrow=el('p','СОЗДАНИЕ ГЕРОЯ','eyebrow'),ready=el('p','','wizard-ready'),close=el('button','Закрыть','wizard-close');ready.setAttribute('role','status');
  // A player often arrives with the hero already written down; reading that text
  // is local, and nothing it finds is applied without an explicit confirmation.
  const paste=el('button','Вставить лист','wizard-paste');paste.type='button';
  head.append(eyebrow,title,ready,paste,close);
  const body=el('div','','wizard-body'),nav=el('nav','','wizard-steps'),stage=el('main','','wizard-stage'),sheet=el('aside','','wizard-sheet');
  nav.setAttribute('aria-label','Шаги создания героя');stage.tabIndex=-1;sheet.setAttribute('aria-label','Лист героя');
  // «Шаг 2 из 6» текстом 14 px и точки: не полоса с процентами.
  const progress=el('p','','wizard-progress');progress.setAttribute('role','status');
  body.append(nav,stage,sheet);
  const foot=el('footer','','wizard-foot'),need=el('p','','wizard-need'),back=el('button','Назад'),sheetToggle=el('button','Лист','wizard-sheet-toggle'),next=el('button','Дальше','primary'),recommendButton=el('button','Рекомендовать','wizard-recommend'),spacer=el('span','','wizard-spacer');
  back.type=sheetToggle.type=next.type=recommendButton.type='button';
  need.setAttribute('role','status');need.hidden=true;
  recommendButton.title='Предложить законные варианты для незакрытых шагов. Ручные решения не стираются.';
  foot.append(need,spacer,sheetToggle,recommendButton,back,next);
  box.append(head,body,foot);root.append(box);document.body.append(root);
  ui={root,box,title,titleLong,titleShort,eyebrow,ready,paste,close,nav,progress,stage,sheet,back,next,need,sheetToggle,recommend:recommendButton};
  close.onclick=()=>shut(false);
  paste.onclick=protect(()=>{state.import=state.import?null:{text:'',result:null,busy:false,error:''};draw();});
  back.onclick=()=>{if(screenIndex()>0){state.screen=screenIndex()-1;persist();draw();}};
  next.onclick=protect(()=>{if(isLastScreen())return submit();state.screen=screenIndex()+1;persist();draw();});
  sheetToggle.onclick=()=>{const shown=root.classList.toggle('is-sheet');sheetToggle.setAttribute('aria-expanded',String(shown));};
  // The suggestion is an explicit user action, so the button that offers it must
  // actually call it: the footer drew «Рекомендовать» while nothing listened.
  recommendButton.onclick=protect(()=>recommend());
  root.addEventListener('keydown',trap);
  return ui;}
 function trap(event){if(!state.open)return;if(event.key==='Escape'){event.preventDefault();shut(false);return;}if(event.key!=='Tab')return;const nodes=focusable();if(!nodes.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
 function focusable(){if(!ui)return [];return Array.from(ui.root.querySelectorAll('button,input,select,textarea,summary,[href],[tabindex]:not([tabindex="-1"])')).filter(node=>!node.disabled&&!node.hidden&&node.offsetParent!==null);}
 function card(tag,extra){const node=el(tag,undefined,('wizard-card '+(extra||'')).trim());return node;}
 function chip(text,cls){return el('span',text,('wizard-chip '+(cls||'')).trim());}
 function drawSteps(){if(!ui)return;const list=screens(),index=screenIndex(),screen=list[index];ui.nav.replaceChildren();
  ui.progress.textContent=list.length?('Шаг '+(index+1)+' из '+list.length+' · '+screen.title):'Шаг';
  const ol=el('ol','','wizard-list');
  list.forEach((row,index2)=>{const item=el('li');if(index2===index)item.className='is-current';
   const done=row.steps.every(step=>!step.required||step.status==='complete');
   const button=el('button');button.type='button';
   button.append(chip(done?'✓':(index2===index?'●':'○'),'is-'+(done?'complete':(index2===index?'current':'empty'))),el('span',row.title||row.id,'wizard-step-title'));
   button.setAttribute('aria-current',index2===index?'step':'false');
   button.setAttribute('aria-label','Шаг '+(index2+1)+': '+row.title);
   button.onclick=()=>{state.screen=index2;persist();draw();};item.append(button);ol.append(item);});
  ui.nav.append(ui.progress,ol);}
 function drawSheet(){if(!ui)return;ui.sheet.replaceChildren();if(state.mode==='level'&&state.levelUp){drawDelta(ui.sheet);}
  const sheet=state.mode==='level'&&state.levelUp?state.levelUp.after:(state.contract&&state.contract.sheet);
  if(!sheet){ui.sheet.append(el('h2','Лист героя'),el('p',sheetReason(),'hint'));return;}
  const head=el('div','','wizard-sheet-head');head.append(el('h2',state.draft.name||'Без имени'),el('p',(chosenName('class')||state.draft.class_slug||'')+(chosenName('subclass')?' · '+chosenName('subclass'):'')+' · уровень '+String(state.draft.level),'hint'));ui.sheet.append(head);
  const vitals=el('div','','wizard-vitals');for(const [label,value] of [['ХП',sheet.hp_max],['КД',sheet.ac],['Скорость',sheet.speed?sheet.speed+' фт':'—'],['БМ',sheet.proficiency_bonus]]){const cell=el('div','','wizard-vital');cell.append(el('span',label,'wizard-vital-label'),el('strong',value===null||value===undefined||value===''?'—':String(value)));vitals.append(cell);}ui.sheet.append(vitals);
  const table=el('dl','','wizard-abilities');for(const row of sheet.ability_scores||[]){const value=row.score===null||row.score===undefined?'—':String(row.score);table.append(el('dt',ABILITY_TITLES[row.id]||row.name),el('dd',value+(row.modifier===null||row.modifier===undefined?'':' ('+(row.modifier>=0?'+':'')+row.modifier+')')));}ui.sheet.append(table);
  const facts=el('dl','','wizard-facts');for(const [label,value] of [['Спасброски',(sheet.save_proficiencies||[]).join(', ')],['Навыки',(sheet.skill_proficiencies||[]).join(', ')],['Компетентность',(sheet.skill_expertise||[]).join(', ')],['Оружие',(sheet.weapon_proficiencies||[]).join(', ')],['Доспехи',(sheet.armor_training||[]).join(', ')],['Пассивная внимательность',sheet.passive_perception]])facts.append(el('dt',label),el('dd',value===null||value===undefined||value===''?'—':String(value)));ui.sheet.append(facts);
  if((sheet.features||[]).length||(sheet.feats||[]).length){const box=el('div','','wizard-block');box.append(el('h3','Особенности'));for(const row of (sheet.features||[]).concat(sheet.feats||[]))box.append(el('p',row.name||row.id));ui.sheet.append(box);}
  const slots=sheet.spell_slots||{},pact=sheet.pact_slots||{};if(Object.keys(slots).length||Object.keys(pact).length){const box=el('div','','wizard-block');box.append(el('h3','Ячейки заклинаний'));for(const [level,count] of Object.entries(slots).sort((a,bb)=>Number(a[0])-Number(bb[0])))box.append(el('p','Уровень '+level+': '+count));for(const [level,count] of Object.entries(pact))box.append(el('p','Договор, уровень '+level+': '+count));ui.sheet.append(box);}
  const equipment=state.contract&&state.contract.equipment;if(equipment&&(equipment.grants||[]).length){const box=el('div','','wizard-block');box.append(el('h3','Снаряжение'),el('p','Выдано исходными комплектами. Золото: '+equipment.gold_gp+' зм.','hint'));for(const grantRow of equipment.grants)box.append(el('p',grantRow.label+' ('+grantRow.source+')'));for(const item of equipment.items)box.append(el('p',item.slug+(item.count>1?' × '+item.count:''),'hint'));if((equipment.unresolved||[]).length)for(const slug of equipment.unresolved)box.append(el('p','Нет документа предмета в корпусе: '+slug,'hint'));ui.sheet.append(box);}}
 // Why there is no sheet, in the engine's own words. Saying "choose species and
 // class" while both are already chosen is what made a blocked hero look like an
 // untouched form.
 function sheetReason(){const draft=state.draft||{};if(!draft.species_slug||!draft.class_slug)return 'Лист появится, когда выбраны вид и класс. Ничего не рассчитывается в браузере: числа присылает Nat20.';
  const issues=(state.contract&&state.contract.sheet_issues)||[];const reason=issues[0]?String(issues[0]):'';
  return 'Вид и класс выбраны, но лист пока не выводится: '+reason+' Числа считает Nat20, не браузер; исправьте шаг, о котором идёт речь, и лист появится сам.';}
 function drawDelta(host){const advance=state.levelUp;const box=el('div','','wizard-delta');box.append(el('h2','Уровень '+advance.from_level+' → '+advance.to_level));box.append(el('p','ХП +'+advance.hp_gain,'wizard-delta-hp'));for(const row of advance.granted_features||[])box.append(el('p','Новая особенность: '+(row.name||row.id)));for(const row of advance.granted_feats||[])box.append(el('p','Новая черта: '+(row.name||row.id)));for(const [level,count] of Object.entries(advance.granted_slots||{}))box.append(el('p','Ячейки уровня '+level+': '+count));box.append(el('p','Герой не меняется, пока вы не подтвердите.','hint'));host.append(box);}
 function optionCard(step,row,selected,onclick,{multi=false,disabled=false}={}){const node=card('button','wizard-option');node.type='button';node.disabled=disabled;node.dataset.id=String(row.id);node.setAttribute('role',multi?'checkbox':'radio');node.setAttribute('aria-checked',String(selected));if(selected)node.classList.add('is-chosen');const feat=featAbout(row.id);const name=feat?feat[0]:(row.name||row.id);node.append(el('span',name,'wizard-option-name'));const detail=feat?feat[1]:about(step,row);if(detail)node.append(el('span',detail,'wizard-option-detail'));if(row.disabled_reason)node.append(el('span',row.disabled_reason,'wizard-option-block'));node.setAttribute('aria-label',name+(detail?'. '+detail:''));node.onclick=protect(async()=>{if(disabled)return;await onclick();});return node;}
 // У экрана один вопрос; своя строка появляется только у второй части экрана.
 function partTitle(step,screen){if(step.id==='lineage')return 'Наследие';if(step.id==='subclass')return 'Подкласс';if(step.choice_key)return step.title||'Выбор';if(screen&&screen.steps.length>1&&!['species','class','background','scores','final','equipment'].includes(step.id))return step.title||'';return '';}
 function stageHead(step,lead){const box=el('div','','wizard-stage-head');box.append(el('h2',step.title||STEP_TITLES[step.id]||step.id));if(lead)box.append(el('p',lead,'hint'));return box;}
 function singleStep(step,field,apply){const grid=el('div','','wizard-grid');for(const row of step.options||[]){const chosen=(step.chosen||[]).includes(String(row.id).split(',')[0])||String(state.draft[field])===String(row.id);grid.append(optionCard(step,row,chosen,async()=>{await apply(row);}));}return grid;}
 async function chooseSpecies(row){const group=row;if(group.lineage_required){state.draft.species_base=group.id;if(!(group.variants||[]).some(v=>v.slug===state.draft.species_slug))state.draft.species_slug=null;}else{const only=(group.variants||[])[0];state.draft.species_base=group.id;state.draft.species_slug=only?only.slug:null;}state.advance=true;await syncContract();}
 async function chooseLineage(row){state.draft.species_slug=row.id;state.advance=true;await syncContract();}
 async function chooseClass(row){if(state.draft.class_slug!==row.id){state.draft.class_slug=row.id;state.draft.subclass_slug=null;state.draft.selected_choices=[];state.answers={};state.draft.choices={};state.draft.equipment_kits={};state.draft.kit_picks={};state.draft.prepared_spells=[];}state.advance=true;await syncContract();}
 async function chooseSubclass(row){state.draft.subclass_slug=state.draft.subclass_slug===row.id?null:row.id;state.advance=true;await syncContract();}
 async function chooseBackground(row){if(state.draft.background_slug!==row.id){state.draft.background_slug=row.id;for(const key of Object.keys(state.draft.equipment_kits))if(key.startsWith('background:'))delete state.draft.equipment_kits[key];}state.advance=true;await syncContract();}
 function drawScores(step){const host=el('div');
  const methods=el('div','','wizard-grid');for(const row of step.options||[])methods.append(optionCard(step,row,step.chosen_method===row.id,async()=>{
    // Numbers typed for one method are not numbers for another: the array is a set
    // of six printed values, the budget is a purchase. Carrying one into the other
    // leaves a remainder the new method cannot pay for, so the switch is explicit.
    if(state.draft.ability_score_method&&state.draft.ability_score_method!==row.id)state.draft.ability_scores={};
    state.draft.ability_score_method=row.id;if(row.id==='standard_array'&&!Object.keys(state.draft.ability_scores).length)state.draft.ability_scores={};await syncContract();}));host.append(methods);
  if(step.chosen_method){const budget=step.budget||{},line=el('p','','wizard-budget');if(budget.kind==='point_buy')line.textContent='Потрачено '+budget.spent+' из '+budget.total+' очков · осталось '+budget.left;else if(budget.kind==='standard_array')line.textContent='Осталось разложить: '+((budget.left||[]).join(', ')||'ничего');host.append(line,el('p','Число 15 даёт +2 на d20, 20 — +5: модификатор и есть прибавка к броску.','hint'));
   const grid=el('div','','wizard-scores');const bonuses=new Map((step.bonuses||[]).map(row=>[row.id,Number(row.bonus)||0]));
   // The array is a set of values to place, so a value can move: choosing a free value
   // fills the first empty score, and choosing a value already held by another score
   // exchanges the two. Nothing is typed to fix an order mistake, and a value can
   // never be duplicated or invented.
   const values=Object.assign({},state.draft.ability_scores||{});
   const free=new Map();for(const value of budget.array||[])free.set(value,(free.get(value)||0)+1);
   for(const value of Object.values(values))if(value!==null&&value!==undefined)free.set(value,(free.get(value)||0)-1);
   for(const ability of step.abilities||[]){const row=el('label','','wizard-score');row.append(el('span',ability.name,'wizard-ability-name'));
    const bonus=bonuses.get(ability.id)||0;const base=ability.value===null||ability.value===undefined?null:Number(ability.value);
    const input=el('input');input.type='number';input.min=String(ability.minimum||1);input.max=String(ability.maximum||20);
    input.value=base===null?'':String(base);input.setAttribute('aria-label',ability.name+(bonus?' база, без бонуса':''));
    // Clearing a score removes it from the draft. Sending null would be rejected as
    // an invalid integer, so the key is deleted instead of being set to empty.
    input.onchange=protect(async()=>{const raw=input.value.trim();state.edited++;if(raw==='')delete state.draft.ability_scores[ability.id];else state.draft.ability_scores[ability.id]=Number(raw);await syncContract();});
    row.append(input);
    if(bonus)row.append(el('span','+'+bonus+' от прошлого','hint'));
    const total=base===null?null:base+bonus;
    const modifier=total===null?null:Math.floor((total-10)/2);
    row.append(el('strong',total===null?'—':String(total),'wizard-total'));
    row.append(el('span',modifier===null?'':'модификатор '+(modifier>=0?'+':'')+modifier+' на d20','wizard-mod'));
    grid.append(row);}host.append(grid);
   if(step.swap&&(budget.array||[]).length){const picker=el('div','','wizard-grid');const chips=[];
    const redraw=()=>{for(const chip of chips){const left=free.get(chip.value)||0;chip.button.disabled=left<=0&&!Object.values(state.draft.ability_scores||{}).includes(chip.value);chip.button.classList.toggle('is-used',left<=0);}};
    for(const value of budget.array){const chipButton=el('button',String(value),'wizard-option');chipButton.type='button';
     chipButton.onclick=protect(async()=>{
      // A free value fills the first empty score; an occupied value swaps with the
      // score that holds it. The same gesture fixes both, so no order mistake locks.
      const holder=Object.keys(values).find(key=>values[key]===value);
      const spare=ABILITY_ORDER.find(key=>state.draft.ability_scores[key]===undefined);
      const target=spare||ABILITY_ORDER[0];
      if(holder===undefined){state.draft.ability_scores[target]=value;}
      else if(holder!==target){const other=state.draft.ability_scores[target];state.draft.ability_scores[holder]=other===undefined?undefined:other;if(other===undefined)delete state.draft.ability_scores[holder];state.draft.ability_scores[target]=value;}
      await syncContract();});
     chips.push({button:chipButton,value});picker.append(chipButton);}
    redraw();host.append(el('p','Нажмите значение, чтобы взять его. Занятое значение меняется местами с той характеристикой, куда вы его кладёте.','hint'),picker);
    const reset=el('button','Сбросить распределение');reset.type='button';reset.onclick=protect(async()=>{state.draft.ability_scores={};await syncContract();});host.append(reset);}}
  return host;}
 function drawChoice(step){const host=el('div');const chosen=new Set(step.chosen||[]);const limit=Number(step.count)||1;const answer=state.answers[step.choice_key];
  if(step.kind==='increase'||step.kind==='asi'){const cap=Number(step.cap)||1,points=Number(step.points)||Number(step.count)||1;const current=Object.assign({},answer&&typeof answer==='object'?answer:(step.increases||{}));const spent=Object.values(current).reduce((sum,value)=>sum+Number(value||0),0);host.append(el('p','Распределите прибавку: '+points+' очк., не больше '+cap+' в одну характеристику. Разложено '+spent+' из '+points+'.','hint'));const grid=el('div','','wizard-scores');for(const slug of step.options.map(row=>row.id)){const row=el('div','','wizard-score');row.append(el('span',ABILITY_TITLES[slug]||slug,'wizard-ability-name'));const value=el('strong',String(current[slug]||0));const plus=el('button','+','wizard-step-button'),minus=el('button','−','wizard-step-button');plus.type=minus.type='button';plus.onclick=protect(async()=>{if((current[slug]||0)>=cap||spent>=points)return;current[slug]=(current[slug]||0)+1;await answerChoice(step,current);});minus.onclick=protect(async()=>{if(!current[slug])return;current[slug]-=1;if(!current[slug])delete current[slug];await answerChoice(step,current);});row.append(minus,value,plus);
     // Предел на характеристику и конец очков видны кнопкой: раньше «+» нажимался и
     // молча ничего не делал, игрок читал это как «значения не раскладываются».
     plus.disabled=(current[slug]||0)>=cap||spent>=points;minus.disabled=!(current[slug]||0);grid.append(row);}host.append(grid);return host;}
  host.append(el('p','Отметьте '+limit+'. Осталось: '+Number(step.missing||0)+'.','hint'));const grid=el('div','','wizard-grid');const options=step.options||[];
  for(const row of options){const selected=chosen.has(String(row.id));grid.append(optionCard(step,row,selected,async()=>{const next=new Set(chosen);if(next.has(String(row.id)))next.delete(String(row.id));else{if(next.size>=limit)throw new Error('Можно выбрать не больше '+limit+'. Снимите другой вариант.');next.add(String(row.id));}await answerChoice(step,Array.from(next));},{multi:true}));}
  if(step.locked&&step.locked.length)host.append(el('p','Уже занято из другого источника: '+step.locked.join(', ')+'. Повторно потратить нельзя.','hint'));
  host.append(grid);return host;}
 async function answerChoice(step,value){state.answers[step.choice_key]=value;state.draft.choices[step.choice_key]=value;await syncContract();}
 function drawMagic(step){const host=el('div');const limits=step.limits||[];const caps={};for(const row of limits)caps[row.name]=Number(row.value);if(limits.length)host.append(el('p','Можно взять — '+limits.map(row=>row.name+': '+row.value).join(' · '),'hint'));const chosen=new Set(state.draft.prepared_spells||[]);
  const block=(title,rows,kind,cap)=>{const box=el('div','','wizard-block');const count=(rows||[]).filter(row=>chosen.has(row.id)).length;box.append(el('h3',title+(cap?' (выбрано '+count+' из '+cap+')':'')));const grid=el('div','','wizard-grid');for(const row of rows||[]){const picked=chosen.has(row.id);grid.append(optionCard(step,row,picked,async()=>{if(chosen.has(row.id))chosen.delete(row.id);else{if(cap&&count>=cap)throw new Error(title+': можно выбрать не больше '+cap+'. Снимите другой вариант.');chosen.add(row.id);}state.draft.prepared_spells=Array.from(chosen);await syncContract();},{multi:true,disabled:!picked&&Boolean(cap)&&count>=cap}));}box.append(grid);return box;};
  if((step.cantrips||[]).length)host.append(block('Заговоры',step.cantrips,'cantrip',caps['cantrips-known']));
  if((step.spells||[]).length)host.append(block('Заклинания',step.spells,'spell',caps['max-prepared']));
  if(!(step.cantrips||[]).length&&!(step.spells||[]).length)host.append(el('p','Для этого класса корпус не публикует ячейки на этом уровне, поэтому список пуст.','hint'));
  if(step.note)host.append(el('p',step.note,'hint'));return host;}
 function drawEquipment(host,step){const box=el('div','','wizard-equipment');if(step.note)box.append(el('p',step.note,'hint'));
  const plural=count=>count===1?'предмет':(count<5?'предмета':'предметов');
  for(const group of step.groups||[]){const fieldset=el('fieldset','','wizard-kits');
   // «Комплект: wizard» игроку ничего не говорит: источник назван по-русски.
   const who=String(group.id||'').startsWith('class:')?'от класса '+(chosenName('class')||''):String(group.id||'').startsWith('background:')?'от прошлого '+(chosenName('background')||''):String(group.title||'');
   fieldset.append(el('legend','Что взять '+who));
   const grid=el('div','','wizard-grid');
   for(const row of group.options||[]){const items=(row.items||[]).length;const node=card('button','wizard-option');node.type='button';node.setAttribute('role','radio');node.setAttribute('aria-checked',String(group.chosen===row.id));if(group.chosen===row.id)node.classList.add('is-chosen');
    // Буква варианта приходит в id, а name — это перечень предметов.
    const letter=String(row.id||'').replace(/[A-C]/g,match=>'АБВ'['ABC'.indexOf(match)]);
    node.append(el('span',items?('Набор '+(letter||'')):'Только золото','wizard-option-name'));
    const list=items?((row.name?row.name+', ':'')+items+' '+plural(items)+' и '+row.gold_gp+' золотых'):(row.gold_gp+' золотых — снаряжение купите сами');
    node.append(el('span',list,'wizard-option-detail'));
    if(row.recommended)node.append(chip('рекомендуем','is-complete'));
    node.onclick=protect(async()=>{state.draft.equipment_kits[group.id]=row.id;await syncContract();});grid.append(node);}
   fieldset.append(grid);const picks=group.picks||{};const chosenRow=(group.options||[]).find(row=>row.id===group.chosen);
   for(const pick of (chosenRow&&chosenRow.picks)||[]){const select=el('select');select.setAttribute('aria-label',pick.label);const empty=el('option','Выберите…');empty.value='';select.append(empty);for(const option of pick.options||[]){const item=el('option',option.name);item.value=option.slug;select.append(item);}select.value=picks[pick.group]||'';select.onchange=protect(async()=>{if(!state.draft.kit_picks[group.id])state.draft.kit_picks[group.id]={};state.draft.kit_picks[group.id][pick.group]=select.value;await syncContract();});fieldset.append(field(pick.label,select));}
   box.append(fieldset);}host.append(box);}
 function drawFinal(step,options){options=options||{};const host=el('div');
  if(state.mode!=='level'){const nameRow=el('label','','wizard-field');nameRow.append(el('span','Имя героя'));const name=el('input');name.type='text';name.maxLength=80;name.value=state.draft.name||'';name.setAttribute('autocomplete','off');name.oninput=()=>{state.draft.name=name.value;state.edited++;queueSync();};name.onchange=protect(async()=>{await syncContract();});nameRow.append(name);host.append(nameRow);
   const storyRow=el('label','','wizard-field');storyRow.append(el('span','О герое (необязательно)'));const story=el('textarea');story.rows=3;story.maxLength=4000;story.value=state.draft.backstory||'';story.oninput=()=>{state.draft.backstory=story.value;state.edited++;};story.onchange=protect(async()=>{await syncContract();});storyRow.append(story);host.append(storyRow,el('p','Текст уходит Мастеру отдельно от листа: он не выдаётся NPC автоматически.','hint'));}
  if(options.summary!==false)host.append(drawSummary());return host;}
 // Готово: чем герой получился, одной строкой — без таблиц и процентов.
 function drawSummary(){const box=el('div','','wizard-summary');const sheet=(state.contract&&state.contract.sheet)||null;
  const who=[chosenName('species')||state.draft.species_base||'',chosenName('lineage'),chosenName('class')||state.draft.class_slug||'',chosenName('background')].filter(Boolean);
  box.append(el('h3','Кем получился герой'),el('p',who.join(' · ')||'Герой ещё не собран','wizard-summary-who'));
  if(sheet){box.append(el('p','Хиты '+sheet.hp_max+' · Защита '+sheet.ac,'wizard-summary-vitals'));const scores=(sheet.ability_scores||[]).map(row=>ABILITY_TITLES[row.id]+' '+row.score+(row.modifier===null||row.modifier===undefined?'':' ('+(row.modifier>=0?'+':'')+row.modifier+' на d20)')).join(' · ');box.append(el('p',scores,'hint'));}
  const sheetButton=el('button','Лист героя','wizard-sheet-open');sheetButton.type='button';
  sheetButton.onclick=()=>{const shown=ui.root.classList.toggle('is-sheet');sheetButton.setAttribute('aria-expanded',String(shown));};
  box.append(sheetButton);
  return box;}
  // Reading a pasted sheet is local: the endpoint returns what the text states,
  // the lines it took each fact from, and every name that matched more than one
  // corpus entry. Applying it fills the draft; every limit is still checked by
  // the same contract the wizard always posts to.
  async function readSheet(text){state.import.busy=true;state.import.error='';draw();
   try{const result=await api('/prototype/wizard/import',{text});state.import.result=result;}
   catch(error){state.import.error=error.message||String(error);}
   finally{state.import.busy=false;draw();}}
 function drawImport(){const host=el('div','','wizard-import');host.append(stageHead({title:'Вставить готовый лист'},'Строки разбираются локально: значения не додумываются и не обрезаются, а исходный текст сохраняется целиком.'));
  const row=el('label','','wizard-field');row.append(el('span','Текст листа'));const area=el('textarea');area.rows=10;area.maxLength=20000;area.value=state.import.text||'';area.setAttribute('aria-label','Текст листа героя');area.oninput=()=>{state.import.text=area.value;read.disabled=!!state.import.busy||!area.value.trim();};row.append(area);host.append(row);
  const actions=el('div','','wizard-import-actions');const read=el('button',state.import.busy?'Читаем…':'Разобрать','primary');read.type='button';read.disabled=!!state.import.busy||!(state.import.text||'').trim();read.onclick=protect(()=>readSheet(state.import.text));const cancel=el('button','Отмена');cancel.type='button';cancel.onclick=protect(()=>{state.import=null;draw();});actions.append(read,cancel);host.append(actions);
  if(state.import.error)host.append(el('p',state.import.error,'wizard-reason'));
  const result=state.import.result;
  if(result){
   const box=el('div','','wizard-block');box.append(el('h3','Что нашлось в тексте'));
   for(const fact of result.fields||[])box.append(el('p',(STEP_TITLES[fact.field]||fact.field)+': '+String(fact.value)+' — «'+fact.line+'»','hint'));
   for(const fact of result.unknown||[])box.append(el('p',fact.reason+' («'+String(fact.value)+'»)','wizard-reason'));
   for(const fact of result.ambiguous||[])box.append(el('p','Нужно подтвердить: '+fact.field+' — «'+fact.line+'». Похожие: '+(fact.alternatives||[]).join(', '),'wizard-reason'));
   if(!(result.fields||[]).length)box.append(el('p','Ничего пригодного в тексте не нашлось. Исходный текст сохранён, значения можно выбрать вручную.','hint'));
   host.append(box);
   const apply=el('button','Заполнить мастер','primary');apply.type='button';apply.disabled=!result.draft;
   apply.onclick=protect(async()=>{if(!result.draft)return;state.source=result.source||state.import.text||'';state.draft=merge(result.draft);state.answers={};state.import=null;state.screen=0;await syncContract();});
   host.append(apply,el('p','Исходный текст остаётся в памяти мастера до подтверждения и не отправляется никуда, кроме этой формы.','hint'));}
  return host;}
 // A hero left in another adventure is not this hero. The screen says so and
 // offers to bring those answers here; nothing is applied until the player says.
 function drawOffer(){const host=el('div','','wizard-offer');const other=state.offer;
  host.append(el('h2','Есть незавершённый герой из другого приключения'),el('p','Он остался в своём приключении и сюда не переносится сам. Продолжить его или начать здесь чистого героя?','hint'));
  const who=(other.draft&&other.draft.name)?('Имя: '+other.draft.name):'Имя ещё не указано';
  host.append(el('p',who+' · '+String(other.adventure||'').replace('prepared:','приключение '),'wizard-option-detail'));
  const actions=el('div','','wizard-offer-actions');
  const take=el('button','Взять его ответы сюда','primary'),fresh=el('button','Начать здесь чистого героя');take.type=fresh.type='button';
  take.onclick=protect(async()=>{state.draft=merge(other.draft);state.answers=other.answers||{};state.screen=0;state.offer=null;persist();await syncContract();});
  fresh.onclick=protect(async()=>{state.offer=null;state.draft=blank();state.answers={};state.screen=0;persist();await syncContract();});
  actions.append(take,fresh);host.append(actions,el('p','Ответы другого приключения не удаляются, пока вы не подтвердите героя здесь.','hint'));
  return host;}
 function drawStage(){if(!ui)return;ui.stage.replaceChildren();ui.stage.setAttribute('aria-busy',String(state.busy));if(state.offer){ui.stage.append(drawOffer());return;}if(state.import){ui.stage.append(drawImport());return;}
  const list=screens(),screen=list[screenIndex()];if(!screen){ui.stage.append(el('p','Шагов пока нет: сервер ещё не вернул договор.','hint'));return;}
  const host=el('div','','wizard-screen');
  if(state.mode==='level'&&state.levelUp&&screenIndex()===0&&state.levelUp.from_level){const delta=el('div');drawDelta(delta);host.append(delta);}
  if(state.mode!=='level'&&screenIndex()===0)host.append(drawLevel());
  const head=el('div','','wizard-screen-head');head.append(el('h2',screen.question,'wizard-question'));if(screen.hint)head.append(el('p',screen.hint,'hint'));host.append(head);
  // На «Готово» имя стоит первым: поле, которое обязательно заполнить, не должно
  // прятаться под комплектами. Итог героя — в конце экрана.
  const ordered=screen.id==='gear'?[...screen.steps].sort((a,b)=>(a.id==='final'?-1:0)-(b.id==='final'?-1:0)):screen.steps;
  for(const item of reopened()){const box=el('div','','wizard-return');box.append(el('p','Шаг «'+item.screen.title+'» снова просит выбор: '+stepNeed(item.step).toLowerCase(),'hint'));const back2=el('button','Вернуться');back2.type='button';back2.onclick=protect(()=>{state.screen=item.index;persist();draw();});box.append(back2);host.append(box);}
  for(const step of ordered){const part=el('section','','wizard-part');part.dataset.step=step.id;if(step.choice_key)part.dataset.choice=step.choice_key;
   const label=partTitle(step,screen);if(label)part.append(el('h3',label,'wizard-part-title'));
   if(step.id==='species')part.append(singleStep(step,'species_slug',chooseSpecies));
   else if(step.id==='lineage')part.append(singleStep(step,'species_slug',chooseLineage));
   else if(step.id==='class')part.append(singleStep(step,'class_slug',chooseClass));
   else if(step.id==='subclass')part.append(singleStep(step,'subclass_slug',chooseSubclass));
   else if(step.id==='background')part.append(singleStep(step,'background_slug',chooseBackground));
   else if(step.id==='scores')part.append(drawScores(step));
   else if(step.id==='magic')part.append(drawMagic(step));
   else if(step.id==='equipment')drawEquipment(part,step);
   else if(step.id==='final')part.append(drawFinal(step,{summary:screen.id!=='gear'}));
   else if(step.choice_key)part.append(drawChoice(step));
   else part.append(stageHead(step));
   host.append(part);}
  if(screen.id==='gear')host.append(drawSummary());
  // A03/F01: предметы из подготовки Мастера видны на шаге снаряжения и остаются
  // у героя в новой истории. Снаряжение Nat20 при этом выбирается отдельно:
  // числа и владение считает движок, запись игрока не подменяет лист.
  if(screen.id==='gear'&&(state.carriedItems||[]).length){
    const names=state.carriedItems.map(row=>row&&row.name?row.name:row&&row.id?row.id:String(row));
    host.append(el('p','Из подготовки Мастера: '+names.length+' — '+names.join(', ')+'. Они останутся у героя в новой истории; снаряжение Nat20 выбирается ниже.','hint'));
  }
  if((state.recommended||[]).length){const note=el('p','Предложение заполнило незакрытые шаги. Ваши решения сохранены: '+state.recommended.length+' шт. Ничего не подтверждено — проверьте и подтвердите сами.','hint');note.classList.add('wizard-recommended');host.prepend(note);}
  if(state.error&&!(state.errorSource==='submit'&&(state.draft.name||'').trim())){const box=el('div','','wizard-error');box.append(el('p',state.error));const retry=el('button','Повторить');retry.type='button';retry.onclick=protect(()=>{const again=state.retry||syncContract;state.error='';state.errorSource='';state.retry=null;draw();return again();});box.append(retry);host.append(box);}
  if(state.busy)host.append(el('p','Проверяем шаг…','wizard-loading'));
  ui.stage.append(host);
  // Новый экран открывается сверху: иначе прокрутка прошлого шага остаётся,
  // и «Готово» начинается с середины комплектов.
  if(shownScreen!==screenIndex()){ui.stage.scrollTop=0;shownScreen=screenIndex();
   // Без этого фокус уезжал в body и Tab уводил из диалога: экран пересобран.
   const question=ui.stage.querySelector('.wizard-question');if(question){question.setAttribute('tabindex','-1');question.focus();}}}
 // Перерисовка не вкладывается в себя: снятие фокуса с поля ввода (blur → change →
 // syncContract → draw) приходит прямо во время replaceChildren и роняет дерево
 // («the node to be removed is no longer a child»). Второй вызов ждёт своей очереди.
 function draw(){if(!ui)return;if(state.drawing){state.queued=true;return;}state.drawing=true;
  try{do{state.queued=false;drawNow();}while(state.queued);}finally{state.drawing=false;}}
 function drawNow(){if(!ui)return;ui.root.hidden=!state.open;if(!state.open)return;
  if(state.advance&&!state.busy){state.advance=false;const screen=activeScreen();if(screen&&!screenNeed(screen)&&!isLastScreen()){state.screen=screenIndex()+1;persist();}}
  ui.titleLong.textContent=state.mode==='level'?'Повышение уровня':state.mode==='rebuild'?'Пересборка героя':'Создание героя';ui.titleShort.textContent=state.mode==='level'?'Уровень':state.mode==='rebuild'?'Пересборка':'Герой';ui.eyebrow.textContent=state.mode==='level'?'НОВЫЙ УРОВЕНЬ':state.mode==='rebuild'?'ГЕРОЙ ПО КНИГЕ':(state.prepared?'ГЕРОЙ ДЛЯ ПОДГОТОВЛЕННОГО МИРА':'НОВАЯ ИГРА');
  const blocking=(state.contract&&state.contract.blocking)||[];ui.ready.textContent=!state.contract?'Загружаем договор…':(contractReady()?'Готово':(((state.contract.confirm_issues||[])[0])||('Осталось шагов: '+blocking.length)));ui.ready.classList.toggle('is-loading',state.busy);
  const need=state.mode==='level'?levelNeed(activeScreen()):screenNeed(activeScreen());
  ui.need.textContent=need||'';ui.need.hidden=!need;
  ui.back.disabled=screenIndex()<=0;
  ui.next.textContent=isLastScreen()?(state.mode==='level'?'Подтвердить уровень':state.mode==='rebuild'?'Пересобрать героя':'Создать героя'):'Дальше';
  ui.next.disabled=Boolean(need)||state.busy;
  if(ui.recommend){ui.recommend.hidden=state.mode==='level';ui.recommend.disabled=state.busy;}
  ui.root.setAttribute('aria-busy',String(state.busy));drawSteps();drawStage();drawSheet();persist();}
 function contractReady(){const contract=state.contract;if(!contract)return false;return Boolean(contract.confirm_ready===undefined?contract.ready:contract.confirm_ready);}
 function confirmReady(){if(!state.contract||state.busy)return false;if(state.mode==='level'){for(const step of steps()){if(step.id==='final'||CREATION_ONLY.has(step.id))continue;if(Number(step.missing||0)>0)return false;}return !(state.contract.issues||[]).length;}return contractReady();}
 // An explicit suggestion, never an automatic choice. It fills only the steps
 // still owed, reports what it kept, and confirms nothing by itself.
 async function recommend(){if(state.busy||state.applied)return;state.busy=true;state.error='';draw();
  // Сборка на 8-м считается движком дольше: замер — 35 с на «Воине» 8-го уровня
  // (на первом — 14 с), а сорок пять секунд на живом сервере уже не хватало.
  try{const value=await deadline(api('/prototype/wizard/recommend',contractPayload()),150,'Сервер не ответил за 150 секунд: предложение не собралось. Нажмите «Рекомендовать» ещё раз или выберите шаги вручную.');
   if(!value.ok){state.error=value.reason||'Предложение не собралось.';}
   else{state.draft=merge(value.draft);state.answers={};state.recommended=(value.kept||[]).slice();await syncContract();}}
  catch(error){state.error=error.message;}
  finally{state.busy=false;draw();}}
 async function submit(){if(state.applied)return;if(state.mode!=='level'&&state.mode!=='rebuild'&&!(state.draft.name||'').trim()){state.error='Имя героя пустое: сервер не создаёт героя без имени. Впишите имя в поле выше.';state.errorSource='submit';state.retry=()=>submit();draw();const focus=ui&&ui.stage.querySelector('.wizard-field input[type=text]');if(focus)focus.focus();return;}if(!confirmReady())return;state.applied=true;state.busy=true;state.error='';state.retry=null;draw();try{if(state.mode==='level')await submitLevel();else if(state.mode==='rebuild')await submitRebuild();else await submitCreate();}catch(error){state.error=error.message;state.errorSource='submit';state.retry=()=>submit();state.applied=false;}finally{state.busy=false;draw();}}
 function pendingRecord(){try{return JSON.parse(localStorage.getItem(PENDING));}catch(e){return null;}}
 // Уровень героя — параметр сборки, а не подпись: значение уходит в черновик
 // (`draft.level`), и сервер пересчитывает под него подкласс, повышения
 // характеристик, заклинания всех уровней и ячейки. Границы уровней и имя книги
 // приходят из определения мира (`world-readiness`), а не придумываются экраном.
 function levelBounds(){const world=state.world||{};const min=Math.min(Math.max(Number(world.minimum_level)||1,1),20);const max=Math.min(Math.max(Number(world.maximum_level)||20,min),20);return {min,max,rec:Number(world.hero_level)||null};}
 function levelNote(){const world=state.world;if(!world)return 'Свободная игра: уровень героя от 1 до 20.';const rawMin=Number(world.minimum_level),rawMax=Number(world.maximum_level),book=String(world.book||'').trim(),who=book?('Книга «'+book+'»'):'Книга';if(!rawMin||!rawMax)return who+' уровень не назвала: герой с 1-го по 20-й.';const {min,max}=levelBounds();if(min===max)return who+' рассчитана на '+min+'-й уровень.';return who+' рассчитана на уровни с '+min+'-го по '+max+'-й.';}
 // Книга без рекомендации оставляет уровень 1: выдумывать требование нельзя.
 async function loadWorld(){const id=state.prepared?state.prepared.draft_id:state.sourceDraft;state.world=null;if(!id)return null;
  try{const world=await api('/creation/drafts/'+id+'/world-readiness');if(!world||typeof world!=='object')return null;state.world=Object.assign({draft_id:id},world);
   const bounds=levelBounds();const level=Number(state.draft.level)||1;
   if(level<bounds.min||level>bounds.max){const next=Math.min(Math.max(bounds.rec||bounds.min,bounds.min),bounds.max);if(next<level)state.fitting=level;state.draft.level=next;await syncContract();}
   // Книга приходит после первой отрисовки, и без этого шага экран показывал бы
   // «Свободную игру», пока уровень не поменяется: подпись книги — часть того
   // же договора, а не отдельный запрос экрана.
   draw();
   return state.world;}catch(error){return null;}}
 function drawLevel(){const host=el('section','','wizard-part wizard-level');host.dataset.step='level';host.append(el('h3','Уровень героя','wizard-part-title'),el('p',levelNote(),'hint'));
  const bounds=levelBounds(),row=el('label','','wizard-field');row.append(el('span','Уровень героя'));
  const select=el('select');select.id='wizard-level';select.setAttribute('aria-label','Уровень героя');
  for(let level=bounds.min;level<=bounds.max;level++){const option=el('option',String(level));option.value=String(level);select.append(option);}
  select.value=String(Number(state.draft.level)||bounds.min);
  select.onchange=protect(async()=>{const was=Number(state.draft.level)||bounds.min;state.draft.level=Math.min(Math.max(Number(select.value)||bounds.min,bounds.min),bounds.max);if(state.draft.level<was)state.fitting=was;state.error='';state.errorSource='';await syncContract();});
  row.append(select);host.append(row);
  if(bounds.min===bounds.max&&Number(state.draft.level)!==bounds.min)host.append(el('p','Книга рассчитана на '+bounds.min+'-й уровень, выбран '+String(state.draft.level)+'-й.','wizard-reason'));
  const dropped=(state.contract&&state.contract.dropped)||[];
  if(dropped.length)host.append(el('p','Уровень ниже прежнего — сняты выборы: '+dropped.map(item=>String(item.title||item.key)+(item.level?(' ('+item.level+' ур.)'):'')).join(', ')+'.','wizard-reason'));
  return host;}
 async function worldReady(){if(!state.prepared)return null;const world=await api('/creation/drafts/'+state.prepared.draft_id+'/world-readiness');if(!world.ready||!world.world_ready||world.run_id!==state.prepared.run_id)throw new Error('Мир изменился. Вернитесь к материалам и подтвердите его заново.');return world;}
 async function submitCreate(){const world=await worldReady();const build=state.contract.build;if(!build)throw new Error('Сервер не вернул собранного героя.');const revision=world?world.revision:(state.prepared?state.prepared.revision:null);
  const payload={name:(state.draft.name||'').trim(),build,prepared_spells:state.draft.prepared_spells||[],adventure:state.prepared?('prepared:'+state.prepared.draft_id):'sandbox',description:'',encounter_monsters:[],mechanics_ruleset:'dnd2024',rules_confirmed:true,hero_confirmed:true,background_confirmed:true,hero_background:(state.draft.backstory||'').slice(0,4000)};
  if(state.prepared)payload.adventure_revision=revision;
  // The book read before the hero step belongs to this start, not to the screen
  // that uploaded it: the campaign is created with the draft that holds it.
  if(state.sourceDraft)payload.preparation_draft=state.sourceDraft;
  // A03/F01: the four parts of the master's draft go to the server explicitly.
  // The server checks them against the saved draft before anything is created.
  if(state.moduleId)payload.module_id=state.moduleId;
  if(state.heroDraft)payload.hero_draft=state.heroDraft;
  if((state.carried||[]).length)payload.equipment=state.carried;
  // Starting a campaign is the one action that advances a prepared world, so a
  // re-send after a lost response would otherwise carry a new revision, miss the
  // server's idempotency record and start a second game (or be refused). A retry
  // of the same prepared world therefore resends the exact stored body, and a
  // deliberate change of hero or text still produces a new request.
  const old=pendingRecord();
  const same=row=>Boolean(row&&row.payload&&row.signature)&&row.signature===JSON.stringify(payload)&&row.adventure===payload.adventure&&row.run_id===(state.prepared?state.prepared.run_id:null);
  let body;
  if(same(old)){body=JSON.parse(old.payload);}
  else{const requestId=crypto.randomUUID();body=Object.assign({},payload,{request_id:requestId});
   localStorage.setItem(PENDING,JSON.stringify({signature:JSON.stringify(payload),request_id:requestId,adventure:payload.adventure,run_id:state.prepared?state.prepared.run_id:null,payload:JSON.stringify(body)}));}
  let created;try{created=await api('/prototype/campaigns',body);}catch(error){if(error.status&&error.status<500)localStorage.removeItem(PENDING);throw error;}campaignId=created.id;if(created.notice)toast(created.notice);localStorage.setItem('solo-campaign',campaignId);localStorage.removeItem(KEY);localStorage.removeItem(PENDING);await window.refresh();view('play');shut(true);}
 async function submitLevel(){const advance=state.levelUp;const pending=pendingRecord();const reuse=pending&&pending.campaign===advance.campaign?pending:null;const requestId=reuse?reuse.request_id:crypto.randomUUID();localStorage.setItem(PENDING,JSON.stringify({campaign:advance.campaign,request_id:requestId}));
  const result=await api('/campaigns/'+advance.campaign+'/level-up',{request_id:requestId,expected_version:advance.version,draft:state.contract.draft,answers:state.answers});localStorage.removeItem(PENDING);campaignId=advance.campaign;localStorage.setItem('solo-campaign',campaignId);await window.refresh();view('play');toast('Уровень '+result.result.to_level+' подтверждён.');shut(true);}
 // L02: «Пересобрать героя» на экране сверки. Сборку считает сервер (тот же
 // рабочий процесс, что и обычный старт), а кампания остаётся прежней: книга,
 // сцена и её бои не пересобираются, меняется только герой. Ход не пишется —
 // до первого броска пересборка бесплатна.
 async function submitRebuild(){const build=state.contract.build;if(!build)throw new Error('Сервер не вернул собранного героя.');const cid=state.rebuildCampaign||campaignId;if(!cid)throw new Error('Не найдена кампания, в которой пересобирается герой.');await worldReady();
  const result=await api('/campaigns/'+cid+'/level-gate/rebuild',{request_id:crypto.randomUUID(),build,prepared_spells:state.draft.prepared_spells||[],name:(state.draft.name||'').trim()});
  campaignId=cid;localStorage.setItem('solo-campaign',cid);localStorage.removeItem(KEY);await window.refresh();view('play');toast('Герой пересобран на '+(result.hero&&result.hero.level?result.hero.level:build.level)+'-й уровень.');shut(true);}
 function shut(done){if(!state.open)return;state.open=false;state.applied=false;if(ui){ui.root.hidden=true;ui.root.classList.remove('is-sheet');}if(restoreFocus&&restoreFocus.isConnected)restoreFocus.focus();if(!done)persist();}
 async function start(mode,options){options=options||{};state.mode=mode;state.applied=false;state.advance=false;state.error='';state.import=null;restoreFocus=document.activeElement;
  // Every start resets the screen first, so a late answer from the previous hero
  // cannot paint itself onto the new one.
  state.contract=null;state.seq++;state.busy=false;state.source='';state.edited=0;state.retry=null;
  if(mode==='level'){state.levelUp={campaign:options.campaign};state.prepared=null;state.draft=blank();state.answers={};state.screen=0;state.offer=null;}
  // L02: пересборка героя по просьбе Мастера. Это обычная сборка, но книга уже
  // связана с кампанией, уровень берётся из её требования, а подтверждение
  // заменяет героя в этой же кампании — второй кампании не появляется.
  else if(mode==='rebuild'){state.rebuildCampaign=options.rebuildCampaign||'';state.prepared=options.prepared||null;state.sourceDraft='';state.levelUp=null;state.world=null;state.fitting=null;state.draft=merge(null);state.answers={};state.screen=0;state.offer=null;const asked=Number(options.level)||0;if(asked>=1&&asked<=20)state.draft.level=asked;state.heroDraft=null;state.carried=[];state.carriedItems=[];state.moduleId='';state.book=options.prepared&&options.prepared.book?options.prepared.book:null;}
  // Opening the same adventure again continues the hero the player already
  // drafted; recall() compares adventure, mode and schema, so a draft from
  // another game is never applied here. Only an explicit fresh start skips it.
  else{state.prepared=options.prepared||null;state.sourceDraft=options.preparationDraft||'';state.levelUp=null;state.world=null;state.fitting=null;const saved=options.fresh?null:recall();state.draft=merge(saved&&saved.draft);state.answers=(saved&&saved.answers)||{};state.screen=(saved&&Number.isInteger(saved.screen)?saved.screen:0)||0;state.offer=options.fresh?otherDraft():null;if(options.prepared){state.draft.name=state.draft.name||'';}
   // A03/F01: герой, старт и предметы из подготовки Мастера приходят в редактор
   // по именам, а не «сами собой»: имя и предыстория подставлены, предметы
   // показаны на шаге снаряжения и уходят в запуск тем же черновиком.
   state.heroDraft=options.heroDraft||null;state.carried=Array.isArray(options.equipment)?options.equipment.slice():[];state.carriedItems=Array.isArray(options.equipmentItems)?options.equipmentItems.slice():[];state.moduleId=options.module_id||'';state.book=options.book||null;
   if(state.heroDraft){const hero=state.heroDraft;if(!(state.draft.name||'').trim()&&hero.name)state.draft.name=String(hero.name);if(!(state.draft.backstory||'').trim()&&hero.background)state.draft.backstory=String(hero.background);const level=Number(hero.level);if(Number.isInteger(level)&&level>=1&&level<=20&&Number(state.draft.level||1)===1)state.draft.level=level;}}
  shell();state.open=true;ui.root.classList.remove('is-sheet');draw();ui.root.hidden=false;
  try{
   if(mode==='level'){const data=await api('/campaigns/'+options.campaign+'/level-up');if(!data.available)throw new Error(data.reason||'Повышение уровня недоступно.');state.levelUp=Object.assign({campaign:options.campaign},data);state.contract=data.contract;state.draft=merge(data.contract.draft);state.answers={};state.screen=0;state.busy=false;state.error='';draw();const focus=ui.stage.querySelector('button,input,select');if(focus)focus.focus();return true;}
   await syncContract();await loadWorld();const focus=ui.stage.querySelector('button,input,select');if(focus)focus.focus();return true;
  }catch(error){state.error=error.message;state.contract=null;draw();return false;}}
 window.soloWizard={open:options=>{options=options||{};return start(options.levelUp?'level':options.rebuildCampaign?'rebuild':'create',options);},levelUp:campaign=>start('level',{campaign}),close:()=>shut(false),goto:target=>{const list=screens();const index=list.findIndex(row=>row.id===target||row.steps.some(step=>step.id===target||step.choice_key===target));if(index<0)return false;state.screen=index;draw();return true;},screens:()=>screens().map(row=>({id:row.id,title:row.title,steps:row.steps.map(step=>step.id)})),screen:()=>screenIndex(),state:()=>({mode:state.mode,draft:state.draft,answers:state.answers,contract:state.contract,levelUp:state.levelUp,world:state.world,error:state.error,import:state.import,source:state.source,rebuildCampaign:state.rebuildCampaign,screen:screenIndex(),screens:screens().map(row=>({id:row.id,title:row.title,steps:row.steps.map(step=>step.id)}))})};
 window.soloOpenPreparedNat20=async selection=>{await start('create',{prepared:selection});};
 window.soloOpenNewGame=()=>start('create',{prepared:null});
})();
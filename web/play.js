'use strict';
const $=id=>document.getElementById(id);
// A chat-only shell (?ui=chat) loads no panels, so the controls and slots the
// panels own are absent. This file runs top-down, and one throw on a missing
// node would leave the feed empty, so those bindings go through ctl(): it
// returns the element or, only inside the chat shell, a stand-in that drops
// the work. In the full page a missing id still throws instead of hiding a typo.
function ctl(id){const node=$(id);if(node)return node;
 if(document.documentElement.dataset.ui!=='chat')throw new Error('Элемент #'+id+' отсутствует в полном интерфейсе');
 return {append(){},replaceChildren(){},classList:{toggle(){}},set onclick(v){},set onsubmit(v){},set onchange(v){},set oninput(v){}};}
const abilities={strength:'Сила',dexterity:'Ловкость',constitution:'Телосложение',intelligence:'Интеллект',wisdom:'Мудрость',charisma:'Харизма'};
let campaignId=localStorage.getItem('solo-remember-campaign')==='off'?null:localStorage.getItem('solo-campaign'),current=null,busy=false,streamController=null,profiles=[],editingEvent=null,lastUIRender=null;
let pendingSend=null;try{pendingSend=JSON.parse(localStorage.getItem('solo-pending-send'));}catch(e){}
let historyRestoreRequested=false,historyViewCampaign=null;
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
// n13: где лежат файлы. На телефоне «на этом телефоне», на столе «на этом
// компьютере» — одно слово, но неверное слово здесь читается как ошибка.
function hereWord(){return matchMedia('(max-width:760px)').matches?'телефоне':'компьютере';}
function field(name,input){const label=el('label',name);label.append(input);return label;}
function toast(text){($('toast')||{}).textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>{$('toast').hidden=true;},7000);}
function protect(fn){return async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};}
// Отказ формы приходил англоязычным списком pydantic, и экран показывал одно и то же
// «Не удалось обработать запрос.» Игрок не знал, какое поле сервер не принял.
const FIELD_NAMES={name:'имя героя',draft:'черновик героя',answers:'ответы шагов',build:'лист героя',hero:'герой',text:'текст',adventure:'приключение',request_id:'номер запроса',prepared_spells:'заклинания',ability_scores:'характеристики',selected_choices:'выборы класса',backstory:'предыстория','master.literary.rhythm':'темп повествования','master.literary':'художественный стиль','master.profile_name':'имя профиля','master.literary.detail':'подробность','master.literary.emotion':'эмоциональность','master.literary.dialogue':'диалоги','master.literary.imagery':'образность','master.literary.initiative':'инициативность подачи','master.literary.surprises':'сюжетные неожиданности','master.literary.freedom':'фокус мира','master.literary.tension':'атмосфера','master.literary.pace':'темп','master.literary.npc_autonomy':'самостоятельность NPC','master.literary.focus':'акцент','master.literary.consequences':'подача последствий'};
const VALIDATION_TEXTS={string_too_short:'пустое или слишком короткое значение',string_too_long:'слишком длинный текст',missing:'поле не передано',dict_type:'ожидается набор «ключ → значение»',list_type:'ожидается список',int_parsing:'нужно целое число',int_type:'нужно целое число',float_parsing:'нужно число',literal_error:'недопустимое значение',bool_parsing:'нужно «да» или «нет»',extra_forbidden:'лишнее поле',string_type:'ожидается текст',value_error:'значение не принято'};
function validationReason(detail){if(!Array.isArray(detail)||!detail.length)return '';const parts=[];for(const row of detail.slice(0,3)){const field=(row.loc||[]).filter(x=>x!=='body'&&x!=='query'&&x!=='path').join('.')||'запрос';const named=FIELD_NAMES[field]||FIELD_NAMES[field.split('.')[0]]||field;parts.push(named+' — '+(VALIDATION_TEXTS[row.type]||row.msg||'значение не принято'));}return 'Сервер не принял запрос: '+parts.join('; ')+'.';}

// n02: один разбор отказа на весь интерфейс. Сервер называет причину в
// `detail` — строкой или объектом {code,message}. Игроку печатается message,
// код остаётся в журнале (телеграфия пишет тело каждого ответа с кодом ≥400),
// а «Не удалось обработать запрос» — только для ответа без причины вообще:
// текст вместо JSON, пустое тело, незнакомый формат.
function refusalOf(status,raw){
 let data=null;try{data=raw?JSON.parse(raw):null;}catch(parse){data=null;}
 const detail=data&&data.detail,object=detail&&typeof detail==='object'&&!Array.isArray(detail);
 const code=(object&&detail.code)||(data&&data.code)||'';
 const message=(typeof detail==='string'?detail.trim():'')
  ||(object&&typeof detail.message==='string'?detail.message.trim():'')
  ||(data&&typeof data.message==='string'?data.message.trim():'')
  ||validationReason(detail);
 return {code,message:message||('Не удалось обработать запрос: сервер ответил кодом '+status+' и не назвал причину. Повторите; если повторяется — сообщите время и шаг.')};
}
async function api(path,body,method=body===undefined?'GET':'POST',absolute){let r;try{r=await fetch((absolute||'/api')+path,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});}catch(network){ // Браузер отдаёт здесь английское «Failed to fetch», и экран молчал о причине.
 const error=new Error('Нет связи с приложением: сервер не ответил. Проверьте, что приложение запущено, и повторите; введённое не потеряно.');error.offline=true;error.cause=network;throw error;}
 // Тело читается текстом: отказ бывает не JSON (чужой Host, страница шлюза), а
 // r.json() на нём бросал английский SyntaxError вместо причины.
 const raw=await r.text();let data=null;try{data=raw?JSON.parse(raw):null;}catch(parse){data=null;}
 if(!r.ok){const refusal=refusalOf(r.status,raw),error=new Error(refusal.message);error.status=r.status;error.code=refusal.code;
  if(refusal.code)console.info('[solo] отказ',r.status,refusal.code,path);
  // 404 on our own /api route means the running process predates the page, not
  // that the player did something wrong. The editor cannot work against an API
  // that never had the route, so the reason is named and repair is offered.
  // Если сервер сам назвал причину кодом — она и остаётся, догадка не нужна.
  if(r.status===404&&!refusal.code&&String(path).startsWith('/')&&!/^\/(campaigns|creation)\/[^/]+$/.test(path)){error.staleServer=true;error.message='Интерфейс новее запущенного сервера: маршрут '+path+' отсутствует в текущем процессе. Перезапустите приложение; ваши сохранения не пострадают.';}
  throw error;}
 if(method==='POST'&&path.startsWith('/campaigns/')&&path.includes('/restore/'))historyRestoreRequested=path.split('/')[2];return data===null&&raw?raw:data;}
function needCampaign(){if(!current)throw new Error('Откройте или создайте кампанию.');}
function view(name){document.querySelectorAll('.view').forEach(e=>e.classList.toggle('active',e.id===name));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.view===name));if(name==='campaigns')protect(loadCampaigns)();if(name==='performance')protect(loadPerformance)();if(name==='master')protect(async()=>{await loadProfiles();await loadMasterHistory();})();}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));
// r223: тема одна на всё приложение — dark | light. Старые значения
// (obsidian/parchment/minimal) больше не существуют: они приводятся к dark,
// чтобы у игравших остался рабочий экран, а не пустая палитра. Ключ
// localStorage прежний ('solo-theme') — выбор не теряется.
const THEME_KEY='solo-theme';
function applyTheme(value){const theme=value==='light'?'light':'dark';document.documentElement.dataset.theme=theme;localStorage.setItem(THEME_KEY,theme);const select=$('theme');if(select)select.value=theme;document.querySelectorAll('.themetoggle').forEach(b=>{b.setAttribute('aria-pressed',theme==='light'?'true':'false');b.setAttribute('aria-label',theme==='light'?'Тёмная тема':'Светлая тема');});window.soloTheme=theme;}
const storedTheme=localStorage.getItem(THEME_KEY);
applyTheme(storedTheme==='light'||storedTheme==='dark'?storedTheme:(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'));
const themeChoice=$('theme');if(themeChoice){themeChoice.onchange=()=>applyTheme(themeChoice.value);}
document.addEventListener('click',event=>{const target=event.target;const button=target&&target.closest?target.closest('.themetoggle'):null;if(!button)return;event.preventDefault();applyTheme(document.documentElement.dataset.theme==='light'?'dark':'light');});
const effectChoice=$('effects');if(effectChoice){effectChoice.checked=localStorage.getItem('solo-effects')!=='off';document.body.classList.toggle('effects',effectChoice.checked);effectChoice.onchange=()=>{document.body.classList.toggle('effects',effectChoice.checked);localStorage.setItem('solo-effects',effectChoice.checked?'on':'off');};}
function markBusy(value,label='Мастер отвечает…'){busy=value;$('send').disabled=value;($('story-status')||{}).textContent=value?label:'Мастер ждёт вашей реплики';}
async function refresh(){if(!campaignId)return;current=await api('/campaigns/'+campaignId);try{render()}catch(e){console.error('RENDER_FAIL',e&&e.stack||e);throw e}}
function message(who,text,cls='master',undone=false){const box=el('article',undefined,'message '+cls+(undone?' undone':''));box.append(el('div',who+(undone?' · ХОД ОТМЕНЁН':''),'speaker'),el('div',text,'prose'));return box;}
function fallback(results){return results.map(r=>r.type==='check'?(r.formula||r.text||'Результат проверки не получен.'):['unsupported','needs_context'].includes(r.type)?r.reply:r.type==='travel_stop'?r.text:r.type==='move'?'Путь пройден.':r.type==='deferred'?'Это намерение пока не исполнено.':r.type==='halted'?'Последующие шаги ещё не выполнялись.':r.type==='denied'?'Это действие не совершено.':r.text||'').join('\n');}
function render(){
 const start=performance.now(),state=current.state,hero=state.hero,place=state.locations.find(p=>p.name===state.location);
 const chat=$('chat'),restored=historyRestoreRequested===current.id,newCampaign=historyViewCampaign!==current.id;
 const previousTop=chat.scrollTop,following=chat.scrollHeight-chat.clientHeight-previousTop<32;
 const anchor=[...chat.querySelectorAll('[data-message-key]')].find(node=>node.getClientRects().length&&node.getBoundingClientRect().bottom>chat.getBoundingClientRect().top);
 const anchorKey=anchor?.dataset.messageKey,anchorOffset=anchor?anchor.getBoundingClientRect().top-chat.getBoundingClientRect().top:0;
 const openArchives=new Set([...chat.querySelectorAll('.undone-history[open]')].map(node=>node.dataset.archiveKey));
 historyRestoreRequested=false;historyViewCampaign=current.id;
 ($('campaign-label')||{}).textContent=current.name;($('chapter-label')||{}).textContent=(state.preparation&&state.preparation.library&&state.preparation.library.title)||'';($('place-label')||{}).textContent=place.name;($('scene-title')||{}).textContent=place.name;($('scene-description')||{}).textContent=place.status||place.public_scene||place.description;($('turn')||{}).textContent='Ход '+state.turn;
 let entry=$('entry-readiness');if(!entry){entry=el('p',undefined,'hint');entry.id='entry-readiness';entry.setAttribute('role','status');const anchor=$('scene-description');if(anchor)anchor.after(entry);else{$('action-form')?.before(entry);}}entry.hidden=!state.adventure?.entry_readiness;entry.textContent=state.adventure?.entry_readiness?.boundary||'';
 ($('hero-summary')||{}).textContent=hero.name+(current.master.hide_mechanics?' · история сохраняется':' · '+hero.hp+'/'+hero.max_hp+' хитов · история сохраняется');$('scene-risk').hidden=!current.master.declared_lethal_risk;
 ($('hero-details')||{}).textContent=hero.name+' · уровень '+hero.level+' · хиты '+hero.hp+'/'+hero.max_hp+' · КБ '+hero.ac;
 ctl('abilities').replaceChildren();for(const[k,v]of Object.entries(hero.abilities)){const m=Math.floor((v-10)/2);ctl('abilities').append(el('div',abilities[k]+': '+v+' ('+(m>=0?'+':'')+m+')','ability'));}
 $('chat').replaceChildren();
 // The opening belongs to the GM: the scene description is what the player is
 // told, never something their hero "said". Exactly one copy is rendered here,
 // and game-screen.js no longer adds a second one.
 // B1: the opening is the first thing the master said, so it stays at the
 // top of the feed for the whole adventure instead of vanishing after turn one.
  const openingText=[place.public_scene||place.description,...(place.facts||[])].filter(Boolean).join('\n\n');
 // B1: exactly one copy. The timeline already carries the master's opening
 // once it is recorded, so the header copy is added only before that.
 // A setup event ('lantern-install', 'prepared-world') prints the scene text as
 // its own MASTER line through fallback(results), so adding the header copy too
 // showed the opening twice. One source: the setup event.
  const setupScene=(current.history||[]).find(h=>['prepared-world','lantern-install'].includes(String(h.request_id||''))&&(h.results||[]).some(r=>r&&r.kind==='scene_install'));
  if(!setupScene&&openingText.trim()&&![...current.history,...(current.conversation||[])].some(h=>String(h.narrative||h.reply||'').includes(openingText.slice(0,40))))
   $('chat').append(message('МАСТЕР',openingText,'master'));

 const timeline=[...current.history.map(value=>({type:'event',value})),...(current.gm_history||[]).map(value=>({type:'gm',value})),...(current.conversation||[]).map(value=>({type:'conversation',value}))].sort((a,b)=>a.value.created-b.value.created);
 let archive=null;
 // A conversational turn is stored twice: as a journal event and as a
 // conversation row pointing at it through journal_id. The server marks the
 // linked row with journal_event, and the journal pair already shows the same
 // accepted turn, so the reader must not print it a second time.
 const shownEvents=new Set(timeline.filter(x=>x.type==='event').map(x=>String(x.value.id)));
 for(const{type,value:h}of timeline){
  if(type==='conversation'&&h.journal_event&&shownEvents.has(String(h.journal_event)))continue;
  if(h.undone){if(!archive){archive=el('details',undefined,'undone-history');archive.dataset.archiveKey=type+':'+h.id;archive.open=!restored&&!newCampaign&&openArchives.has(archive.dataset.archiveKey);archive.append(el('summary','Отменённая ветка · не влияет на текущую сцену'));chat.append(archive);}}else archive=null;
  const target=archive||chat,key=type+':'+h.id;
 // A setup record ('prepared-world', 'lantern-install') carries the scene text, but
 // it is not something the hero said: signing it with the hero's name rendered the
 // opening twice, once "spoken" by the player. Its MASTER line still renders below.
 const setup=type==='event'&&['prepared-world','lantern-install'].includes(String(h.request_id||''));
 if(!setup){const player=message(type==='gm'?'ИГРОК · ВНЕ РОЛИ':hero.name.toUpperCase(),h.original||h.question,'player',Boolean(h.undone));player.dataset.messageKey=key+':player';target.append(player);}
  if(type==='conversation'){const m=message('МАСТЕР',h.reply,'master',Boolean(h.undone));m.dataset.messageKey=key+':master';target.append(m);continue;}
  if(type==='gm'){const m=message('МАСТЕР · ВНЕ РОЛИ',h.answer||'Вопрос сохранён.','gm',Boolean(h.undone));m.dataset.messageKey=key+':master';if(!h.undone&&h.status!=='complete'){const b=el('button','Получить ответ','quiet');b.onclick=protect(()=>narrateGM(h.id));m.append(b);}target.append(m);continue;}
  const m=message('МАСТЕР',h.narrative||fallback(h.results),'master',Boolean(h.undone));m.dataset.event=h.id;
  m.dataset.messageKey=key+':master';
  if(h.narrative_data?.presentation_only&&localStorage.getItem('solo-uncertainty-notes')==='on')m.append(el('small','Художественная подача: новые сведения не изменяют мир автоматически.','hint'));
  if(h.error)m.append(el('p','Описание остановлено или не завершено.','hint'));target.append(m);
  for(const proposal of h.undone?[]:h.narrative_data?.proposals||[]){const card=el('div',undefined,'proposal');card.append(el('p','Возможное новое сведение: '+proposal.text));if(proposal.status==='pending'){const yes=el('button','Принять в историю'),no=el('button','Отклонить');for(const[b,approve]of[[yes,true],[no,false]])b.onclick=protect(async()=>{if(busy)throw new Error('Дождитесь ответа.');await api('/events/'+h.id+'/proposals/'+proposal.id,{approve});await refresh();});card.append(yes,no);}else card.append(el('small',proposal.status==='approved'?'Сведение принято':'Предложение отклонено'));$('chat').append(card);}
 }
 for(const[k,v]of Object.entries(current.master))if($(k)){if(typeof v==='boolean')$(k).checked=v;else $(k).value=v;}
 renderSaves();
 // Older dialogue stays in the database; the newest page is only the start of
 // it. Without a way back the log stopped at fifty and the opening words were
 // unreachable. The control asks for the page before the cursor and prepends it.
 const older=document.getElementById('load-older');if(older)older.remove();
 if(current.conversation_more&&current.conversation_cursor){
  const button=el('button','Показать более ранние ходы','ghost');button.id='load-older';button.type='button';
  button.onclick=protect(async()=>{
   needCampaign();if(busy)return;button.disabled=true;button.textContent='Загружаю…';
   try{
    const q=encodeURIComponent(JSON.stringify(current.conversation_cursor));
    const page=await api('/campaigns/'+campaignId+'/conversation?before='+q);
    const rows=page.conversation||[];
    if(!rows.length){button.remove();return;}
    current.conversation=[...rows,...(current.conversation||[])];
    current.conversation_more=Boolean(page.conversation_more);
    current.conversation_cursor=page.conversation_cursor;
    await render({keepScroll:true});
    const back=document.getElementById('load-older');if(back)back.focus();
   }catch(e){button.disabled=false;button.textContent='Показать более ранние ходы';throw e;}
  });
  const chat=$('chat');if(chat)chat.prepend(button);
 }
 document.dispatchEvent(new CustomEvent('solo:render',{detail:current}));
 if(restored){const active=[...chat.querySelectorAll('[data-event]')].filter(node=>!node.classList.contains('undone')).at(-1);chat.scrollTop=active?chat.scrollTop+active.getBoundingClientRect().bottom-chat.getBoundingClientRect().bottom+12:0;}
 else if(newCampaign||following)chat.scrollTop=chat.scrollHeight;
 else{const next=[...chat.querySelectorAll('[data-message-key]')].find(node=>node.dataset.messageKey===anchorKey);chat.scrollTop=next?chat.scrollTop+next.getBoundingClientRect().top-chat.getBoundingClientRect().top-anchorOffset:previousTop;}
 lastUIRender=+(performance.now()-start).toFixed(2);
}
// The journal and the conversation log are the only record that a turn was
// committed, and they carry its request_id: that is an exact answer, while a
// status name is not — a stored scene turn reports 'observation', and a stored
// turn can even report a version error. A turn with no row anywhere was refused
// before any write, so the feed would swallow the player's words: echoTurn()
// then shows that exchange once, after the final render.
function recordedInFeed(requestId){return [...(current.history||[]),...(current.conversation||[]),...(current.gm_history||[])].some(row=>row.request_id===requestId);}
function echoTurn(text,reply){const chat=$('chat');chat.append(message((current.state.hero?.name||'ИГРОК').toUpperCase(),text,'player'));if(reply)chat.append(message('МАСТЕР',reply,'master'));chat.scrollTop=chat.scrollHeight;}
async function sendAction(){needCampaign();if(busy)return;
 // The route is read from current.backend, but the URL is built from campaignId.
 // A campaign switched while the previous render failed would send this line to
 // the wrong backend's handler, so the state is re-read before the choice.
 if(current.id!==campaignId)await refresh();
if(current.backend!=='nat20')throw new Error('Эта кампания создана в прежней версии и не открывается. Начните новую кампанию.');
 const text=$('player-input').value.trim();if(!text)return;if(!pendingSend||pendingSend.campaign!==campaignId||pendingSend.text!==text)pendingSend={campaign:campaignId,text,id:crypto.randomUUID(),selection:window.soloSelection?.campaign===campaignId?window.soloSelection.value:null};localStorage.setItem('solo-pending-send',JSON.stringify(pendingSend));const sentId=pendingSend.id;markBusy(true,'Мастер слушает…');let answered=null;try{// r210: у кампании один интерпретатор — command-turn Nat20; прежний /play удалён.
if(!Number.isInteger(current.version))throw new Error('Состояние кампании ещё не загружено. Обновите экран.');const r=await api('/campaigns/'+campaignId+'/command-turn',{request_id:pendingSend.id,text,expected_version:current.version});answered=r;
 pendingSend=null;localStorage.removeItem('solo-pending-send');$('player-input').value='';await refresh();}finally{markBusy(false);// A failing final render must not swallow the refusal either, and the focus
// belongs to the composer in both cases.
try{await refresh();}finally{if(answered&&!recordedInFeed(sentId))echoTurn(text,answered.narrative||null);$('player-input').focus();}}}
$('action-form').onsubmit=protect(async e=>{e.preventDefault();await sendAction();});
// n03: одно поле и один способ отправки. Enter отправляет (на телефоне это
// клавиша «Ввод» на клавиатуре — enterkeyhint="send"), Shift+Enter — перенос
// строки, Ctrl+Enter остался для привычки со стола.
$('player-input').addEventListener('keydown',e=>{
  if(e.key!=='Enter'||e.isComposing)return;
  if(e.shiftKey||e.altKey)return;
  e.preventDefault();
  protect(sendAction)();
});
async function narrate(event,regenerate=false){if(busy)return;markBusy(true);try{await streamAnswer('/events/'+event+'/narrate',{regenerate},event);}finally{markBusy(false);await refresh();}}
async function narrateGM(id){if(busy)return;markBusy(true);try{await streamAnswer('/gm/'+id+'/stream',undefined);}finally{markBusy(false);await refresh();}}
async function streamAnswer(path,body,event){
 streamController=new AbortController();$('stop').hidden=false;($('story-status')||{}).textContent='Мастер отвечает…';let box=event?document.querySelector('[data-event="'+event+'"]'):null;
 if(!box){box=message(path.startsWith('/gm')?'МАСТЕР · ВНЕ РОЛИ':'МАСТЕР','','master');$('chat').append(box);}const prose=box.querySelector('.prose');prose.textContent='';let buffer='';
 try{const r=await fetch('/api'+path,{method:'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:streamController.signal});if(!r.ok)throw new Error('Мастер не смог продолжить ответ.');const reader=r.body.getReader(),decoder=new TextDecoder();while(true){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});const frames=buffer.split('\n\n');buffer=frames.pop();for(const frame of frames)for(const line of frame.split('\n'))if(line.startsWith('data: ')){const d=JSON.parse(line.slice(6));if(d.type==='token'){const chat=$('chat'),following=chat.scrollHeight-chat.clientHeight-chat.scrollTop<32;prose.textContent+=d.text;if(following)chat.scrollTop=chat.scrollHeight;}if(d.type==='error')toast(d.message?('Описание не завершено. Игровое состояние сохранено. Причина: '+d.message):'Описание не завершено. Игровое состояние сохранено.');}}}catch(e){if(e.name!=='AbortError')throw e;}finally{streamController=null;$('stop').hidden=true;}
}
$('stop').onclick=()=>streamController?.abort();($('undo')||{}).onclick=protect(async()=>{needCampaign();if(busy)throw new Error('Остановите ответ перед отменой хода.');await api('/campaigns/'+campaignId+'/undo',{});await refresh();});


const openCreate=()=>$('create-dialog').showModal();$('begin')?.addEventListener('click',openCreate);ctl('new-campaign').onclick=openCreate;

async function loadCampaigns(){const list=await api('/campaigns');$('campaign-list').replaceChildren();for(const c of list){const supported=c.backend==='nat20',card=el('div',undefined,'list-card'),text=el('div');text.append(el('h3',c.name),el('small',new Date(c.created*1000).toLocaleString('ru-RU')));if(!supported)text.append(el('small','Создана в прежней версии — не открывается. Начните новую.','hint'));const b=el('button','Продолжить');b.disabled=!supported;b.onclick=protect(async()=>{if(busy)throw new Error('Дождитесь ответа.');campaignId=c.id;localStorage.setItem('solo-campaign',c.id);await refresh();view('play');});card.append(text,b);$('campaign-list').append(card);}}
function renderSaves(){ctl('save-list').replaceChildren();for(const s of current.saves){const card=el('div',undefined,'list-card');card.append(el('h3',s.name));const b=el('button','Восстановить');b.onclick=protect(async()=>{if(busy)throw new Error('Дождитесь ответа.');await api('/campaigns/'+campaignId+'/restore/'+s.id,{});await refresh();});card.append(b);ctl('save-list').append(card);}}
ctl('save-form').onsubmit=protect(async e=>{e.preventDefault();needCampaign();await api('/campaigns/'+campaignId+'/saves',{name:$('save-name').value.trim()});await refresh();toast('Приключение сохранено.');});
function readMaster(){const m={...(current?.master||selectedProfile().master)};for(const k of Object.keys(m))if($(k))m[k]=typeof m[k]==='boolean'?$(k).checked:['temperature','max_tokens'].includes(k)?Number($(k).value):$(k).value;m.literary=collectLiterary();return m;}
ctl('master-form').onsubmit=protect(async e=>{e.preventDefault();needCampaign();await api('/campaigns/'+campaignId+'/master',readMaster(),'PUT');await refresh();await loadMasterHistory();toast('Настройки сохранены.');});
function downloadJSON(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
ctl('master-export').onclick=protect(()=>{downloadJSON(readMaster(),'solo-master.json');});ctl('master-import').onchange=protect(async e=>{const file=e.target.files[0];if(!file)return;if(file.size>30000)throw new Error('Профиль слишком большой.');const master=JSON.parse(await file.text());const added=await api('/master/profiles',{name:master.profile_name||'Импортированный профиль',master});await loadProfiles();$('profile-select').value=added.id;fillMaster(master);e.target.value='';toast('Профиль импортирован в библиотеку.');});
async function loadMasterHistory(){if(!current)return;const rows=await api('/campaigns/'+campaignId+'/master/history');$('master-history').replaceChildren();for(const r of rows){const card=el('div',undefined,'list-card');card.append(el('span',new Date(r.created*1000).toLocaleString('ru-RU')));const b=el('button','Вернуть версию');b.onclick=protect(async()=>{await api('/campaigns/'+campaignId+'/master',r.master,'PUT');await refresh();await loadMasterHistory();});card.append(b);$('master-history').append(card);}}
ctl('prompt-preview').onclick=protect(async()=>{needCampaign();const h=[...current.history].reverse().find(h=>!h.undone);if(!h)throw new Error('Сначала начните историю.');($('prompt-text')||{}).textContent=JSON.stringify(await api('/events/'+h.id+'/prompt'),null,2);$('prompt-text').hidden=false;});
async function loadProfiles(){profiles=await api('/master/profiles');$('profile-select').replaceChildren();for(const p of profiles){const o=el('option',p.name);o.value=p.id;$('profile-select').append(o);}if(!current&&profiles.length)fillMaster(profiles[0].master);}
ctl('profile-apply').onclick=protect(async()=>{needCampaign();const p=profiles.find(p=>p.id===$('profile-select').value);await api('/campaigns/'+campaignId+'/master',p.master,'PUT');await refresh();await loadMasterHistory();toast('Профиль выбран. Предыдущие настройки сохранены.');});ctl('profile-save').onclick=protect(async()=>{const master=readMaster();await api('/master/profiles',{name:master.profile_name,master});await loadProfiles();toast('Профиль сохранён отдельно.');});
ctl('narrative-edit-close').onclick=()=>$('narrative-edit-dialog').close();ctl('narrative-edit-form').onsubmit=protect(async e=>{e.preventDefault();if(busy)throw new Error('Дождитесь ответа.');await api('/events/'+editingEvent+'/narrative',{text:$('narrative-correction').value},'PUT');$('narrative-edit-dialog').close();await refresh();toast('Исправлен только текст.');});
async function loadPerformance(){const p=await api('/performance');$('performance-stats').replaceChildren();for(const[v,label]of[[p.cpu_percent+'%','CPU'],[p.app_ram_mb+' МиБ','RAM приложения'],[(p.gpu?.vram_used_mb??'—')+' МиБ','Общая VRAM GPU'],[(lastUIRender??'—')+' мс','Отрисовка']]){const x=el('div',undefined,'metric');x.append(el('strong',v),el('small',label));$('performance-stats').append(x);}$('calls').replaceChildren();for(const m of p.llm_calls){const row=el('tr');for(const v of[m.mode,m.context_tokens??'—',m.ttft_ms??'—',m.duration_ms??'—',m.tokens_per_sec??'—',m.status])row.append(el('td',String(v)));$('calls').append(row);}($('turn-metrics')||{}).textContent=current?JSON.stringify(current.history.slice(-5).map(h=>h.metrics),null,2):'';}
ctl('performance-refresh').onclick=protect(loadPerformance);
// n12: «Тестовые данные · mock» — признак тестового адаптера, и в обычной сборке
// его не должно быть ни в шапке, ни на экране создания: владелец видел эту строку
// и решал, что игра показывает заглушку. Показываем её только при явном тестовом
// флаге (?audit=1 или ?mock-badge=1). Без флага тестовый адаптер честно считается
// неподключённым Мастером — облачного Мастера в нём действительно нет.
function mockBadge(){try{const q=new URLSearchParams(location.search);return q.get('audit')==='1'||q.get('mock-badge')==='1';}catch(e){return false;}}
window.soloMockBadge=mockBadge;
async function health(){try{const h=await api('/health'),mock=h.llm?.mock===true,ready=h.readiness?.status==='ready';($('model-status')||{}).textContent=mock&&mockBadge()?'Тестовые данные · mock':ready&&!mock?'Облачный Мастер доступен':h.llm&&!mock?'Облачный профиль настроен':'Подключите облачного Мастера';ctl('model-dot').classList.toggle('off',!ready);}catch(e){($('model-status')||{}).textContent='Локальный сервер не подключён';}finally{setTimeout(health,5000);}}
protect(async()=>{await health();if(campaignId){try{await refresh();if(pendingSend?.campaign===campaignId)$('player-input').value=pendingSend.text;}catch(e){toast(e.message);}}})();


// ---- «Начать приключение»: источник, герой, редактор героя Nat20 ----
// r210: ручного листа больше нет ни на одном экране. Диалог всегда ведёт
// один путь: прочитать источник, показать что понято, открыть редактор
// Nat20 (правила считает сервер) и создать кампанию его движком.
{
 const steps=$('begin-steps'),template=$('chat-begin-steps');
 if(steps&&template)steps.append(template.content.cloneNode(true));
 let draft=null,sourceName='',uploading=false,boundWorld=null;
 const panel=(id,shown)=>{const node=$(id);if(node)node.hidden=!shown;};
 // The draft outlives a page reload: without this every visit left a stored copy
 // of the uploaded document (up to 8 МБ) inside preparation.sqlite3 forever.
 // A draft that already started a campaign is not reused.
 async function ensureDraft(){
  if(draft)return draft;
  const saved=localStorage.getItem('solo-source-draft');
  if(saved)try{const existing=await api('/preparation/drafts/'+saved);if(!existing.campaign){draft=existing;return draft;}}catch(error){}
  draft=await api('/preparation/drafts',{});localStorage.setItem('solo-source-draft',draft.id);
  return draft;
 }
 // The upload answer carries every source of the draft, and a second fast pick
 // would otherwise borrow the other file's quality. The digest names this file.
 async function digest(file){
  try{const buffer=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
   return [...new Uint8Array(buffer)].map(byte=>byte.toString(16).padStart(2,'0')).join('');}catch(error){return '';}
 }
 // What the server actually reported, in the player's words. A source the
 // extractor read but could not structure says so instead of pretending the
 // places and NPC were found.
 function understood(quality){
  const rows=[],pages=quality.pages||0,count=pages===1?'страница':pages>=2&&pages<=4?'страницы':'страниц';
  rows.push((quality.status==='usable'?'Прочитано: ':'Текст не извлечён: ')+pages+' '+count+', '+(quality.characters||0)+' символов.');
  if(quality.structure==='structured')rows.push('Понято: мест '+quality.locations+', NPC '+quality.npc+', событий '+quality.events+'.');
  else if(quality.status==='usable')rows.push('Текст прочитан, но сценарий не распознан — начнём с чистого листа.');
  for(const warning of quality.warnings||[])rows.push(warning);
  return rows;
 }
 // A whole book is read by the page library in its own process, so the dialog
 // watches the import job instead of the upload: done/total are pages, and the
 // same answer names the chapter boundaries and modules the import produced.
 async function readLibrary(id,status){
  const started=Date.now();
  for(;;){
   const job=await api('/adventure/jobs/'+id);
   if(job.status==='complete')return job;
   if(job.status==='failed'||job.status==='cancelled')throw new Error(job.message||'книга не прочитана');
   status.textContent='Читаю книгу: '+(job.done||0)+' из '+(job.total||'?')+' страниц';
   if(Date.now()-started>3600000)throw new Error('чтение книги не завершилось за час');
   await new Promise(resolve=>setTimeout(resolve,600));
  }
 }
 if($('source-file'))$('source-file').onchange=async event=>{
  const file=event.target.files[0],status=$('source-status'),summary=$('source-summary'),next=$('source-next'),picker=$('source-file');
  if(!file||uploading)return;
  uploading=true;picker.disabled=true;
  next.hidden=true;summary.hidden=true;summary.replaceChildren();
  status.textContent='Читаю «'+file.name+'»…';
  try{
   const current=await ensureDraft();
   const data=new FormData();data.append('file',file);
   const response=await fetch('/api/preparation/drafts/'+current.id+'/sources',{method:'POST',body:data});
   const result=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'источник не прочитан');
   // A book above the draft limit is no longer refused: the server hands it to the
   // page library and says which branch it took. The size is not judged here — a
   // browser limit that hides the one control the player needs is not a rule.
   if(result.path==='library'){
    // A second upload of the same file is deduplicated: the job finishes with
// done=total=0 while the bundle already holds every page. The number the
// player reads must come from the bundle, not from the queue counter.
const job=await readLibrary(result.job,status),reused=result.bundle?await api('/adventure/library').then(rows=>(rows||[]).find(row=>row.id===result.bundle)).catch(()=>null):null,pages=(reused&&reused.pages)||job.done||job.total||0;
    // The module is stored by the server on the same draft the hero step and the
    // campaign start read. It is not a promise this screen has to remember.
    const world=await api('/preparation/drafts/'+current.id+'/world',{}).catch(error=>({bound:false,reasons:[error.message]}));
    boundWorld=world&&world.bound?world:null;
    const binding=boundWorld
     ?[el('p','Глава «'+boundWorld.title+'», стр. '+pageSpan(boundWorld)+' остаётся с героем: кампания начнётся на её первой странице.','hint')]
     :[el('p','Кампания начнётся без книги: '+(world.reasons||[]).join(' ')+'.','hint')];
    summary.replaceChildren(
     el('p','Понято: страниц '+pages+', глав '+(job.chapters||0)+
        (job.appendices?', эпизодов '+(job.episodes||0)+', приложений '+job.appendices
                       :', модулей '+(job.modules||0))+'.','hint'),
     el('p','Книга прочитана постранично и лежит в библиотеке приключений: текст в поисковом индексе, границы глав предложены автоматически. Закрытые материалы остаются у Мастера.','hint'),...binding);
    summary.hidden=false;sourceName=file.name;
    status.textContent='«'+file.name+'» прочитан целиком.';
    next.hidden=false;
    return;
   }
   const hash=await digest(file),sources=result.sources||[];
   const quality=((hash?sources.find(source=>source.sha256===hash):null)||sources.at(-1)||{}).quality||{};
   // A stored but unreadable document is not a success: the extractor answers 200
   // with status 'failed', and calling that «принят» would contradict its own
   // warning that preparation is blocked for this source.
   const usable=quality.status==='usable';
   if(usable)sourceName=file.name;
   summary.replaceChildren(...understood(quality).map(text=>el('p',text,'hint')));
   summary.hidden=false;
   status.textContent=usable?'«'+file.name+'» принят.':'«'+file.name+'» прочитан, но текста в нём нет.';
   next.hidden=!usable;
  }catch(error){
   // Any file is a question, never a dead end: the picker stays usable and the
   // dialog is not restarted, so the player simply chooses another document.
   status.textContent='Не удалось прочитать «'+file.name+'»: '+error.message+'. Выберите другой файл — диалог остаётся открытым.';
  }finally{uploading=false;picker.disabled=false;event.target.value='';}
 };
 // The draft lives on the server and its id in localStorage, so a reload between
 // the upload and the hero step must not silently drop the book: this screen asks
 // the saved draft what it is bound to instead of trusting what it saw before.
 async function recallDraft(){
  if(draft)return draft;
  const saved=localStorage.getItem('solo-source-draft');if(!saved)return null;
  try{const existing=await api('/preparation/drafts/'+saved);if(existing.campaign)return null;draft=existing;return draft;}catch(error){return null;}
 }
 const pageSpan=world=>Array.isArray(world&&world.pages)&&world.pages[0]?world.pages[0][0]+'–'+world.pages[0][1]:'страницы не указаны';
 async function recallBinding(){
  const current=await recallDraft();
  if(!current||boundWorld)return;
  const world=await api('/preparation/drafts/'+current.id+'/world',{}).catch(()=>({bound:false,reasons:[]}));
  boundWorld=world&&world.bound?world:null;
 }
 const toHero=async()=>{panel('begin-source',false);panel('begin-hero',true);
  const button=$('hero-editor');if(button)button.focus();
  await protect(async()=>{
   await recallBinding();
   const book=boundWorld?('Книга остаётся с героем: кампания начнётся на первой странице главы «'+boundWorld.title+'» (стр. '+pageSpan(boundWorld)+'); сценарные события книги пока не проверены.'):'Книга к герою не привязана: кампания начнётся без неё.';
   const note=$('hero-note');
   if(note)note.textContent=(sourceName?('Источник «'+sourceName+'» разобран и сохранён в подготовку приключения. '):'Материалы сохранены в подготовку приключения. ')+'Теперь соберите героя: вид, класс, предыстория, характеристики и снаряжение. Правила считает сервер — в браузере их нет. '+book;
  })();};
 if($('source-next'))$('source-next').onclick=toHero;
 if($('source-skip'))$('source-skip').onclick=toHero;
 // The entry always starts at the source step: reopening the dialog must not
 // strand the player on a step whose upload would land out of sight.
 if($('hero-back'))$('hero-back').onclick=()=>{panel('begin-hero',false);panel('begin-source',true);};
 if($('begin'))$('begin').addEventListener('click',()=>{panel('begin-source',true);panel('begin-hero',false);});
 for(const id of ['source-close','hero-close']){const node=$(id);if(node)node.onclick=()=>$('create-dialog').close();}
 if($('hero-editor'))$('hero-editor').onclick=async()=>{
  const status=$('hero-status');status.textContent='Открываю редактор героя…';
  try{
   const info=await api('/health');
   // The wizard contract route is registered only for the experimental Nat20
   // build. Naming that is better than a button that promises a screen this
   // process cannot open; the parsed source is stored either way.
   if(!info.experimental)throw new Error('этот запуск сервера поднят без экспериментальных маршрутов Nat20. Источник сохранён; редактор откроется в сборке с ними');
   if(!window.soloWizard)throw new Error('файл редактора (wizard.js) не загрузился');
   status.textContent='';
   $('create-dialog').close();
   const current=await recallDraft();
   await window.soloWizard.open({preparationDraft:current?current.id:''});
  }catch(error){status.textContent='Редактор героя не открылся: '+error.message+'.';}
 };
}

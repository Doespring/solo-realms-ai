'use strict';
const $=id=>document.getElementById(id);
const abilities={strength:'Сила',dexterity:'Ловкость',constitution:'Телосложение',intelligence:'Интеллект',wisdom:'Мудрость',charisma:'Харизма'};
let campaignId=localStorage.getItem('solo-remember-campaign')==='off'?null:localStorage.getItem('solo-campaign'),current=null,busy=false,streamController=null,profiles=[],editingEvent=null,lastUIRender=null;
let pendingSend=null;try{pendingSend=JSON.parse(localStorage.getItem('solo-pending-send'));}catch(e){}
let historyRestoreRequested=false,historyViewCampaign=null;
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function field(name,input){const label=el('label',name);label.append(input);return label;}
function toast(text){($('toast')||{}).textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>{$('toast').hidden=true;},7000);}
function protect(fn){return async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};}
async function api(path,body,method=body===undefined?'GET':'POST'){const r=await fetch('/api'+path,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok){const detail=data.detail,error=new Error(typeof detail==='string'?detail:detail?.message||'Не удалось обработать запрос.');error.status=r.status;error.code=detail?.code||data.code;
  // 404 on our own /api route means the running process predates the page, not
  // that the player did something wrong. The editor cannot work against an API
  // that never had the route, so the reason is named and repair is offered.
  if(r.status===404&&String(path).startsWith('/')&&!/^\/(campaigns|creation)\/[^/]+$/.test(path)){error.staleServer=true;error.message='Интерфейс новее запущенного сервера: маршрут '+path+' отсутствует в текущем процессе. Перезапустите приложение; ваши сохранения не пострадают.';}
  throw error;}if(method==='POST'&&path.startsWith('/campaigns/')&&path.includes('/restore/'))historyRestoreRequested=path.split('/')[2];return data;}
function needCampaign(){if(!current)throw new Error('Откройте или создайте кампанию.');}
function view(name){document.querySelectorAll('.view').forEach(e=>e.classList.toggle('active',e.id===name));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.view===name));if(name==='campaigns')protect(loadCampaigns)();if(name==='performance')protect(loadPerformance)();if(name==='master')protect(async()=>{await loadProfiles();await loadMasterHistory();})();}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));
$('theme').value=localStorage.getItem('solo-theme')||'dark';document.documentElement.dataset.theme=$('theme').value;
$('theme').onchange=()=>{document.documentElement.dataset.theme=$('theme').value;localStorage.setItem('solo-theme',$('theme').value);};
$('effects').checked=localStorage.getItem('solo-effects')!=='off';document.body.classList.toggle('effects',$('effects').checked);
$('effects').onchange=()=>{document.body.classList.toggle('effects',$('effects').checked);localStorage.setItem('solo-effects',$('effects').checked?'on':'off');};
function markBusy(value,label='Мастер отвечает…'){busy=value;$('send').disabled=value;($('story-status')||{}).textContent=value?label:'Мастер ждёт вашей реплики';}
async function refresh(){if(!campaignId)return;const older=(current&&current.conversation)||[];current=await api('/campaigns/'+campaignId);try{mergeOlder(older);render()}catch(e){console.error('RENDER_FAIL',e&&e.stack||e);throw e}}
function mergeOlder(older){const fresh=current.conversation||[];if(!older.length||!fresh.length)return;const seen=new Set(fresh.map(r=>r.id));const kept=older.filter(r=>!seen.has(r.id));if(!kept.length)return;const all=[...kept,...fresh].sort((a,b)=>(a.created-b.created)||(String(a.id)<String(b.id)?-1:1));current.conversation=all;const first=all[0];current.conversation_cursor=[first.created,first.id];current.conversation_more=true;}
function message(who,text,cls='master',undone=false){const box=el('article',undefined,'message '+cls+(undone?' undone':''));box.append(el('div',who+(undone?' · ХОД ОТМЕНЁН':''),'speaker'),el('div',text,'prose'));return box;}
function fallback(results){return results.map(r=>r.type==='check'?(r.success?'Попытка удалась.':'Попытка не удалась.'):['unsupported','needs_context'].includes(r.type)?r.reply:r.type==='travel_stop'?r.text:r.type==='move'?'Путь пройден.':r.type==='deferred'?'Это намерение пока не исполнено.':r.type==='halted'?'Последующие шаги ещё не выполнялись.':r.type==='denied'?'Это действие не совершено.':r.text||'').join('\n');}
function render(options){
 const keepScroll=Boolean(options&&options.keepScroll);
 const start=performance.now(),state=current.state,hero=state.hero,place=state.locations.find(p=>p.name===state.location);
 const chat=$('chat'),restored=historyRestoreRequested===current.id,newCampaign=historyViewCampaign!==current.id;
 const previousTop=chat.scrollTop,following=chat.scrollHeight-chat.clientHeight-previousTop<32;
 const anchor=[...chat.querySelectorAll('[data-message-key]')].find(node=>node.getClientRects().length&&node.getBoundingClientRect().bottom>chat.getBoundingClientRect().top);
 const anchorKey=anchor?.dataset.messageKey,anchorOffset=anchor?anchor.getBoundingClientRect().top-chat.getBoundingClientRect().top:0;
 // The anchor may leave the screen after the rebuild (a page of older turns is
 // taller than the visible part). Remember the first row that was on screen so
 // the reader keeps the place even when that row was the top one.
 const anchorIndexOf=anchorKey?[...chat.querySelectorAll('[data-message-key]')].findIndex(n=>n.dataset.messageKey===anchorKey):-1;
 const openArchives=new Set([...chat.querySelectorAll('.undone-history[open]')].map(node=>node.dataset.archiveKey));
 historyRestoreRequested=false;historyViewCampaign=current.id;
 ($('campaign-label')||{}).textContent=current.name;($('scene-title')||{}).textContent=place.name;($('scene-description')||{}).textContent=place.description;($('turn')||{}).textContent='Ход '+state.turn;
 let calc=$('calculator-status');if(!calc){calc=el('p',undefined,'hint');calc.id='calculator-status';const anchor=$('scene-description');if(anchor)anchor.after(calc);}{const note=current.rules_backend_note||'';calc.textContent=note;calc.hidden=!note;calc.setAttribute('role','status');}
 let entry=$('entry-readiness');if(!entry){entry=el('p',undefined,'hint');entry.id='entry-readiness';entry.setAttribute('role','status');const anchor=$('scene-description');if(anchor)anchor.after(entry);else{$('action-form')?.before(entry);}}entry.hidden=!state.adventure?.entry_readiness;entry.textContent=state.adventure?.entry_readiness?.boundary||'';
 ($('hero-summary')||{}).textContent=hero.name+(current.master.hide_mechanics?' · история сохраняется':' · '+hero.hp+'/'+hero.max_hp+' хитов · история сохраняется');$('scene-risk').hidden=!current.master.declared_lethal_risk;
 ($('hero-details')||{}).textContent=hero.name+' · уровень '+hero.level+' · хиты '+hero.hp+'/'+hero.max_hp+' · КБ '+hero.ac;
 $('abilities').replaceChildren();for(const[k,v]of Object.entries(hero.abilities)){const m=Math.floor((v-10)/2);$('abilities').append(el('div',abilities[k]+': '+v+' ('+(m>=0?'+':'')+m+')','ability'));}
 $('chat').replaceChildren();
 // The opening belongs to the GM: the scene description is what the player is
 // told, never something their hero "said". Exactly one copy is rendered here,
 // and game-screen.js no longer adds a second one.
 // B1: the opening is the first thing the master said, so it stays at the
 // top of the feed for the whole adventure instead of vanishing after turn one.
  const openingText=[place.description,...(place.facts||[])].filter(Boolean).join('\n\n');
 // B1: exactly one copy. The timeline already carries the master's opening
 // once it is recorded, so the header copy is added only before that.
  if(openingText.trim()&&![...current.history,...(current.conversation||[])].some(h=>String(h.narrative||h.reply||'').includes(openingText.slice(0,40))))
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
 else if(newCampaign||following)chat.scrollTop=keepScroll?chat.scrollTop:chat.scrollHeight;
 else{const live=[...chat.querySelectorAll('[data-message-key]')];const next=live.find(node=>node.dataset.messageKey===anchorKey)||live[anchorIndexOf];chat.scrollTop=next?chat.scrollTop+next.getBoundingClientRect().top-chat.getBoundingClientRect().top-anchorOffset:previousTop;}
 lastUIRender=+(performance.now()-start).toFixed(2);
}
let lastEventId=null;
function announceReplay(r){
  // The server answers a repeat with the result it already committed. The player
  // must not think the action ran twice: say so, and say whether the table has
  // moved on since (moved_on), which is the honest reason the old outcome differs
  // from what the scene looks like now.
  if(!r)return;
  // The server answers a repeat either with an explicit replayed flag or, on the
  // older path, with the very same stored answer - same event id, same text.
  // Both mean 'this was already done'; neither means the action ran twice.
  // The stored answer arrives through the stream, not in this reply, so the
  // event id is what identifies 'the same turn coming back'.
  const again=r.replayed===true||(!!r.event&&!!r.event.id&&r.event.id===lastEventId);
  if(r.event&&r.event.id)lastEventId=r.event.id;
  if(!again)return;
  toast(r.moved_on
    ?'Этот ход уже был выполнен — показан прежний результат. С тех пор сцена изменилась.'
    :'Этот ход уже был выполнен — показан прежний результат, повторно ничего не произошло.');
}
async function sendAction(){needCampaign();if(busy)return;const text=$('player-input').value.trim();if(!text)return;if(!pendingSend||pendingSend.campaign!==campaignId||pendingSend.text!==text)pendingSend={campaign:campaignId,text,id:crypto.randomUUID(),selection:window.soloSelection?.campaign===campaignId?window.soloSelection.value:null};localStorage.setItem('solo-pending-send',JSON.stringify(pendingSend));markBusy(true,'Мастер слушает…');try{const r=await api('/campaigns/'+campaignId+'/play',{request_id:pendingSend.id,text,selection:pendingSend.selection||null});pendingSend=null;localStorage.removeItem('solo-pending-send');$('player-input').value='';await refresh();if(r.replayed)announceReplay(r);if(r.kind==='event')await streamAnswer('/events/'+r.event.id+'/narrate',{regenerate:false},r.event.id);if(r.kind==='gm')await streamAnswer('/gm/'+r.gm+'/stream',undefined);}finally{markBusy(false);await refresh();$('player-input').focus();}}
$('action-form').onsubmit=protect(async e=>{e.preventDefault();await sendAction();});$('player-input').addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();protect(sendAction)();}});
async function narrate(event,regenerate=false){if(busy)return;markBusy(true);try{await streamAnswer('/events/'+event+'/narrate',{regenerate},event);}finally{markBusy(false);await refresh();}}
async function narrateGM(id){if(busy)return;markBusy(true);try{await streamAnswer('/gm/'+id+'/stream',undefined);}finally{markBusy(false);await refresh();}}
async function streamAnswer(path,body,event){
 streamController=new AbortController();$('stop').hidden=false;($('story-status')||{}).textContent='Мастер отвечает…';let box=event?document.querySelector('[data-event="'+event+'"]'):null;
 if(!box){box=message(path.startsWith('/gm')?'МАСТЕР · ВНЕ РОЛИ':'МАСТЕР','','master');$('chat').append(box);}const prose=box.querySelector('.prose');prose.textContent='';let buffer='';
 try{const r=await fetch('/api'+path,{method:'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:streamController.signal});if(!r.ok)throw new Error('Мастер не смог продолжить ответ.');const reader=r.body.getReader(),decoder=new TextDecoder();while(true){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});const frames=buffer.split('\n\n');buffer=frames.pop();for(const frame of frames)for(const line of frame.split('\n'))if(line.startsWith('data: ')){const d=JSON.parse(line.slice(6));if(d.type==='token'){const chat=$('chat'),following=chat.scrollHeight-chat.clientHeight-chat.scrollTop<32;prose.textContent+=d.text;if(following)chat.scrollTop=chat.scrollHeight;}if(d.type==='error')toast(d.message?('Описание не завершено. Игровое состояние сохранено. Причина: '+d.message):'Описание не завершено. Игровое состояние сохранено.');}}}catch(e){if(e.name!=='AbortError')throw e;}finally{streamController=null;$('stop').hidden=true;}
}
$('stop').onclick=()=>streamController?.abort();($('undo')||{}).onclick=protect(async()=>{needCampaign();if(busy)throw new Error('Остановите ответ перед отменой хода.');await api('/campaigns/'+campaignId+'/undo',{});await refresh();});
for(const[k,label]of Object.entries(abilities)){const input=el('input');input.type='number';input.min=1;input.max=30;input.value={strength:14,dexterity:14,constitution:12,intelligence:10,wisdom:12,charisma:10}[k];input.id='new-'+k;$('create-abilities').append(field(label,input));}
const openCreate=()=>$('create-dialog').showModal();$('begin')?.addEventListener('click',openCreate);$('new-campaign').onclick=openCreate;$('create-close').onclick=()=>$('create-dialog').close();
$('create-form').onsubmit=protect(async e=>{e.preventDefault();if(busy)return;const scores={};for(const k of Object.keys(abilities))scores[k]=Number($('new-'+k).value);const hp=Number($('new-hp').value);const c=await api('/campaigns',{name:$('new-name').value.trim(),ruleset:$('new-ruleset').value,world_mode:$('new-world-mode').value,map_travel:$('new-map-travel').checked,hero:{name:$('new-hero').value.trim(),background:$('new-background').value,species:$('new-species').value,class_name:$('new-class').value,origin:$('new-origin').value,level:Number($('new-level').value),hp,max_hp:hp,ac:Number($('new-ac').value),abilities:scores}});campaignId=c.id;localStorage.setItem('solo-campaign',c.id);$('create-dialog').close();await refresh();view('play');});
async function loadCampaigns(){const list=await api('/campaigns');$('campaign-list').replaceChildren();for(const c of list){const card=el('div',undefined,'list-card'),text=el('div');text.append(el('h3',c.name),el('small',new Date(c.created*1000).toLocaleString('ru-RU')));const b=el('button','Продолжить');b.onclick=protect(async()=>{if(busy)throw new Error('Дождитесь ответа.');campaignId=c.id;localStorage.setItem('solo-campaign',c.id);await refresh();view('play');});card.append(text,b);$('campaign-list').append(card);}}
function renderSaves(){$('save-list').replaceChildren();for(const s of current.saves){const card=el('div',undefined,'list-card');card.append(el('h3',s.name));const b=el('button','Восстановить');b.onclick=protect(async()=>{if(busy)throw new Error('Дождитесь ответа.');await api('/campaigns/'+campaignId+'/restore/'+s.id,{});await refresh();});card.append(b);$('save-list').append(card);}}
$('save-form').onsubmit=protect(async e=>{e.preventDefault();needCampaign();await api('/campaigns/'+campaignId+'/saves',{name:$('save-name').value.trim()});await refresh();toast('Приключение сохранено.');});
function readMaster(){const m={...(current?.master||selectedProfile().master)};for(const k of Object.keys(m))if($(k))m[k]=typeof m[k]==='boolean'?$(k).checked:['temperature','max_tokens'].includes(k)?Number($(k).value):$(k).value;m.literary=collectLiterary();return m;}
$('master-form').onsubmit=protect(async e=>{e.preventDefault();needCampaign();await api('/campaigns/'+campaignId+'/master',readMaster(),'PUT');await refresh();await loadMasterHistory();toast('Настройки сохранены.');});
function downloadJSON(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('master-export').onclick=protect(()=>{downloadJSON(readMaster(),'solo-master.json');});$('master-import').onchange=protect(async e=>{const file=e.target.files[0];if(!file)return;if(file.size>30000)throw new Error('Профиль слишком большой.');const master=JSON.parse(await file.text());const added=await api('/master/profiles',{name:master.profile_name||'Импортированный профиль',master});await loadProfiles();$('profile-select').value=added.id;fillMaster(master);e.target.value='';toast('Профиль импортирован в библиотеку.');});
async function loadMasterHistory(){if(!current)return;const rows=await api('/campaigns/'+campaignId+'/master/history');$('master-history').replaceChildren();for(const r of rows){const card=el('div',undefined,'list-card');card.append(el('span',new Date(r.created*1000).toLocaleString('ru-RU')));const b=el('button','Вернуть версию');b.onclick=protect(async()=>{await api('/campaigns/'+campaignId+'/master',r.master,'PUT');await refresh();await loadMasterHistory();});card.append(b);$('master-history').append(card);}}
$('prompt-preview').onclick=protect(async()=>{needCampaign();const h=[...current.history].reverse().find(h=>!h.undone);if(!h)throw new Error('Сначала начните историю.');($('prompt-text')||{}).textContent=JSON.stringify(await api('/events/'+h.id+'/prompt'),null,2);$('prompt-text').hidden=false;});
async function loadProfiles(){profiles=await api('/master/profiles');$('profile-select').replaceChildren();for(const p of profiles){const o=el('option',p.name);o.value=p.id;$('profile-select').append(o);}if(!current&&profiles.length)fillMaster(profiles[0].master);}
$('profile-apply').onclick=protect(async()=>{needCampaign();const p=profiles.find(p=>p.id===$('profile-select').value);await api('/campaigns/'+campaignId+'/master',p.master,'PUT');await refresh();await loadMasterHistory();toast('Профиль выбран. Предыдущие настройки сохранены.');});$('profile-save').onclick=protect(async()=>{const master=readMaster();await api('/master/profiles',{name:master.profile_name,master});await loadProfiles();toast('Профиль сохранён отдельно.');});
$('narrative-edit-close').onclick=()=>$('narrative-edit-dialog').close();$('narrative-edit-form').onsubmit=protect(async e=>{e.preventDefault();if(busy)throw new Error('Дождитесь ответа.');await api('/events/'+editingEvent+'/narrative',{text:$('narrative-correction').value},'PUT');$('narrative-edit-dialog').close();await refresh();toast('Исправлен только текст.');});
async function loadPerformance(){const p=await api('/performance');$('performance-stats').replaceChildren();for(const[v,label]of[[p.cpu_percent+'%','CPU'],[p.app_ram_mb+' МиБ','RAM приложения'],[(p.gpu?.vram_used_mb??'—')+' МиБ','Общая VRAM GPU'],[(lastUIRender??'—')+' мс','Отрисовка']]){const x=el('div',undefined,'metric');x.append(el('strong',v),el('small',label));$('performance-stats').append(x);}$('calls').replaceChildren();for(const m of p.llm_calls){const row=el('tr');for(const v of[m.mode,m.context_tokens??'—',m.ttft_ms??'—',m.duration_ms??'—',m.tokens_per_sec??'—',m.status])row.append(el('td',String(v)));$('calls').append(row);}($('turn-metrics')||{}).textContent=current?JSON.stringify(current.history.slice(-5).map(h=>h.metrics),null,2):'';}
$('performance-refresh').onclick=protect(loadPerformance);
// The interface this page was built against. A server that speaks another
// contract may answer with fields of a different shape; saying so once, in
// words, is better than a turn that half works.
const UI_CONTRACT='solo-api-1';
// Kept on the machine, not in memory: a page reload must still be able to tell
// that the server behind it is not the one it last spoke to.
let seenBuild=localStorage.getItem('solo-seen-build')||null;
async function health(){try{const h=await api('/health'),ready=h.readiness?.status==='ready';
 // A process that was started before the files on disk changed keeps serving
 // its own code. That is normal, but the page must not let it pass for the
 // current version: the old code shows the old defects, and the person would
 // blame the new one. Say it in words the first time the change is noticed.
 // Sent separately from the health wording, which the status line overwrites
 // with its own text; a notice that lives only in that one line disappears.
 const restarted=h.build&&seenBuild&&h.build!==seenBuild;
 if(h.build){seenBuild=h.build;try{localStorage.setItem('solo-seen-build',h.build);}catch(e){}}
 const mismatch=h.contract&&h.contract!==UI_CONTRACT;
 // Warnings win over the routine wording: the line below would otherwise
 // overwrite every notice on the very next line and hide it for good.
 const stale=h.drift===true;
 // Name the layer that diverged. The server compares its loaded code and its own
 // page files separately, so the page must not blame the server when only web/*
 // was edited - then the server is current and a reload is enough.
 const uiOnly=stale&&h.ui_drift===true&&h.disk===h.build;
 const note=mismatch?'Версия сервера не совпадает с интерфейсом ('+h.contract+' вместо '+UI_CONTRACT+'). Обновите страницу или перезапустите сервер.'
   :uiOnly?'Страница устарела: её файлы на диске изменены, сервер работает на правильной сборке. Обновите страницу.'
   :stale?'Сервер работает на прежней версии: его код на диске изменён. Перезапустите сервер, чтобы увидеть исправления.'
   :restarted?'Сервер перезапущен: сборка '+h.build+'. Подробности загружаются текущей версией.'
   :h.llm?.mock?'Тестовые данные · mock':ready?'Облачный Мастер доступен':h.llm?'Облачный профиль настроен':'Подключите облачного Мастера';
 const box=$('model-status');if(box){box.textContent=note;box.dataset.notice=mismatch?'contract':stale?'stale':restarted?'restart':'';box.dataset.contractMismatch=mismatch?'1':'';}$('model-dot').classList.toggle('off',!ready);}catch(e){($('model-status')||{}).textContent='Локальный сервер не подключён';}finally{setTimeout(health,5000);}}
protect(async()=>{await health();if(campaignId){try{await refresh();if(pendingSend?.campaign===campaignId)$('player-input').value=pendingSend.text;}catch(e){toast(e.message);}}})();

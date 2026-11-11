/* Stage B: retain the existing sections and callbacks behind a tabletop shell. */
(()=>{
 'use strict';
 document.body.classList.add('tabletop');
 const nav=document.querySelector('.sidebar nav'),campaignMenu=document.querySelector('.campaign-nav .nav-submenu');
 const originals=[...nav.querySelectorAll('button[data-view]')],find=id=>originals.find(b=>b.dataset.view===id);
 const primary=el('div',undefined,'tabletop-primary');primary.setAttribute('aria-label','Основные экраны');
 for(const[id,title]of [['play','Игра'],['character','Персонаж'],['journal','Блокнот']]){const b=find(id);b.querySelector('.nav-text').textContent=title;primary.append(b);}
 campaignMenu.append(primary);
 const tools=el('details',undefined,'nav-group tabletop-advanced');tools.id='tabletop-advanced';tools.append(el('summary','Advanced / Developer'));
 const advancedHint=el('p','Технические разделы и редакторы канона. Ручное изменение листа требует включения режима разработчика.','hint');tools.append(advancedHint);
 const advancedMenu=el('div',undefined,'nav-submenu');tools.append(advancedMenu);
 const modeLabel=$('advanced-mode')?.closest('label');if(modeLabel)tools.insertBefore(modeLabel,advancedMenu);
 const settings=find('settings'),campaigns=find('campaigns'),exit=find('exit');
 const secondary=new Set(['play','character','journal','settings','campaigns','exit','inventory','skills','world','saves','adventure-handouts','cloud','costs']);
 for(const b of originals)if(!secondary.has(b.dataset.view))advancedMenu.append(b);
 // Secondary campaign sections remain reachable from their parent screens.
 function shortcuts(parent,entries){const row=el('div',undefined,'tabletop-tabs');row.setAttribute('aria-label','Разделы экрана');for(const[id,title]of entries){const original=find(id);if(!original)continue;const b=el('button',title);b.type='button';b.dataset.tabletopLink=id;b.onclick=()=>original.click();row.append(b);}parent.querySelector('h1')?.after(row);return row;}
 const heroTabs=[['character','Лист героя'],['inventory','Инвентарь и торговля'],['skills','Навыки и магия']];
 for(const id of ['character','inventory','skills'])shortcuts($(id),heroTabs);
 $('character').append(el('p','Герой создаётся пошаговым конструктором, а повышение уровня Nat20 считает по опыту кампании.','hint'));
 $('journal').querySelector('h1').textContent='Блокнот';
 const known=$('journal').querySelector('.known-journal');if(known)$('journal').append(known);
 for(const id of ['journal','saves'])shortcuts($(id),[['journal','События и записи'],['saves','Контрольные точки']]);
 const notes=el('section',undefined,'player-notes'),noteTitle=el('h2','Мои записи'),noteText=el('textarea'),noteStatus=el('p','','hint'),noteSave=el('button','Сохранить запись','primary'),noteReload=el('button','Обновить блокнот');
 noteText.id='player-notes';noteText.rows=7;noteText.maxLength=20000;noteText.setAttribute('aria-label','Личные записи');notes.append(noteTitle,el('p','Ваши догадки и планы. Записи не становятся фактами мира и не передаются Мастеру.','hint'),noteText,noteSave,noteReload,noteStatus);$('journal').querySelector('.tabletop-tabs').after(notes);
 let noteCampaign=null,noteRevision=0,noteDirty=false,noteRequest=null,noteLoading=false,noteLoadStamp=0;
 const noteDrafts=new Map(),noteSaving=new Set(),noteKey=cid=>'solo-note-draft:'+cid;
 function readNoteDraft(cid){
  if(noteDrafts.has(cid))return noteDrafts.get(cid);
  try{const d=JSON.parse(localStorage.getItem(noteKey(cid)));if(d&&typeof d.text==='string'&&d.text.length<=20000&&Number.isInteger(d.revision)&&d.revision>=0){noteDrafts.set(cid,d);return d;}}catch{}
  return null;
 }
 function writeNoteDraft(cid,draft){
  if(draft)noteDrafts.set(cid,draft);else noteDrafts.delete(cid);
  try{if(draft)localStorage.setItem(noteKey(cid),JSON.stringify(draft));else localStorage.removeItem(noteKey(cid));}catch{}
 }
 function rememberNotes(){if(noteCampaign&&noteDirty)writeNoteDraft(noteCampaign,{text:noteText.value,revision:noteRevision,request:noteRequest});}
 function activateNotes(){
  if(noteCampaign===campaignId)return;
  rememberNotes();noteLoadStamp++;noteLoading=false;noteCampaign=campaignId;
  const draft=readNoteDraft(noteCampaign);noteRevision=draft?.revision??0;noteText.value=draft?.text??'';noteDirty=!!draft;noteRequest=draft?.request||null;
  noteText.disabled=false;noteSave.disabled=noteSaving.has(noteCampaign);noteStatus.textContent=draft?'Восстановлен несохранённый черновик.':'Откройте блокнот, чтобы загрузить записи.';
 }
 noteText.oninput=()=>{noteDirty=true;noteRequest=null;rememberNotes();noteStatus.textContent='Есть несохранённые изменения.';};
 async function loadNotes(force=false){
  if(!current)return;activateNotes();if(noteLoading||noteDirty&&!force)return;
  const cid=noteCampaign,stamp=++noteLoadStamp;noteLoading=true;noteText.disabled=noteSave.disabled=true;
  try{const data=await api('/campaigns/'+cid+'/notes');if(cid!==campaignId||stamp!==noteLoadStamp)return;noteRevision=data.revision;noteText.value=data.text;noteDirty=false;noteRequest=null;if(force)writeNoteDraft(cid,null);noteStatus.textContent='Запись '+data.revision+' · хранится отдельно от событий.';}
  finally{if(stamp===noteLoadStamp){noteLoading=false;noteText.disabled=false;noteSave.disabled=noteSaving.has(cid);}}
 }
 noteSave.onclick=protect(async()=>{
  needCampaign();if(noteCampaign!==campaignId)throw new Error('Откройте блокнот текущей кампании.');const cid=campaignId;if(noteSaving.has(cid)||noteLoading)return;
  noteRequest=noteRequest||{request_id:crypto.randomUUID(),text:noteText.value,expected_revision:noteRevision};const sent=noteRequest;rememberNotes();noteSaving.add(cid);noteSave.disabled=true;
  try{
   const result=await api('/campaigns/'+cid+'/notes',sent,'PUT'),draft=readNoteDraft(cid);
   if(draft)writeNoteDraft(cid,draft.text===sent.text?null:{...draft,revision:result.revision,request:null});
   if(cid!==campaignId||noteCampaign!==cid)return;noteRevision=result.revision;noteRequest=null;
   if(noteText.value===sent.text){noteDirty=false;noteStatus.textContent='Запись сохранена.';}else{rememberNotes();noteStatus.textContent='Сохранена предыдущая версия; новые изменения ещё не сохранены.';}
  }finally{noteSaving.delete(cid);if(noteCampaign===cid)noteSave.disabled=noteLoading;}
 });
 noteReload.onclick=protect(async()=>{if(noteDirty&&!confirm('Обновить блокнот и заменить несохранённый текст?'))return;await loadNotes(true);});
 const eventLog=el('section',undefined,'notebook-events');eventLog.append(el('h2','События приключения'));const eventRows=el('div');eventLog.append(eventRows);$('journal').append(eventLog);
 function renderEvents(){eventRows.replaceChildren();for(const event of (current?.history||[]).filter(e=>!e.undone).slice(-30).reverse()){const row=el('details',undefined,'disclosure');row.append(el('summary','Ход '+event.seq+' · '+event.original.slice(0,100)),el('p',event.narrative||fallback(event.results),'prose'));eventRows.append(row);}if(!eventRows.childElementCount)eventRows.append(el('p','Здесь появятся события вашего приключения.','hint'));}
 // Dice are an auxiliary instrument: a saved free roll cannot adjudicate a game action.
 const dice=el('details',undefined,'tabletop-dice disclosure');dice.id='tabletop-dice';dice.append(el('summary','Кубики'));
 const diceForm=el('form'),diceCount=el('input'),diceSides=el('select'),diceMod=el('input'),rollButton=el('button','Бросить'),diceResult=el('div'),diceHistory=el('details');
 diceCount.type='number';diceCount.min=1;diceCount.max=20;diceCount.value=1;diceCount.id='dice-count';diceMod.type='number';diceMod.min=-100;diceMod.max=100;diceMod.value=0;diceMod.id='dice-modifier';diceSides.id='dice-sides';
 for(const n of [4,6,8,10,12,20,100]){const option=el('option','d'+n);option.value=n;diceSides.append(option);}diceSides.value=20;diceForm.append(field('Кости',diceCount),field('Грани',diceSides),field('Бонус',diceMod),rollButton);diceHistory.append(el('summary','Последние броски'),diceResult);dice.append(diceForm,el('p','Свободные броски не меняют мир. Проверки действий выполняет движок.','hint'),diceHistory);campaignMenu.append(dice);
 let diceRequest=null,diceCid=null;const showDice=rows=>{diceResult.replaceChildren();for(const row of rows)diceResult.append(el('p',row.formula+' = '+row.total+' ['+row.dice.join(', ')+']','dice-entry'));};
 diceForm.oninput=()=>{diceRequest=null;};diceForm.onsubmit=protect(async e=>{e.preventDefault();needCampaign();const cid=campaignId;if(diceCid!==cid){diceCid=cid;diceRequest=null;}diceRequest=diceRequest||{request_id:crypto.randomUUID(),count:Number(diceCount.value),sides:Number(diceSides.value),modifier:Number(diceMod.value)};rollButton.disabled=true;try{const result=await api('/campaigns/'+cid+'/dice',diceRequest);if(cid!==campaignId)return;showDice([result]);diceHistory.open=true;diceRequest=null;}finally{rollButton.disabled=false;}});
 dice.addEventListener('toggle',()=>{if(dice.open&&current)protect(async()=>{const cid=campaignId;const rows=await api('/campaigns/'+cid+'/dice');if(cid===campaignId)showDice(rows);})();});
 // Store the secondary original controls here so callbacks/selectors remain compatible.
 const legacy=el('div',undefined,'tabletop-secondary');for(const id of ['inventory','skills','world','saves','adventure-handouts'])if(find(id))legacy.append(find(id));advancedMenu.append(legacy);
 const navigation=el('div',undefined,'tabletop-utilities');navigation.append(campaigns,settings,...['cloud','costs'].map(find).filter(Boolean));nav.replaceChildren(document.querySelector('.campaign-nav'),navigation,tools,exit);
 for(const id of ['play','world','adventure-handouts'])if($(id))shortcuts($(id),[['play','Игра'],['world','Карта · по желанию'],['adventure-handouts','Материалы игрока']]);
 const info=el('button','Сведения о сцене','quiet');info.type='button';info.setAttribute('aria-expanded','false');info.onclick=()=>{const shown=document.body.classList.toggle('tabletop-scene-open');info.setAttribute('aria-expanded',String(shown));};$('play').querySelector('.tabletop-tabs')?.append(info);
 // A direct legacy view still highlights its primary tabletop parent.
 const priorView=view;view=function(name){activateNotes();priorView(name);const parent=['inventory','skills'].includes(name)?'character':name==='saves'?'journal':['world','adventure-handouts'].includes(name)?'play':name;for(const b of primary.querySelectorAll('.nav'))b.classList.toggle('active',b.dataset.view===parent);if(name==='journal')protect(()=>loadNotes())();};
 
// A view that needs an open campaign must say so instead of clicking into a
// silent refusal: the item is disabled and its title names the reason.
const CAMPAIGN_VIEWS=['inventory','skills','world','saves','adventure-handouts','campaign-settings','campaign-rules'];
function syncCampaignNav(){const open=Boolean(current);for(const b of document.querySelectorAll('.nav[data-view]')){
  const needs=CAMPAIGN_VIEWS.includes(b.dataset.view);
  const off=needs&&!open;b.setAttribute('aria-disabled',off?'true':'false');b.disabled=off;
  if(off)b.title='Сначала откройте кампанию в «Моих приключениях».';else b.removeAttribute('title');}}
document.addEventListener('solo:render',syncCampaignNav);syncCampaignNav();
document.addEventListener('solo:render',()=>{renderEvents();activateNotes();if($('journal').classList.contains('active'))protect(()=>loadNotes())();});if(current)renderEvents();
 window.addEventListener('beforeunload',e=>{if(noteDirty){e.preventDefault();e.returnValue='';}});
 $('player-input').placeholder='Что делает или говорит ваш герой?';
 $('hero-details').hidden=true;$('abilities').hidden=true;
 const rolls=el('details',undefined,'disclosure');rolls.append(el('summary','Проверки и путь'),$('rolls'));$('journal').append(rolls);
})();

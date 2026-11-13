/* Compose existing RPG panels. All numbers and action availability come from the adapter. */
(()=>{
 'use strict';
 const play=$('play');play.classList.add('unified-game');
 const hero=$('scene-hero');if(hero){hero.open=true;rail.prepend(hero);}
 const log=el('details',undefined,'disclosure game-log');log.open=true;
 const rows=el('div');log.append(el('summary','Броски и события'),rows);rail.append(log);
 // Строка последнего броска остаётся в DOM ради проверки восстановления
 // (tests/test_small_window_ui.cjs читает её), но на экране её нет с r220:
 // game-screen.css прячет .game-last-roll, а бросок уже показан плашкой в ленте.
 const lastRoll=el('p','Бросков пока нет.','game-last-roll');lastRoll.id='game-last-roll';lastRoll.hidden=true;
 const feedback=el('div',undefined,'game-feedback'),feedbackText=el('span'),recover=el('button','Обновить сцену');
 feedback.id='game-feedback';feedback.setAttribute('role','status');feedback.hidden=true;recover.type='button';recover.hidden=true;feedback.append(feedbackText,recover);$('action-form').before(lastRoll,feedback);
 // L02: сверка уровня героя с книгой — ход Мастера текстом, до первого броска.
 // Кнопок действия в игровом экране нет (решение владельца 08.11): игрок
 // отвечает словами «пересобрать героя» или «играем так», их разбирает сервер.
 const gateBox=el('section',undefined,'game-gate');gateBox.id='game-gate';gateBox.setAttribute('role','status');
 gateBox.hidden=true;$('action-form').before(gateBox);
 function renderGate(gate){
  // Решённая сверка остаётся на экране строкой Мастера: после перезапуска
  // игрок видит, каким способом ведётся игра, а не пустое место. Полный ответ
  // Мастера уже стоит в ленте хода, поэтому здесь — только решение и числа,
  // без второго пересказа той же фразы.
  if(!gate||(!gate.gap&&!gate.decision)){gateBox.hidden=true;gateBox.replaceChildren();return;}
  const methodWords={danger_from_hero_level:'опасность боёв ведётся от уровня героя, задания вне сюжета дают опыт',rebuilt_at_book_level:'герой пересобран на уровень книги'};
  const lines=[el('p','МАСТЕР · сверка уровня','speaker')];
  if(!gate.decided){
   lines.push(el('p',gate.ask,'game-gate__text'));
   lines.push(el('p','Ответьте словами: «пересобрать героя» — откроется редактор на '+gate.required+'-м уровне; «играем так» — я буду вести опасность под ваш '+gate.hero_level+'-й.','hint'));
  }else lines.push(el('p','Решено: '+(methodWords[gate.method]||'решение записано')+'. Книга просит '+gate.required+'-й уровень, герой '+gate.hero_level+'-й.','game-gate__text'));
  gateBox.replaceChildren(...lines);gateBox.hidden=false;
 }
 // A02: старая запись не валит экран. Совместимость приходит с сервера
 // (схема, ревизия мира, подпись регистрации), экран только называет её
 // по-русски и даёт три действия: копия с обновлением / проверка / выгрузка.
 // Ничего не сверяется и не считается в браузере.
 const compatBox=el('section',undefined,'game-compat');compatBox.id='game-compat';
 compatBox.setAttribute('role','status');compatBox.hidden=true;$('action-form').before(compatBox);
 async function compatAction(action){
  const cid=campaignId;if(!cid)return;
  if(action==='export'){window.open('/api/campaigns/'+cid+'/compat/export','_blank');
   status('ready','Выгрузка кампании открыта: файл сохранён браузером. История и состояние в нём целиком.');return;}
  compatBox.querySelectorAll('button').forEach(button=>{button.disabled=true;});
  let answer=null;
  try{
   const data=await baseAPI('/campaigns/'+cid+'/compat/'+action,{});
   if(action==='migrate'){
    await rejoinScene();
    answer={kind:'ready',text:(data.message||'Кампания обновлена до текущей версии.')+' Сцена перечитана.'};
   }else answer={kind:data.ok?'ready':'unsupported',text:data.summary||'Проверка выполнена.'};
  }catch(error){
   answer={kind:'error',text:error&&error.message||'Действие не выполнено: проверьте связь и повторите.'};
  }finally{
   if(campaignId===cid&&packet)renderPacket(packet);
   // Перерисовка сцены ставит свою строку («ввод заперт, история читается») и
   // затирала ответ проверки: игрок нажимал «Проверить базу» и не видел итога.
   // Ответ действия возвращается на место последним.
   if(answer)status(answer.kind,answer.text);
  }
 }
 function renderCompatibility(compat){
  if(!compat){compatBox.hidden=true;compatBox.replaceChildren();return;}
  const events=compat.events||{};
  if(compat.status==='ok'){
   // Старая запись, которую уже можно играть: события прежнего обработчика
   // помечены историческими одной строкой Мастера, ввод не заперт.
   if(!events.historical||!events.count){compatBox.hidden=true;compatBox.replaceChildren();return;}
   compatBox.dataset.state='history';
   compatBox.replaceChildren(el('p','МАСТЕР · старая запись','speaker'),
    el('p',events.reason||'События прежнего обработчика прочитаны как исторические.','game-compat__text'));
   compatBox.hidden=false;return;
  }
  // Несовместимость видна ДО ввода: причина, что именно не сошлось и три пути.
  const lines=[el('p','МАСТЕР · совместимость кампании','speaker'),
   el('p','Эта кампания сохранена прежней версией и здесь не играется, пока её не обновят. История читается, ничего не потеряно.','game-compat__text')];
  for(const check of compat.checks||[]){
   if(check.ok)continue;
   lines.push(el('p',(check.label||'Проверка')+': '+(check.detail||''),'hint game-compat__check'));
  }
  const row=el('div',undefined,'button-row game-compat__actions');
  // Кнопки берутся у сервера: у записи без состояния движка он оставляет только
  // выгрузку, и обещать «Проверить базу»/«Обновить», которые гейт отобьёт, нельзя.
  for(const action of compat.actions||[]){
   const id=action.id,label=action.label||id;
   const disabled=id==='migrate'&&!compat.migratable;
   const button=el('button',label);button.type='button';button.disabled=Boolean(disabled);
   if(disabled)button.title='Автоматическое обновление этой записи невозможно: '+(compat.summary||'');
   button.dataset.action=id;button.onclick=protect(()=>compatAction(id));row.append(button);
  }
  lines.push(row,el('p','«Сделать копию и обновить» сначала кладёт снимок профиля в backups и только потом меняет кампанию. «Экспортировать кампанию» работает при любом исходе.','hint'));
  compatBox.dataset.state='needs-choice';compatBox.replaceChildren(...lines);compatBox.hidden=false;
 }
 // The main screen is a conversation: the player types intent and the Master answers. The mechanics stay available to the dialogue but are not offered as a palette of buttons.
 const actions=el('div',undefined,'button-row game-actions');actions.setAttribute('aria-label','Подтверждённые действия');actions.hidden=true;$('action-form').after(actions);
 // Решение владельца 08.11: в игровом экране нет кнопок действия, включая бросок.
 // Бросок и всё остальное — текстом в поле ввода («бросаю d20», «атакую гоблина»).
 // От прежнего ряда осталась одна подпись состояния хода: это текст, не кнопка.
 const diceRow=el('div',undefined,'dice-row'),turnNote=el('span','','dice-row__note');
 turnNote.setAttribute('aria-live','polite');diceRow.append(turnNote);$('action-form').after(diceRow);
 const restore=el('details',undefined,'disclosure game-restore'),saves=el('select'),restoreButton=el('button','Восстановить');saves.setAttribute('aria-label','Контрольная точка');restoreButton.type='button';restore.append(el('summary','Восстановить сохранение'),saves,restoreButton);rail.append(restore);
 const saveNow=el('button','Сохранить контрольную точку');saveNow.type='button';restore.querySelector('summary').textContent='Сохранения и восстановление';restore.insertBefore(saveNow,saves);
 const saveCaption=el('p','','hint game-save-name');saveCaption.setAttribute('aria-live','polite');saves.after(saveCaption);
 function describeSave(){saveCaption.textContent=saves.selectedOptions[0]?.textContent||'Контрольных точек пока нет.';saves.title=saveCaption.textContent;}saves.onchange=describeSave;
 const battleActions=el('div',undefined,'button-row game-actions');battleActions.hidden=true;{const panel=$('battle-panel');if(panel)panel.querySelector('.battle-content').prepend(battleActions);}
 const battleContent=$('battle-panel')?.querySelector('.battle-content'),mapDetails=el('details',undefined,'game-map');mapDetails.append(el('summary','Тактическая карта и осмотр'));
 const battleStatus=[...battleContent.children].filter(node=>node.tagName==='P').slice(0,2);
 for(const node of [...battleContent.children])if(node!==battleActions&&!battleStatus.includes(node)&&!node.classList.contains('battle-actors'))mapDetails.append(node);
 battleContent.append(mapDetails);
 // r220: шапка со статами и вход в лист героя. Это слой оформления: числа
 // берутся из пакета сцены (движок), разметка — из макета «Стол и лист».
 const topbar=document.querySelector('main>header');
 let vitals=null;
 if(topbar){vitals=el('div',undefined,'vitals');vitals.setAttribute('aria-label','Состояние героя');topbar.insertBefore(vitals,topbar.querySelector('.runtime')||null);}
 rail.id='sheet-rail';
 const sheetToggle=el('button','Лист','iconbtn sheet-toggle');
 const sheetClose=el('button','Закрыть лист','sheet-close');
 let sheetReturn=null;
 function setSheet(open){
  document.body.classList.toggle('sheet-open',open);
  sheetToggle.setAttribute('aria-expanded',open?'true':'false');
  if(open){sheetReturn=document.activeElement;if(typeof sheetClose.focus==='function')sheetClose.focus();}
  else if(sheetReturn&&typeof sheetReturn.focus==='function')sheetReturn.focus();
 }
 if(topbar){sheetToggle.type='button';sheetToggle.id='sheet-toggle';sheetToggle.setAttribute('aria-controls','sheet-rail');sheetToggle.setAttribute('aria-expanded','false');topbar.append(sheetToggle);}
 sheetClose.type='button';sheetClose.setAttribute('aria-controls','sheet-rail');
 sheetToggle.onclick=()=>setSheet(!document.body.classList.contains('sheet-open'));
 sheetClose.onclick=()=>setSheet(false);
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&document.body.classList.contains('sheet-open'))setSheet(false);});
 rail.prepend(sheetClose);
 // Пометка броска: та же вилка, что в макете (03-play), но значения — из
 // проекции сервера. Ни одного нового числа здесь не считается: dice, total
 // и success приходят из движка как есть.
 // r226: знак кубика из design/dice (файлы дизайнера, генерируются
 // design/dice/sign.py). В ленте — чип 36 px, на экране броска — гранёный
 // d20 120 px. Силуэты — копия web/visual/dice/symbols.svg на самой странице:
 // бросок рисуется сразу, без ожидания сети и без внешнего <use>, который
 // браузеры разрешают по-разному. Расхождение копии с файлом ловит тест
 // test_dice_sign_matches_designer_symbols.
 const DICE_SYMBOLS='<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" aria-hidden="true" focusable="false"><symbol id="sg-chip-24" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><polygon class="hull" points="1.75,12.09 7.00,3.00 17.50,3.00 22.75,12.09 17.50,21.19 7.00,21.19"/></g></symbol><symbol id="sg-chip-36" viewBox="0 0 36 36"><g fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><polygon class="hull" points="1.60,18.29 9.85,4.00 26.35,4.00 34.60,18.29 26.35,32.58 9.85,32.58"/></g></symbol><symbol id="sg-d20-64" viewBox="0 0 64 64"><g fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"><polygon class="hull" points="1.77,32.48 16.77,6.50 46.77,6.50 61.77,32.48 46.77,58.46 16.77,58.46"/><polygon class="plate" fill="currentColor" fill-opacity=".1" points="22.50,16.42 50.31,32.48 22.50,48.54"/></g></symbol><symbol id="sg-d20-120" viewBox="0 0 120 120"><g fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"><polygon class="hull" points="2.43,59.73 31.43,9.50 89.43,9.50 118.43,59.73 89.43,109.96 31.43,109.96"/><polygon class="plate" fill="currentColor" fill-opacity=".1" points="42.50,28.69 96.27,59.73 42.50,90.77"/><path class="edge" d="M42.50 28.69 L89.43 9.50 M42.50 28.69 L2.43 59.73 M89.43 9.50 L96.27 59.73 M96.27 59.73 L89.43 109.96 M89.43 109.96 L42.50 90.77 M42.50 90.77 L2.43 59.73"/><path class="edge" d="M42.50 28.69 L31.43 9.50 M96.27 59.73 L118.43 59.73 M42.50 90.77 L31.43 109.96"/></g></symbol></svg>';
 document.body.insertAdjacentHTML('beforeend',DICE_SYMBOLS);
 // Число и исход — только из движка: dice, total, success, dc, modifier
 // приходят из проекции сервера, браузер их не считает.
 function dieFace(value){return Number.isFinite(Number(value))?Math.round(Number(value)):'—';}
 function dieOutcome(roll,dice){
  if(roll.success===true)return dice.length===1&&dice[0]===20?'crit':'hit';
  if(roll.success===false)return 'miss';
  return '';
 }
 function dieSVG(value,out,big){
  const size=big?120:36;
  return '<svg class="die '+(big?'die--120':'die--36')+(out?' o-'+out:'')+'" viewBox="0 0 '+size+' '+size+'" aria-hidden="true">'
   +'<use href="#'+(big?'sg-d20-120':'sg-chip-36')+'"/><text class="die__num">'+dieFace(value)+'</text></svg>';
 }
 function rollFigures(roll){
  const dice=(roll.dice||[]).filter(value=>typeof value==='number'),parts=[];
  if(Number.isFinite(roll.dc))parts.push('<b>'+Math.round(roll.dc)+'</b> нужно');
  parts.push(dice.length?'<b>'+dice.join(', ')+'</b> выпало':'числа не пришли');
  if(Number.isFinite(roll.modifier)){const m=Math.round(roll.modifier);parts.push('<b>'+(m<0?'−'+Math.abs(m):'+'+m)+'</b>');}
  return parts.join(' · ');
 }
 // Экран броска: тот же бросок крупно, гранёный d20 120 px. Открывается
 // нажатием на знак в ленте — на телефоне плашка узкая, число в грань не влезает.
 function openRollView(roll){
  const dice=(roll.dice||[]).filter(value=>typeof value==='number'),nat=dice.length===1?dice[0]:null,
   out=dieOutcome(roll,dice),verdict=out==='crit'?'крит':out==='hit'?'попал':out==='miss'?'мимо':'';
  const dialog=el('dialog',undefined,'roll-view');
  dialog.append(el('h2',(roll.label||'Бросок')+' · d20'));
  const die=el('div',undefined,'roll-view__die');die.innerHTML=dieSVG(nat!=null?nat:roll.total,out,true);
  const figures=el('p',undefined,'roll-view__figures');figures.innerHTML=rollFigures(roll);
  const sum=el('p',undefined,'roll-view__sum');sum.append(el('span','='),el('b',Number.isFinite(roll.total)?String(roll.total):'—'));
  dialog.append(die,figures,sum);
  if(verdict){const tag=el('p',undefined,'roll-view__verdict'),stamp=el('span',verdict,'roll__verdict');stamp.dataset.out=out;tag.append(stamp);dialog.append(tag);}
  const close=el('button','Закрыть','quiet');close.type='button';close.onclick=()=>dialog.close();dialog.append(close);
  dialog.addEventListener('close',()=>dialog.remove());
  document.body.append(dialog);dialog.showModal();
 }
 function dieBox(value,out){
  const box=el('button',undefined,'roll__die');box.type='button';
  box.setAttribute('aria-label','Показать бросок крупно');box.title='Бросок крупно';
  box.innerHTML=dieSVG(value,out,false);
  return box;
 }
 function rollPlaque(roll){
  if(!roll)return null;
  const dice=(roll.dice||[]).filter(value=>typeof value==='number'),nat=dice.length===1?dice[0]:null;
  const hit=roll.success===true,crit=hit&&nat===20,miss=roll.success===false;
  // r226: три исхода теми же словами, что в макете знака, — попал/мимо/крит.
  const verdict=crit?'крит':hit?'попал':miss?'мимо':'бросок';
  const aside=el('aside',undefined,'roll');
  aside.setAttribute('aria-label',[roll.label||'Бросок',dice.length?'выпало '+dice.join(', '):'',Number.isFinite(roll.total)?'итого '+roll.total:'',verdict].filter(Boolean).join(', '));
  const name=el('div',undefined,'roll__name');name.append(el('p',(roll.label||'Бросок')+' · d20','roll__kicker'));
  const nums=el('div',undefined,'roll__nums'),need=el('p',undefined,'roll__need');
  need.innerHTML=rollFigures(roll);
  const sum=el('p',undefined,'roll__sum');sum.append(el('span','='),el('b',Number.isFinite(roll.total)?String(roll.total):'—'));
  nums.append(need,sum);
  const tag=el('span',undefined,'roll__tag');
  if(hit||miss){const stamp=el('span',verdict,'roll__verdict');stamp.dataset.out=crit?'crit':hit?'hit':'miss';tag.append(stamp);}
  const out=dieOutcome(roll,dice),box=dieBox(nat!=null?nat:roll.total,out);box.onclick=()=>openRollView(roll);
  aside.append(box,name,nums,tag);
  return aside;
 }
 let rollsShown=new Set(),rollsPrimed=false,rollsCampaign=null;
 function paintRoll(article,event){
  if(!article||!event.roll||article.querySelector('.roll'))return;
  const plaque=rollPlaque(event.roll);if(!plaque)return;
  const fresh=rollsPrimed&&!rollsShown.has(event.id);
  rollsShown.add(event.id);
  article.append(plaque);
  // Анимация только у нового броска: история при открытии сцены не пляшет.
  if(fresh){const tag=plaque.querySelector('.roll__tag');
   plaque.classList.add('is-rolling');
   if(tag)tag.addEventListener('animationend',()=>plaque.classList.remove('is-rolling'),{once:true});}
 }
 const baseAPI=api;let packet=null,stamp=0,commandBusy=false,loading=false,retry=null;
// n08: защита героя есть только в листе (`/character` → sheet.ac); в пакете
// сцены её нет, а считать КД в браузере нельзя — движок единственный счётчик.
// Один запрос на кампанию, ответ кешируется; отказ не ломает экран — числа
// защиты просто не будет.
let heroAc=null,acCampaign=null;
async function loadArmorClass(){
 const cid=campaignId;if(!cid||acCampaign===cid)return;acCampaign=cid;heroAc=null;
 try{const data=await baseAPI('/campaigns/'+cid+'/character');if(cid!==campaignId)return;
  const value=data?.sheet?.ac;heroAc=Number.isFinite(Number(value))?Number(value):null;}
 catch(error){heroAc=null;}
 if(cid===campaignId&&packet)renderPacket(packet);
}
 // n01: одно нажатие — один запрос. Замок ставится синхронно, ДО первого await:
 // иначе нажатия, накопленные за время загрузки сцены, все вместе доходили до
 // отправки и рождали три одинаковых запроса (три 409 в журнале за одну секунду).
 let sendLock=false,sendHeldUntil=0;
 function sendHeld(){return Date.now()<sendHeldUntil;}
 function holdSends(){sendHeldUntil=Date.now()+400;
  setTimeout(()=>{if(campaignId&&packet?.snapshot.campaign_id===campaignId)renderPacket(packet);},430);}
 // Отказ по версии — не повод повторять то же тело: сцену перезапрашиваем сами,
 // а строку игроку говорим одну. Возвращает, удалось ли обновить сцену.
 const SCENE_UPDATED='Сцена обновилась. Вот что можно сделать',SCENE_STALE='Сцена изменилась, но обновить её не удалось. Проверьте связь и нажмите «Обновить сцену».';
 async function rejoinScene(){retry=null;pendingSend=null;localStorage.removeItem('solo-pending-send');
  try{await refresh();return await load();}catch(error){return false;}}
 const http={load:cid=>baseAPI('/campaigns/'+cid+'/screen'),send:(cid,command)=>{
  if(command.kind==='restore')return baseAPI('/campaigns/'+cid+'/restore/'+encodeURIComponent(command.parameters.checkpoint),{});
  if(command.kind==='engine')return baseAPI('/campaigns/'+cid+'/commands',{request_id:command.request_id,expected_version:command.expected_version,action:command.parameters});
   // The screen commands live at an absolute path: baseAPI() prepends /api and would
   // request /api/campaigns/<id>/api/campaigns/<id>/screen/commands, which no route
   // serves. The one_backend guard answered that miss with a bare 409, and every
   // button of the Nat20 screen ("Начать бой", проверки, отдых) looked broken.
   return baseAPI('/campaigns/'+cid+'/screen/commands',command,undefined,'/api');
 }};
 let transport=http;
 function actionBlocked(allowTerminal=false){return busy||commandBusy||loading||sendHeld()||(!allowTerminal&&Boolean(packet?.snapshot.terminal));}
 function feedbackTextFor(text){
  if(typeof text==='string'&&/Hide.*spend|hide.*spend/.test(text))return 'Скрытность пока недоступна: движок не расходует требуемое действие. Можно выбрать другое действие.';
  if(typeof text==='string'&&text.startsWith('Nat20 refused operation: ')){
   try{const failures=JSON.parse(text.slice('Nat20 refused operation: '.length));if(failures.some(e=>e.reason==='out_of_range'))return 'Цель вне досягаемости. Сначала выберите доступное перемещение.';}catch(e){}
   return 'Движок отклонил действие в текущей сцене. Выберите доступное действие.';
  }
  return text;
 }
 function status(kind,text){feedback.dataset.state=kind;feedbackText.textContent=feedbackTextFor(text);feedback.hidden=!text;recover.hidden=!['error','conflict'].includes(kind);}
 function rollText(roll){return roll?roll.label+': '+(roll.formula||roll.dice?.join(', ')||'')+' → '+(roll.total??'—')+(roll.success===true?' · успех':roll.success===false?' · неудача':''):'Бросков пока нет.';}
 function clearMechanics(){actions.replaceChildren();battleActions.replaceChildren();rows.replaceChildren();lastRoll.textContent=rollText(null);saves.replaceChildren();describeSave();restoreButton.disabled=true;}
 function buttons(container,commands){
  // A terminal scenario takes no action at all: the screen says why and offers
 // restore instead, so stale commands are not left looking usable.
 if(packet?.snapshot.terminal){container.replaceChildren();container.hidden=true;return;}
 const checks=commands.filter(command=>command.parameters?.kind==='check');
  if(checks.length>4){const group=el('div',undefined,'button-row game-command-choice'),select=el('select'),run=el('button','Выполнить проверку');select.setAttribute('aria-label','Проверка');for(const [i,command] of checks.entries()){const option=el('option',command.label);option.value=String(i);select.append(option);}run.type='button';run.disabled=actionBlocked();run.onclick=protect(()=>dispatch(checks[Number(select.value)]));group.append(select,run);container.replaceChildren(group);commands=commands.filter(command=>command.parameters?.kind!=='check');}
  else container.replaceChildren();
  let movement=null;for(const command of commands){const names={athletics:'Проверить Атлетику',perception:'Проверить Внимательность',strength:'Проверить Силу'},label=command.parameters?.kind==='check'?(names[command.parameters.check_id]||command.label):command.label;const button=el('button',label);button.type='button';button.disabled=actionBlocked();button.onclick=protect(()=>dispatch(command));
   if(command.parameters?.kind==='move'){if(!movement){movement=el('details',undefined,'game-movement');movement.append(el('summary','Перемещение'));container.append(movement);}movement.append(button);}else container.append(button);
  }
  // The row is created hidden and stays empty until a screen offers commands.
  container.hidden=!container.childElementCount;
 }
 function renderPacket(value){
  packet=value;$('send').disabled=actionBlocked();const s=value.snapshot,h=s.hero;const body=(hero&&hero.lastElementChild)||el('div');
  renderGate(s.level_gate);
  renderCompatibility(s.compatibility);
  // r222: поле боя рисует board.js из того же пакета — сам он ничего не считает
  // и своих списков действий не держит.
  document.dispatchEvent(new CustomEvent('solo:packet',{detail:{snapshot:s,commands:value.commands||[]}}));
  // r220: шапка показывает те же числа, что прислал движок, и ничего не считает.
  // n13: подписи вернулись короткой строкой «12/12 хиты · 18 защита» — без них
  // в бою не отличить хиты от защиты (замечание №2 критика). Слово идёт ПОСЛЕ
  // числа: число первым читается, подпись служит его значением.
  if(vitals&&h){const cells=[[String(h.hp)+'/'+h.max_hp,'хиты']];
   if(heroAc!==null)cells.push([String(heroAc),'защита']);
   const parts=[];
   cells.forEach(([value,label],index)=>{if(index)parts.push(el('span','·','vitals__sep'));const cell=el('span');cell.append(el('b',value),el('span',' '+label,'vitals__label'));parts.push(cell);});
   vitals.replaceChildren(...parts);
   vitals.setAttribute('aria-label',cells.map(([value,label])=>label+' '+value).join(', '));
   if(heroAc===null)loadArmorClass();}
  // n08: подпись ряда дока — «ход N · сохранено» (ход берётся из кампании,
  // автосохранение идёт каждый ход — так же сказано на экране «Сохранения»).
  if(turnNote){const turn=Number(current&&current.state&&current.state.turn);turnNote.textContent=turn>0?'ход '+turn+' · сохранено':'сохранено';}
  // (no opening text here: play.js renders the single GM opening, so the
 // transcript no longer repeats itself)
  // n14: строка «HP 12 / 12» убрана из панели «ВАШ ГЕРОЙ» — те же хиты уже
  // стоят в шапке («12/12 хиты»), и величина называлась двумя словами
  // (замечание №6 критика). В панели остаются имя, «Опыт», «Кости хитов»,
  // «Состояния» — то, чего в шапке нет.
  body.replaceChildren(el('h3',h.name));
  if(s.backend==='nat20'){const resources=new Map(h.resources.map(row=>[row.id,row.current]));if(resources.has('death_save:successes')||resources.has('dead')){const death=el('section',undefined,'game-death-state');death.setAttribute('aria-live','polite');death.append(el('p',resources.get('dead')?'Смерть подтверждена Nat20.':h.hp>0?'Герой снова в сознании.':resources.get('death_save:is_stable')?'Герой стабилизирован.':'Герой при смерти.','hint'),el('p','Спасброски от смерти: успехи '+(resources.get('death_save:successes')??'—')+' · провалы '+(resources.get('death_save:failures')??'—'),'hint'));body.append(death);}}
  const flags=[],movement=[];for(const resource of h.resources){
   if(s.backend==='nat20'&&(resource.id.startsWith('death_save:')||resource.id==='dead'))continue;
   if(s.backend==='nat20'&&resource.id.endsWith('_available')){flags.push(resource.label+': '+(resource.current?'да':'нет'));continue;}
   if(s.backend==='nat20'&&['movement_remaining','attacks_remaining'].includes(resource.id)){movement.push(resource.label+': '+resource.current);continue;}
   body.append(el('p',resource.label+': '+resource.current+(resource.maximum==null?'':' / '+resource.maximum),'hint'));
  }
  if(movement.length)body.append(el('p',movement.join(' · '),'hint'));if(flags.length)body.append(el('p',flags.join(' · '),'hint'));
  const conditions=window.SOLO_CONDITIONS||{};
  body.append(el('p','Кости хитов: '+h.hit_dice,'hint'),el('p','Состояния: '+(h.conditions.map(id=>conditions[id]||id).join(', ')||'нет'),'hint'));
  const sheet=el('button','Лист героя','quiet sheet-link');sheet.onclick=()=>view('character');body.append(sheet);
  const logTop=rows.scrollTop,openEvents=new Set([...rows.querySelectorAll('.game-event[open]')].map(node=>node.dataset.eventId));
  lastRoll.textContent=rollText(s.last_roll);rows.replaceChildren();
  for(const event of [...value.events].reverse()){const text=event.display_text||event.text,row=el('details',undefined,'game-event');row.dataset.eventId=event.id;row.open=openEvents.has(event.id);row.append(el('summary','Событие '+event.sequence+' · '+text),el('p',event.roll?rollText(event.roll):text,'hint'));rows.append(row);
   // The server decides what an event reads like (display_text) and the screen
   // shows it verbatim: no dictionary and no engine-line parsing here.
   const article=document.querySelector('[data-event="'+event.id+'"]'),prose=article?.querySelector('.prose');if(prose&&!prose.textContent.trim())prose.textContent=text;
   paintRoll(article,event);
   if(s.backend==='nat20')article?.querySelector('.event-buttons')?.remove();
  }
  if(!value.events.length)rows.append(el('p','Подтверждённых событий пока нет.','hint'));
  rows.scrollTop=logTop;
  rollsPrimed=true;
  const inBattle=Boolean(s.combat?.active);
  if(s.scene){($('scene-title')||{}).textContent=s.scene.title;($('scene-description')||{}).textContent=s.scene.description;}
  // Причина несовместимости уже стоит блоком над вводом, вместе с тремя
  // действиями: второй её экземпляр в строке отклика дублировал текст и звал
  // «Обновить сцену», которая ничего не меняет. Строка отклика остаётся
  // свободной для ответа «Проверить базу» / «Сделать копию и обновить».
  if(s.terminal&&!(s.compatibility&&s.compatibility.status!=='ok'))status('unsupported',s.terminal.message);
  // B0: the play area is a conversation; mechanics stay reachable as text through the same engine, never as a palette of buttons.
   actions.replaceChildren();battleActions.replaceChildren();
  const selectedSave=saves.value;saves.replaceChildren();for(const save of current?.saves||[]){const option=el('option',save.name);option.value=save.id;saves.append(option);}if([...saves.options].some(o=>o.value===selectedSave))saves.value=selectedSave;describeSave();restoreButton.disabled=busy||commandBusy||loading||!saves.options.length;saveNow.disabled=busy||commandBusy||loading;
  const native=s.backend==='nat20',battle=s.combat;mapDetails.hidden=native;$('battle-panel').hidden=!battle;
  const battleKey=s.campaign_id+':'+Boolean(battle?.active);if($('battle-panel').dataset.screenKey!==battleKey){$('battle-panel').dataset.screenKey=battleKey;if(battle?.active)$('battle-panel').open=true;}
  if(battle){
   if(battleStatus[1])battleStatus[1].hidden=true;
   const active=battle.actors.find(a=>a.id===battle.current_actor);$('battle-panel').querySelector('summary').textContent=battle.active?'Бой · раунд '+battle.round+' · '+(active?.name||'скрытый участник'):'Бой завершён';
   if(native){
    battleStatus[0].textContent=battle.current_actor?'Ход: '+(active?.name||'скрытый участник'):'Бой разрешён. Можно завершить столкновение.';
    const actors=battleContent.querySelector('.battle-actors');actors.hidden=false;actors.replaceChildren();
    for(const actor of battle.actors)actors.append(el('p',actor.name+' · '+actor.hp+'/'+actor.max_hp+' HP · инициатива '+actor.initiative+(actor.conditions.length?' · '+actor.conditions.map(id=>conditions[id]||id).join(', '):''),'hint'));
    if(battle.terrain)actors.append(el('p',battle.terrain,'hint game-terrain'));
    return;
   }
   const actorButtons=battleContent.querySelectorAll('.battle-actors button');battle.actors.forEach((actor,index)=>{
    const button=actorButtons[index];if(!button)return;const stats=actor.id==='hero'?h:actor;
    button.textContent=actor.name+' · '+(stats.hp??'—')+'/'+(stats.max_hp??'—')+' HP · инициатива '+actor.initiative+(stats.conditions?.length?' · '+stats.conditions.map(id=>conditions[id]||id).join(', '):'');
   });
  }
 }
 async function load(){
  if(!current)return;const cid=campaignId,seq=++stamp;loading=true;try{const value=await transport.load(cid);if(cid===campaignId&&seq===stamp){loading=false;renderPacket(value);if(!commandBusy&&!busy){if(value.feedback)status(value.feedback.kind,value.feedback.text);else if(feedback.dataset.state==='waiting')status('ready','');}return true;}}catch(error){if(cid===campaignId&&seq===stamp){loading=false;packet=null;clearMechanics();$('send').disabled=true;status('error',error.message);}}return false;
 }
 async function dispatch(command){
  if(sendLock||actionBlocked(command.kind==='restore')||!packet)return;const cid=campaignId;
  sendLock=true;
  const version=command.expected_version??packet.snapshot.version,signature=JSON.stringify([cid,command.kind,command.parameters,version]);
  if(retry?.signature!==signature)retry={signature,body:{request_id:crypto.randomUUID(),expected_version:version,kind:command.kind,parameters:command.parameters}};
  commandBusy=true;status(command.kind==='restore'?'restoring':'waiting',command.kind==='restore'?'Восстанавливаем сохранение…':'Действие отправлено. Ждём подтверждения…');renderPacket(packet);
  try{await transport.send(cid,retry.body);retry=null;if(cid===campaignId){await refresh();if(!(await load()))return;status('ready',command.kind==='restore'?'Сохранение восстановлено.':'Действие подтверждено.');if(command.kind==='restore'){$('chat').scrollIntoView({block:'nearest'});feedback.scrollIntoView({block:'nearest'});}}}
  catch(error){if(cid===campaignId){
   if(error.code==='version_conflict'){const fresh=await rejoinScene();status(fresh?'ready':'conflict',fresh?SCENE_UPDATED:SCENE_STALE);}
   else status(error.status===409?'unsupported':'error',error.message);}}
  finally{commandBusy=false;sendLock=false;holdSends();if(packet?.snapshot.campaign_id===campaignId)renderPacket(packet);}
 }
 recover.onclick=protect(async()=>{await refresh();if(!(await load()))return;status('ready','Сцена обновлена. Выберите действие снова.');});
 saveNow.onclick=protect(async()=>{if(busy||commandBusy)return;saveNow.disabled=true;const cid=campaignId;
  try{await baseAPI('/campaigns/'+cid+'/saves',{name:'Контрольная точка · ход '+current.state.turn});if(cid===campaignId){await refresh();if(await load())status('ready','Контрольная точка сохранена.');}}
  finally{saveNow.disabled=false;}
 });
 const restoreDialog=el('dialog'),restoreMessage=el('p'),restoreYes=el('button','Восстановить контрольную точку','primary'),restoreCancel=el('button','Отмена');let restoreCheckpoint=null;
 restoreYes.type=restoreCancel.type='button';restoreDialog.append(el('h2','Восстановление сохранения'),restoreMessage,restoreYes,restoreCancel);document.body.append(restoreDialog);
 restoreButton.onclick=()=>{if(!saves.value||busy||commandBusy||loading)return;restoreCheckpoint=saves.value;restoreMessage.textContent='Вернуться к «'+saves.selectedOptions[0].textContent+'»? Текущее прохождение вернётся к этому состоянию.';restoreDialog.showModal();};
 restoreCancel.onclick=()=>restoreDialog.close();restoreYes.onclick=protect(async()=>{const checkpoint=restoreCheckpoint;restoreDialog.close();await dispatch({kind:'restore',parameters:{checkpoint}});});
 document.addEventListener('solo:render',()=>{const cid=campaignId,native=current.backend==='nat20';document.body.classList.toggle('nat20-game',native);mapDetails.hidden=native;
  if(cid!==rollsCampaign){rollsShown=new Set();rollsPrimed=false;rollsCampaign=cid;}
  if(native){($('scene-title')||{}).textContent='Тестовое приключение Nat20';($('scene-description')||{}).textContent='Проверяйте навыки, начните базовое столкновение и продолжите бой действием или репликой. Ходы подтверждает движок.';}
  packet=packet?.snapshot.campaign_id===cid?packet:null;loading=true;
  if(packet)renderPacket(packet);else clearMechanics();
  status('waiting','Загружаем подтверждённую сцену…');
  $('send').disabled=actionBlocked()||!packet;load();});
 const baseBusy=markBusy;markBusy=function(value,label){baseBusy(value,label);if(value)status('waiting',label||'Мастер отвечает…');else if(feedback.dataset.state==='waiting')status('ready','Мастер ждёт вашей реплики.');if(packet)renderPacket(packet);else $('send').disabled=true;};
 const legacySend=sendAction;sendAction=async function(){
  // n01: замок ставится до первого await — иначе нажатия, накопленные за время
  // загрузки сцены, доходят до отправки все разом и дают одинаковые запросы.
  if(sendLock||sendHeld())return;
  sendLock=true;
  try{
  if(!packet){await load();if(!packet)return;}
  if(actionBlocked())return;
  if(!packet.snapshot.command_turn)return await legacySend();
  const text=$('player-input').value.trim();if(!text)return;
  const cid=campaignId,signature=JSON.stringify([cid,text,packet.snapshot.version]);
  if(!pendingSend||pendingSend.campaign!==cid||pendingSend.text!==text||pendingSend.expected_version===undefined)pendingSend={campaign:cid,text,id:crypto.randomUUID(),expected_version:packet.snapshot.version};
  localStorage.setItem('solo-pending-send',JSON.stringify(pendingSend));
  if(retry?.signature!==signature)retry={signature,body:{request_id:pendingSend.id,expected_version:pendingSend.expected_version,text}};
  markBusy(true,'Мастер слушает…');
  try{const value=await baseAPI('/campaigns/'+cid+'/command-turn',retry.body);retry=null;pendingSend=null;localStorage.removeItem('solo-pending-send');
   if(cid!==campaignId)return;
   if(['resolved','conversation','observation'].includes(value.status))$('player-input').value='';
   await refresh();if(!(await load()))return;
   // Отказ по версии приходит и телом 200 (status='state_version_error'): сцена уже
   // перезапрошена, поэтому игроку — та же одна фраза, а не «Не удалось обработать».
   if(value.status==='state_version_error')return status('ready',SCENE_UPDATED);
   // L02: слово «пересобрать героя» — не ход, а открытие редактора на уровне
   // книги. Контекст книги (черновик, ревизия, отпечаток прогона) отдаёт
   // сервер: экран не собирает его сам. Старого героя заменит собранный, и
   // только после «Пересобрать героя» — до первого хода это бесплатно.
   if(value.status==='gate_rebuild'){
    $('player-input').value='';
    const context=value.rebuild;
    if(!context||!window.soloWizard){status('unsupported',value.narrative||'Редактор героя сейчас недоступен: книга этой кампании не найдена в этом профиле.');return;}
    await window.soloWizard.open({rebuildCampaign:cid,prepared:context,level:context.level});
    return;
   }
   status(({ai_error:'error',rejected:'unsupported'})[value.status]||value.status,value.status==='resolved'?'':value.narrative);
  }catch(error){
   if(cid===campaignId){
    if(error.code==='version_conflict'){const fresh=await rejoinScene();status('conflict',fresh?SCENE_UPDATED:SCENE_STALE);}
    else status(error.status===409?'unsupported':'error',error.message);}
  }
  }finally{sendLock=false;holdSends();markBusy(false);$('player-input').focus();}
 };
 // r210: маршрута /play нет, поэтому и ветки статуса для него нет — обёртка
 // осталась только как точка подмены транспорта для тестов экрана.
 api=async function(path,body,method,absolute){return absolute?await baseAPI(path,body,method,absolute):await baseAPI(path,body,method);};
 window.SoloGameScreen={render:renderPacket,execute:dispatch,setTransport(value){transport=value||http;retry=null;return load();},reload:load};
 // Creation is an explicit new backend choice; existing campaigns are never migrated.
 // The ready-made trial is a developer tool, never the player's path. The normal product is materials -> preparation -> hero wizard -> dialogue, so no launcher for a canned scene is attached to the player's screen.
  // The creation screen opens this dialog from its "Готовое испытание" button and
  // from the mock-build notice, so the element must exist even when no launcher is
  // on the player's rail; only the visible launcher stays developer-gated.
  const launch=el('button','Испытание движка');launch.id='new-nat20-campaign';launch.type='button';launch.hidden=true;if($('new-campaign'))$('new-campaign').after(launch);else document.body.append(launch);launch.hidden=!window.soloDeveloperTools;
 const dialog=el('dialog'),form=el('form'),name=el('input'),intro=el('p','','hint'),errorText=el('p','','hint'),create=el('button','Начать испытание','primary'),cancel=el('button','Отмена');
 dialog.id='nat20-create-dialog';name.id='nat20-campaign-name';name.required=true;name.maxLength=80;name.value='Мирон';name.setAttribute('aria-label','Имя героя и название испытания');intro.setAttribute('role','status');errorText.setAttribute('role','alert');cancel.type='button';create.type='submit';
 form.append(el('h2','Письмо у старого фонаря'),intro,field('Имя героя',name),errorText,create,cancel);dialog.append(form);document.body.append(dialog);
 launch.onclick=protect(async()=>{dialog.showModal();errorText.textContent='';intro.textContent='Проверяем готовность игрового профиля…';create.disabled=true;
  try{const value=await baseAPI('/nat20/options');intro.textContent=value.available?[value.character,value.equipment,value.description].join('. '):value.message;create.disabled=!value.available;name.focus();}catch(error){errorText.textContent=error.message;}
 });
 // No player-facing entry to the canned trial remains: the hero is created by the wizard after preparation.
 cancel.onclick=()=>dialog.close();
 form.onsubmit=protect(async event=>{event.preventDefault();if(create.disabled||busy||commandBusy)return;create.disabled=cancel.disabled=true;errorText.textContent='';intro.textContent='Сервер готовит героя и сцену…';
  try{const value=await baseAPI('/nat20/campaigns',{name:name.value.trim(),scene:'lantern-post'});campaignId=value.id;localStorage.setItem('solo-campaign',value.id);await refresh();await load();dialog.close();view('play');}
  catch(error){errorText.textContent=error.message;}
  finally{create.disabled=cancel.disabled=false;}
 });
 if(current)load();
})();

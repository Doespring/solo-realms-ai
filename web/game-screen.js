/* Compose existing RPG panels. All numbers and action availability come from the adapter. */
(()=>{
 'use strict';
 const play=$('play');play.classList.add('unified-game');
 const hero=$('scene-hero');if(hero){hero.open=true;rail.prepend(hero);}
 const log=el('details',undefined,'disclosure game-log');log.open=true;
 const rows=el('div');log.append(el('summary','Броски и события'),rows);rail.append(log);
 const lastRoll=el('p','Бросков пока нет.','game-last-roll');lastRoll.id='game-last-roll';
 const feedback=el('div',undefined,'game-feedback'),feedbackText=el('span'),recover=el('button','Обновить сцену');
 feedback.id='game-feedback';feedback.setAttribute('role','status');feedback.hidden=true;recover.type='button';recover.hidden=true;feedback.append(feedbackText,recover);$('action-form').before(lastRoll,feedback);
 // The main screen is a conversation: the player types intent and the Master answers. The mechanics stay available to the dialogue but are not offered as a palette of buttons.
 const actions=el('div',undefined,'button-row game-actions');actions.setAttribute('aria-label','Подтверждённые действия');actions.hidden=true;$('action-form').after(actions);
 const restore=el('details',undefined,'disclosure game-restore'),saves=el('select'),restoreButton=el('button','Восстановить');saves.setAttribute('aria-label','Контрольная точка');restoreButton.type='button';restore.append(el('summary','Восстановить сохранение'),saves,restoreButton);rail.append(restore);
 const saveNow=el('button','Сохранить контрольную точку');saveNow.type='button';restore.querySelector('summary').textContent='Сохранения и восстановление';restore.insertBefore(saveNow,saves);
 const saveCaption=el('p','','hint game-save-name');saveCaption.setAttribute('aria-live','polite');saves.after(saveCaption);
 function describeSave(){saveCaption.textContent=saves.selectedOptions[0]?.textContent||'Контрольных точек пока нет.';saves.title=saveCaption.textContent;}saves.onchange=describeSave;
 const battleActions=el('div',undefined,'button-row game-actions');battleActions.hidden=true;{const panel=$('battle-panel');if(panel)panel.querySelector('.battle-content').prepend(battleActions);}
 const battleContent=$('battle-panel')?.querySelector('.battle-content'),mapDetails=el('details',undefined,'game-map');mapDetails.append(el('summary','Тактическая карта и осмотр'));
 const battleStatus=[...battleContent.children].filter(node=>node.tagName==='P').slice(0,2);
 for(const node of [...battleContent.children])if(node!==battleActions&&!battleStatus.includes(node)&&!node.classList.contains('battle-actors'))mapDetails.append(node);
 battleContent.append(mapDetails);
 const baseAPI=api;let packet=null,stamp=0,commandBusy=false,loading=false,retry=null;
 const http={load:cid=>baseAPI('/campaigns/'+cid+'/screen'),send:(cid,command)=>{
  if(command.kind==='restore')return baseAPI('/campaigns/'+cid+'/restore/'+encodeURIComponent(command.parameters.checkpoint),{});
  if(command.kind==='engine')return baseAPI('/campaigns/'+cid+'/commands',{request_id:command.request_id,expected_version:command.expected_version,action:command.parameters});
  return baseAPI('/campaigns/'+cid+'/screen/commands',command);
 }};
 let transport=http;
 function actionBlocked(allowTerminal=false){return busy||commandBusy||loading||(!allowTerminal&&Boolean(packet?.snapshot.terminal));}
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
  // (no opening text here: play.js renders the single GM opening, so the
 // transcript no longer repeats itself)
  body.replaceChildren(el('h3',h.name),el('p','HP '+h.hp+' / '+h.max_hp,'game-hp'));
  if(s.backend==='nat20'){const resources=new Map(h.resources.map(row=>[row.id,row.current]));if(resources.has('death_save:successes')||resources.has('dead')){const death=el('section',undefined,'game-death-state');death.setAttribute('aria-live','polite');death.append(el('p',resources.get('dead')?'Смерть подтверждена Nat20.':h.hp>0?'Герой снова в сознании.':resources.get('death_save:is_stable')?'Герой стабилизирован.':'Герой при смерти.','hint'),el('p','Спасброски от смерти: успехи '+(resources.get('death_save:successes')??'—')+' · провалы '+(resources.get('death_save:failures')??'—'),'hint'));body.append(death);}}
  const flags=[],movement=[];for(const resource of h.resources){
   if(s.backend==='nat20'&&(resource.id.startsWith('death_save:')||resource.id==='dead'))continue;
   if(s.backend==='nat20'&&resource.id.endsWith('_available')){flags.push(resource.label+': '+(resource.current?'да':'нет'));continue;}
   if(s.backend==='nat20'&&['movement_remaining','attacks_remaining'].includes(resource.id)){movement.push(resource.label+': '+resource.current);continue;}
   body.append(el('p',resource.label+': '+resource.current+(resource.maximum==null?'':' / '+resource.maximum),'hint'));
  }
  if(movement.length)body.append(el('p',movement.join(' · '),'hint'));if(flags.length)body.append(el('p',flags.join(' · '),'hint'));
  const conditions={poisoned:'отравление',prone:'лежит',unconscious:'без сознания',stunned:'ошеломление',restrained:'опутан',grappled:'схвачен',blinded:'ослепление',frightened:'испуг'};
  body.append(el('p','Кости хитов: '+h.hit_dice,'hint'),el('p','Состояния: '+(h.conditions.map(id=>conditions[id]||id).join(', ')||'нет'),'hint'));
  const sheet=el('button','Лист героя','quiet sheet-link');sheet.onclick=()=>view('character');body.append(sheet);
  const logTop=rows.scrollTop,openEvents=new Set([...rows.querySelectorAll('.game-event[open]')].map(node=>node.dataset.eventId));
  lastRoll.textContent=rollText(s.last_roll);rows.replaceChildren();
  if(s.backend==='nat20'){const names={check:'Проверка выполнена.',start_combat:'Бой начат.',move:'Перемещение выполнено.',attack:'Атака выполнена.',end_turn:'Ход завершён.',end_combat:'Бой завершён.',short_rest:'Короткий отдых завершён.',long_rest:'Длительный отдых завершён.'};for(const prose of $('chat').querySelectorAll('.prose')){const match=/^Nat20 confirmed: ([a-z_]+)$/.exec(prose.textContent.trim());if(match&&names[match[1]])prose.textContent=names[match[1]];}}
  for(const event of [...value.events].reverse()){const row=el('details',undefined,'game-event');row.dataset.eventId=event.id;row.open=openEvents.has(event.id);row.append(el('summary','Событие '+event.sequence+' · '+event.text),el('p',event.roll?rollText(event.roll):event.text,'hint'));rows.append(row);
   const article=document.querySelector('[data-event="'+event.id+'"]'),prose=article?.querySelector('.prose');if(prose&&(!prose.textContent.trim()||(s.backend==='nat20'&&prose.textContent.trim().startsWith('Nat20 confirmed:'))))prose.textContent=event.text;
   if(s.backend==='nat20')article?.querySelector('.event-buttons')?.remove();
  }
  if(!value.events.length)rows.append(el('p','Подтверждённых событий пока нет.','hint'));
  rows.scrollTop=logTop;
  const inBattle=Boolean(s.combat?.active);
  if(s.scene){($('scene-title')||{}).textContent=s.scene.title;($('scene-description')||{}).textContent=s.scene.description;}
  if(s.terminal)status('unsupported',s.terminal.message);
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
  if(actionBlocked(command.kind==='restore')||!packet)return;const cid=campaignId;
  const version=command.expected_version??packet.snapshot.version,signature=JSON.stringify([cid,command.kind,command.parameters,version]);
  if(retry?.signature!==signature)retry={signature,body:{request_id:crypto.randomUUID(),expected_version:version,kind:command.kind,parameters:command.parameters}};
  commandBusy=true;status(command.kind==='restore'?'restoring':'waiting',command.kind==='restore'?'Восстанавливаем сохранение…':'Действие отправлено. Ждём подтверждения…');renderPacket(packet);
  try{await transport.send(cid,retry.body);retry=null;if(cid===campaignId){await refresh();if(!(await load()))return;status('ready',command.kind==='restore'?'Сохранение восстановлено.':'Действие подтверждено.');if(command.kind==='restore'){$('chat').scrollIntoView({block:'nearest'});feedback.scrollIntoView({block:'nearest'});}}}
  catch(error){if(cid===campaignId){if(error.code==='version_conflict'){retry=null;await refresh();await load();}status(error.code==='version_conflict'?'conflict':error.status===409?'unsupported':'error',error.message);}}
  finally{commandBusy=false;if(packet?.snapshot.campaign_id===campaignId)renderPacket(packet);}
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
  if(native){($('scene-title')||{}).textContent='Тестовое приключение Nat20';($('scene-description')||{}).textContent='Проверяйте навыки, начните базовое столкновение и продолжите бой действием или репликой. Ходы подтверждает движок.';}
  packet=packet?.snapshot.campaign_id===cid?packet:null;loading=true;
  if(packet)renderPacket(packet);else clearMechanics();
  status('waiting','Загружаем подтверждённую сцену…');
  $('send').disabled=actionBlocked()||!packet;load();});
 const baseBusy=markBusy;markBusy=function(value,label){baseBusy(value,label);if(value)status('waiting',label||'Мастер отвечает…');else if(feedback.dataset.state==='waiting')status('ready','Мастер ждёт вашей реплики.');if(packet)renderPacket(packet);else $('send').disabled=true;};
 const legacySend=sendAction;sendAction=async function(){
  if(!packet){await load();if(!packet)return;}
  if(actionBlocked())return;
  if(!packet.snapshot.command_turn)return legacySend();
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
   status(({state_version_error:'conflict',ai_error:'error',rejected:'unsupported'})[value.status]||value.status,value.status==='resolved'?'Действие подтверждено.':value.narrative);
  }catch(error){status('error',error.message);}
  finally{markBusy(false);$('player-input').focus();}
 };
 api=async function(path,body,method){
  try{
   const value=await baseAPI(path,body,method);
   if(path.endsWith('/play'))status(value.kind==='reply'?'clarification':'ready',value.kind==='reply'?value.reply:'Действие подтверждено.');
   return value;
  }catch(error){if(path.endsWith('/play'))status('error',error.message);throw error;}
 };
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

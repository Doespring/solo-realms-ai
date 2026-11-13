'use strict';
(()=>{
 const panel=el('details',undefined,'disclosure battle-panel');panel.id='battle-panel';panel.append(el('summary','Тактическая сцена'));
 rail.prepend(panel);panel.open=false;
 const content=el('div',undefined,'battle-content'),status=el('p',undefined,'hint'),setup=el('div',undefined,'battle-setup');
 const weapon=el('select');weapon.id='battle-weapon';
 for(const[value,label]of[['unarmed','Безоружный удар'],['club','Дубинка'],['longsword','Длинный меч'],['shortbow','Короткий лук']]){const o=el('option',label);o.value=value;weapon.append(o);}
 const confirmStart=el('input');confirmStart.type='checkbox';confirmStart.id='battle-confirm';
 const terrain=el('select'),seed=el('input'),lighting=el('select'),mapFile=el('input'),importState=el('p',undefined,'hint');let importedMap=null;
 terrain.id='battle-terrain';seed.type='number';seed.min=0;seed.max=2147483647;seed.value=1;lighting.id='battle-lighting';
 for(const[value,label]of[['room','Комната'],['forest_road','Лесная дорога'],['ruins','Руины с секретным проходом'],['bridge','Разрушенный мост'],['balcony','Зал с балконом'],['cellar','Дом с подвалом']]){const o=el('option',label);o.value=value;terrain.append(o);}
 for(const[value,label]of[['bright','Яркий свет'],['dim','Тусклый свет'],['dark','Тьма и светильники']]){const o=el('option',label);o.value=value;lighting.append(o);}
 mapFile.type='file';mapFile.accept='.json';mapFile.onchange=protect(async()=>{importedMap=null;importState.textContent='';const file=mapFile.files[0];if(!file){importedMap=null;importState.textContent='';return;}if(file.size>1500000)throw new Error('Карта превышает 1,5 МБ.');importedMap=JSON.parse(await file.text());if(!importedMap.map||!importedMap.hero_position||!importedMap.enemy_position){importedMap=null;throw new Error('Нужны поля map, hero_position и enemy_position.');}importState.textContent='Выбрана карта: '+file.name+'. Сервер проверит геометрию перед началом.';});
 const start=el('button','Начать подтверждённое столкновение');start.type='button';
 setup.append(el('p','Добавление боя меняет текущую кампанию. Подтвердите столкновение и используемое оружие. Оружие берётся из инвентаря. Если снаряжение прежней кампании ещё не введено, первое подтверждение добавит выбранное оружие (для лука — 20 стрел) в журнал и инвентарь. Карта собирается локально без AI-вызова.','hint'),field('Местность',terrain),field('Seed генерации',seed),field('Освещение',lighting),field('Или загрузить структурированную карту JSON',mapFile),importState,field('Оружие столкновения',weapon),field('Я подтверждаю добавление противника в текущую локацию',confirmStart),start);
 const tools=el('div',undefined,'button-row'),zoomOut=el('button','−'),zoomIn=el('button','+'),center=el('button','Центрировать'),level=el('select');
 zoomOut.type=zoomIn.type=center.type='button';zoomOut.setAttribute('aria-label','Уменьшить карту');zoomIn.setAttribute('aria-label','Увеличить карту');level.setAttribute('aria-label','Уровень поверхности карты');tools.append(zoomOut,zoomIn,center,field('Высота Z',level));
 const measure=el('button','Измерить отрисовку карты'),measured=el('output');measure.type='button';tools.append(measure,measured);
 const canvas=el('canvas');canvas.id='battle-map';canvas.width=900;canvas.height=470;canvas.tabIndex=0;canvas.setAttribute('aria-label','Тактическая карта. Выберите клетку мышью или используйте поля координат ниже.');
 const selection=el('p','Выберите клетку или участника.','hint'),coordinates=el('div',undefined,'form-grid'),xInput=el('input'),yInput=el('input');
 for(const input of[xInput,yInput]){input.type='number';input.min=1;input.max=80;input.value=1;}
 coordinates.append(field('Клетка X',xInput),field('Клетка Y',yInput));
 const move=el('button','Перейти в выбранную клетку'),preview=el('button','Проверить маршрут'),attack=el('button','Атаковать выбранную цель'),nonlethal=el('input');
 nonlethal.type='checkbox';for(const b of[move,preview,attack])b.type='button';
 const commands=el('div',undefined,'button-row');commands.append(preview,move,attack,field('Нелетальный удар в ближнем бою',nonlethal));
 const actions=el('div',undefined,'button-row'),actors=el('div',undefined,'battle-actors'),reaction=el('div',undefined,'button-row'),objects=el('div',undefined,'battle-objects'),stairs=el('div',undefined,'button-row');
 const search=el('button','Искать на выбранной клетке'),fall=el('button','Падение на выбранную поверхность');search.type=fall.type='button';commands.append(search,fall);
 search.onclick=protect(()=>send('search',{position:destination()}));
 fall.onclick=protect(async()=>{needCampaign();const info=await api('/campaigns/'+campaignId+'/combat/fall-preview',{position:destination()});if(confirm(info.warning))await send('drop',{position:destination(),confirm:true});});
 const actionButtons=[];
 for(const[kind,label]of[['dash','Рывок'],['dodge','Уклонение'],['disengage','Отход'],['stand_up','Встать'],['bonus_dash','Бонус: рывок'],['bonus_disengage','Бонус: отход'],['end_turn','Закончить ход'],['npc_turn','Рассчитать ход NPC']]){
  const b=el('button',label);b.type='button';b.dataset.command=kind;b.onclick=protect(()=>send(kind));actions.append(b);actionButtons.push(b);
 }
 for(const[choice,label]of[['attack','Реакция: атаковать'],['decline','Реакция: отказаться']]){const b=el('button',label);b.type='button';b.onclick=protect(()=>send('reaction',{choice}));reaction.append(b);}
 const debug=el('details',undefined,'disclosure developer-only');debug.append(el('summary','Разработчик: геометрия и тестовое столкновение'),setup,measure,measured,commands,actions,reaction);$('master').append(debug);
 const pendingInfo=el('p',undefined,'hint');content.append(status,pendingInfo,tools,canvas,selection,stairs,actors,objects,el('p','Клик осматривает клетку или участника. Действуйте репликой: «Иду на выбранную клетку», «Атакую выбранную цель». Клетка — 5 футов; X/Y с 1, Z с 0 (подземные уровни отрицательные). Колесо — масштаб; правая кнопка или Shift — камера. Затемнение — прошлое наблюдение, ? — последняя известная позиция.','hint'));
 const selectedContext=el('button','Выбрать для реплики');selectedContext.type='button';selection.after(selectedContext);selectedContext.onclick=()=>{if(!selected)return;const actor=battle.actors.find(a=>a.position.x===selected.x&&a.position.y===selected.y&&a.position.z===selected.z);const cell=battle.map.cells.find(c=>c.x===selected.x&&c.y===selected.y&&c.z===selected.z);if(actor)window.soloSelect?.({kind:'actor',id:actor.id},actor.name);else if(cell?.visible)window.soloSelect?.({kind:'cell',x:selected.x+1,y:selected.y+1,z:selected.z},'Клетка '+(selected.x+1)+', '+(selected.y+1)+', Z '+selected.z);else return;$('player-input').focus();};
 const keyboard=el('details',undefined,'disclosure'),inspectCell=el('button','Осмотреть клетку');inspectCell.type='button';keyboard.append(el('summary','Выбрать клетку без мыши'),coordinates,inspectCell);content.append(keyboard);inspectCell.onclick=()=>{if(battle)choose(destination());};
 panel.append(content);
 let battle=null,target=null,selected=null,cellSize=30,offset={x:15,y:20},drag=null,route=[],sending=false,previousVersion=null,motion=null,previousTurn=null,previousEvent=null,fit=true;
 const images={};for(const name of['tile-tile','tile-wall','tile-water','tile-grass','tile-wood','tile-stairs_down','tile-door_closed','tile-door_open','tile-tree','tile-bridge','token-yellow','token-red','token-green']){const image=new Image();images[name]=image;image.onload=()=>draw();image.src='/static/visual/tiles/'+name+'.png';}
 new ResizeObserver(()=>{if(canvas.clientWidth>0){canvas.width=Math.max(300,Math.round(canvas.clientWidth));canvas.height=320;if(fit&&battle)cellSize=Math.max(16,Math.min(34,(canvas.width-30)/battle.map.width,(canvas.height-40)/battle.map.height));draw();}}).observe(canvas);
 measure.onclick=protect(async()=>{if(!battle)throw new Error('Откройте карту.');measure.disabled=true;const times=[],costs=[];try{for(let i=0;i<45;i++){await new Promise(resolve=>requestAnimationFrame(t=>{times.push(t);draw();costs.push(window.soloMapRenderMs);resolve();}));}costs.sort((a,b)=>a-b);measured.textContent=((times.length-1)*1000/(times.at(-1)-times[0])).toFixed(1)+' FPS · медиана рисования '+costs[Math.floor(costs.length/2)]+' мс · 45 кадров';}finally{measure.disabled=false;}});
 const destination=()=>({x:Number(xInput.value)-1,y:Number(yInput.value)-1,z:Number(level.value)});
 async function send(kind,extra={}){
  needCampaign();if(busy||sending)throw new Error('Дождитесь завершения текущей команды.');
  sending=true;
  try{await api('/campaigns/'+campaignId+'/combat/command',{request_id:crypto.randomUUID(),expected_version:current.version,kind,...extra});route=[];await refresh();}
  finally{sending=false;}
 }
 start.onclick=protect(async()=>{needCampaign();if(busy||sending)return;if(!confirmStart.checked)throw new Error('Подтвердите добавление столкновения.');sending=true;try{const pending=current.state.world?.pending_encounter;const field=pending?{proposal:pending.id}:importedMap?{imported_map:importedMap.map,hero_position:importedMap.hero_position,enemy_position:importedMap.enemy_position}:{template:terrain.value,description:{environment:terrain.value,seed:Number(seed.value),lighting:lighting.value}};await api('/campaigns/'+campaignId+'/combat/start',{request_id:crypto.randomUUID(),expected_version:current.version,confirm:true,hero_weapon:weapon.value,...field});confirmStart.checked=false;panel.open=true;offset={x:30,y:30};await refresh();}finally{sending=false;}});
 move.onclick=protect(()=>send('move',{position:destination()}));
 preview.onclick=protect(async()=>{needCampaign();const result=await api('/campaigns/'+campaignId+'/combat/move-preview',{position:destination()});route=result.path;selection.textContent='Стоимость маршрута: '+result.cost_ft+' футов. Реакции противников могут прервать движение.';draw();});
 attack.onclick=protect(()=>{if(!target)throw new Error('Выберите видимую цель.');return send('attack',{target,nonlethal:nonlethal.checked});});
 function render(c){
  const prior=battle;battle=c.state.combat||null;target=null;selected=null;route=[];selection.textContent='Выберите клетку или участника.';selectedContext.hidden=true;if(previousVersion!==c.version||!battle)motion=null;
  weapon.disabled=!!c.state.economy;
  if(c.state.economy){const equipped=c.state.economy.items.find(i=>i.id===c.state.economy.equipment.weapon);weapon.value=equipped?.catalog||'unarmed';}
  setup.hidden=!!battle?.active;
  const pending=c.state.world?.pending_encounter;for(const input of[terrain,seed,lighting,mapFile])input.disabled=!!pending;start.textContent=pending?'Подтвердить подготовленное столкновение':'Начать подтверждённое столкновение';confirmStart.closest('label').firstChild.textContent=pending?'Я подтверждаю предложенное столкновение и объявленный риск':'Я подтверждаю добавление противника в текущую локацию';
  pendingInfo.hidden=!pending;pendingInfo.textContent=pending?pending.text+' Для согласия напишите «Подтверждаю столкновение».':' ';panel.hidden=!battle&&!pending;workspace.classList.toggle('has-battle',!!battle);if(battle||pending)panel.open=true;
  for(const n of[tools,canvas,selection,stairs,keyboard,coordinates,commands,actions,actors,objects])n.hidden=!battle;
  reaction.hidden=!battle?.pending_reaction;
  if(!battle){status.textContent='Боя нет. Кампания остаётся в режиме повествования.';return;}
  const hero=battle.actors.find(a=>a.team==='hero'),active=battle.active,heroTurn=battle.current_actor===hero.id,paused=!!battle.pending_death||!!battle.pending_reaction;
  status.textContent='Раунд '+battle.round+' · '+(active?(heroTurn?'Ваш ход':battle.current_actor?'Ход: '+battle.actors.find(a=>a.id===battle.current_actor)?.name:'Ход скрытого участника'):'Бой завершён')+' · движение '+hero.movement_left_ft+' фт · действие '+(hero.action?'доступно':'использовано')+' · бонус '+(hero.bonus_action?'свободен':'использован')+' · реакция '+(hero.reaction?'доступна':'использована')+' · дистанция оружия '+hero.attack_range_ft+' фт';
  if(battle.pending_death)status.textContent+=' · Смертельный результат сохранён; необратимый переход остановлен. Можно сохранить или откатить ход.';
  const oldLevel=level.value;level.replaceChildren();const zs=[...new Set(battle.map.cells.map(c=>c.z))].sort((a,b)=>a-b);
  for(const z of zs){const o=el('option',String(z));o.value=z;level.append(o);}level.value=zs.includes(hero.position.z)?String(hero.position.z):oldLevel;
  xInput.max=battle.map.width;yInput.max=battle.map.height;
  for(const b of actionButtons){const k=b.dataset.command;b.hidden=k.startsWith('bonus_')&&!hero.registered_bonus;b.disabled=!active||paused||(k==='npc_turn'?heroTurn:!heroTurn)||(['dash','dodge','disengage'].includes(k)&&!hero.action)||(k.startsWith('bonus_')&&!hero.bonus_action);}
  move.disabled=preview.disabled=fall.disabled=!active||!heroTurn||paused;attack.disabled=search.disabled=move.disabled||!hero.action;actors.replaceChildren();stairs.replaceChildren();objects.replaceChildren();
  for(const a of battle.actors){const b=el('button',a.name+' · '+a.hp+'/'+a.max_hp+' хитов · инициатива '+a.initiative+(a.conditions.length?' · '+a.conditions.join(', '):''));b.type='button';b.onclick=()=>choose(a.position,a);actors.append(b);}
  for(const o of battle.map.objects){const b=el('button','Осмотреть: '+o.name);b.type='button';b.onclick=()=>window.soloInspect?.({title:o.name,icon:o.kind==='door'?'door':'search',text:o.kind==='door'?(o.open?'Дверь открыта.':'Дверь закрыта.'):o.kind==='light'?(o.enabled?'Свет горит.':'Источник света погашен.'):'Видимый объект сцены.',rows:[['Клетка',(o.position.x+1)+', '+(o.position.y+1)+', Z '+o.position.z]],selection:{kind:'object',id:o.id}});objects.append(b);}
  const latest=[...c.history].reverse().find(h=>!h.undone),positions=latest?.results.flatMap(r=>(r.operations||[]).filter(op=>op.kind==='move_step'&&op.actor===hero.id).map(op=>op.position))||[];
  if(prior&&c.state.turn>previousTurn&&latest?.id!==previousEvent&&positions.length&&$('effects').checked&&!matchMedia('(prefers-reduced-motion: reduce)').matches){const before=prior.actors.find(a=>a.id===hero.id);if(before)motion={id:hero.id,path:[before.position,...positions],start:performance.now(),duration:Math.min(650,positions.length*90)};}
  previousTurn=c.state.turn;previousEvent=latest?.id;
  draw();
  if(previousVersion!==null&&previousVersion!==c.version&&$('effects').checked&&!matchMedia('(prefers-reduced-motion: reduce)').matches)canvas.animate([{opacity:.75},{opacity:1}],{duration:160});
  previousVersion=c.version;
 }
 function choose(position,actor=null){
  selected=position;target=actor?.team==='enemy'?actor.id:null;xInput.value=position.x+1;yInput.value=position.y+1;level.value=position.z;
  const cell=battle.map.cells.find(c=>c.x===position.x&&c.y===position.y&&c.z===position.z);
  selection.textContent=actor?actor.name+' · дистанция '+(actor.distance_from_hero_ft??0)+' футов':cell?'Клетка '+(position.x+1)+', '+(position.y+1)+' · Z '+position.z+' · '+({floor:'пол',grass:'трава',road:'дорога',water:'вода',rock:'камень',wall:'стена',wood:'дерево',stairs:'лестница',ladder:'лестница',bridge:'мост'}[cell.terrain]||'поверхность')+' · стоимость местности ×'+cell.move_cost+' · укрытие '+cell.cover+(cell.blocked?' · непроходима':'')+(cell.visible?'':' · последнее наблюдение'):'Эта поверхность ещё не исследована.';
  selectedContext.hidden=!actor&&!cell?.visible;
  stairs.replaceChildren();for(const p of cell?.links||[]){const b=el('button','Осмотреть ступень → '+(p.x+1)+', '+(p.y+1)+', Z '+p.z);b.type='button';b.onclick=()=>choose(p);stairs.append(b);}
  draw();
 }
 function draw(){
  const began=performance.now(),ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
  const colors=getComputedStyle(document.documentElement),ink=colors.getPropertyValue('--text').trim()||'#ddd';
  ctx.fillStyle=colors.getPropertyValue('--bg').trim()||'#18202b';ctx.fillRect(0,0,canvas.width,canvas.height);
  if(!battle)return;const z=Number(level.value);
  for(const c of battle.map.cells){
   if(c.z!==z)continue;const px=offset.x+c.x*cellSize,py=offset.y+c.y*cellSize;
   ctx.globalAlpha=(c.visible?1:0.28)*(c.light==='dark'?.65:c.light==='dim'?.82:1);ctx.fillStyle=c.terrain==='wall'?'#424951':c.terrain==='water'?'#346880':c.terrain==='grass'?'#54755a':['stairs','ladder'].includes(c.terrain)?'#957654':c.terrain==='bridge'||c.terrain==='wood'?'#796552':c.terrain==='road'?'#8b8064':'#667278';ctx.fillRect(px,py,cellSize-1,cellSize-1);
   ctx.strokeStyle='rgba(255,255,255,.15)';ctx.lineWidth=1;
   for(let k=1;k<4;k++){ctx.beginPath();if(c.terrain==='water'){ctx.moveTo(px+3,py+k*cellSize/4);ctx.quadraticCurveTo(px+cellSize*.5,py+k*cellSize/4-3,px+cellSize-3,py+k*cellSize/4);}else if(['wood','bridge','stairs','ladder','wall'].includes(c.terrain)){ctx.moveTo(px+2,py+k*cellSize/4);ctx.lineTo(px+cellSize-3,py+k*cellSize/4);}else{const tx=px+((c.x*13+c.y*7+k*11)%Math.max(8,Math.floor(cellSize-6)))+3;ctx.moveTo(tx,py+cellSize*.55);ctx.lineTo(tx+2,py+cellSize*.7);}ctx.stroke();}
   if(c.terrain==='wood'&&c.blocked){ctx.beginPath();ctx.arc(px+cellSize*.5,py+cellSize*.5,cellSize*.3,0,Math.PI*2);ctx.fillStyle='#304f35';ctx.fill();}
   if(c.terrain==='rock'){ctx.fillStyle='#777e83';ctx.beginPath();ctx.moveTo(px+cellSize*.2,py+cellSize*.7);ctx.lineTo(px+cellSize*.35,py+cellSize*.25);ctx.lineTo(px+cellSize*.75,py+cellSize*.3);ctx.lineTo(px+cellSize*.85,py+cellSize*.75);ctx.fill();}
   const texture=images['tile-'+({floor:'tile',wall:'wall',water:'water',grass:'grass',wood:c.blocked?'tree':'wood',stairs:'stairs_down',ladder:'stairs_down',bridge:'bridge',road:'grass'}[c.terrain]||'tile')];if(texture?.complete&&texture.naturalWidth){ctx.fillStyle='#e8d8b7';ctx.fillRect(px,py,cellSize-1,cellSize-1);ctx.drawImage(texture,px+1,py+1,cellSize-3,cellSize-3);}
   if(cellSize>=28){ctx.fillStyle='#292421';ctx.font='9px system-ui';ctx.fillText((c.x+1)+','+(c.y+1),px+3,py+10);}
  }
  ctx.globalAlpha=1;ctx.strokeStyle='#e4ba62';ctx.lineWidth=3;ctx.beginPath();
  for(const[p,i]of route.map((p,i)=>[p,i]))if(p.z===z){const px=offset.x+(p.x+.5)*cellSize,py=offset.y+(p.y+.5)*cellSize;i?ctx.lineTo(px,py):ctx.moveTo(px,py);}ctx.stroke();
  for(const a of [...battle.last_seen.map(a=>({...a,team:'ghost'})),...battle.actors]){
   if(a.position.z!==z)continue;let position=a.position;if(motion?.id===a.id){const progress=Math.min(1,(performance.now()-motion.start)/motion.duration),index=progress*(motion.path.length-1),first=motion.path[Math.floor(index)],second=motion.path[Math.min(motion.path.length-1,Math.floor(index)+1)],t=index%1;if(first.z===second.z)position={x:first.x+(second.x-first.x)*t,y:first.y+(second.y-first.y)*t,z:first.z};if(progress>=1)motion=null;}const px=offset.x+(position.x+.5)*cellSize,py=offset.y+(position.y+.5)*cellSize;
   ctx.globalAlpha=a.team==='ghost'?0.4:1;ctx.beginPath();ctx.arc(px,py,cellSize*.32,0,Math.PI*2);ctx.fillStyle=a.team==='hero'?'#c6a44d':a.team==='ally'?'#508b83':'#a55a55';ctx.fill();ctx.strokeStyle=battle.current_actor===a.id?'#fff':'#252d36';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#fff';ctx.font='bold '+Math.max(12,cellSize*.3)+'px system-ui';ctx.textAlign='center';ctx.fillText(a.team==='ghost'?'?':a.name[0],px,py+5);
   const token=images[a.team==='hero'?'token-yellow':a.team==='ally'?'token-green':'token-red'];if(a.team!=='ghost'&&token?.complete&&token.naturalWidth)ctx.drawImage(token,px-cellSize*.38,py-cellSize*.38,cellSize*.76,cellSize*.76);
  }
  ctx.globalAlpha=1;ctx.textAlign='left';
  if(selected&&selected.z===z){ctx.strokeStyle=ink;ctx.lineWidth=3;ctx.strokeRect(offset.x+selected.x*cellSize+1,offset.y+selected.y*cellSize+1,cellSize-3,cellSize-3);}
  window.soloMapRenderMs=+(performance.now()-began).toFixed(2);
  canvas.dataset.renderMs=String(window.soloMapRenderMs);
  if(motion)requestAnimationFrame(draw);
 }
 function zoom(f){fit=false;cellSize=Math.max(16,Math.min(80,cellSize*f));draw();}
 zoomIn.onclick=()=>zoom(1.2);zoomOut.onclick=()=>zoom(1/1.2);center.onclick=()=>{if(battle){const hero=battle.actors.find(a=>a.team==='hero');offset={x:canvas.width/2-(hero.position.x+.5)*cellSize,y:canvas.height/2-(hero.position.y+.5)*cellSize};}draw();};level.onchange=draw;
 canvas.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?1.1:1/1.1);},{passive:false});
 const point=e=>{const rect=canvas.getBoundingClientRect();return{x:(e.clientX-rect.left)*canvas.width/rect.width,y:(e.clientY-rect.top)*canvas.height/rect.height};};
 canvas.oncontextmenu=e=>e.preventDefault();
 canvas.onpointerdown=e=>{if(e.button===2||e.shiftKey){drag={point:point(e),origin:{...offset}};canvas.setPointerCapture(e.pointerId);}};
 canvas.onpointermove=e=>{if(drag){const p=point(e);offset={x:drag.origin.x+p.x-drag.point.x,y:drag.origin.y+p.y-drag.point.y};draw();}};
 canvas.onpointerup=e=>{if(drag){drag=null;return;}if(!battle)return;const p=point(e),pos={x:Math.floor((p.x-offset.x)/cellSize),y:Math.floor((p.y-offset.y)/cellSize),z:Number(level.value)};if(pos.x<0||pos.y<0||pos.x>=battle.map.width||pos.y>=battle.map.height)return;const actor=battle.actors.find(a=>a.position.x===pos.x&&a.position.y===pos.y&&a.position.z===pos.z);choose(pos,actor);};
 document.addEventListener('solo:render',e=>render(e.detail));document.addEventListener('change',e=>{if(e.target.id==='theme')draw();});
 if(current)render(current);
})();

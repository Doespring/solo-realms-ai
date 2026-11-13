/* r222 — поле боя рядом с лентой.
   Макет design/screens/04-combat.html: заголовок поля, очередь ходов, сетка,
   обозначения и сноска. Данные приходят только из пакета экрана
   (`/api/campaigns/<id>/screen` → `snapshot.combat`), своих чисел и своих
   списков действий здесь нет.

   Геометрия рисуется, если пакет её принёс: сначала старая форма
   `combat.map.cells` (проекция combat_view), затем форма Nat20
   `combat.grid` + `actors[].position`. Пока проекция Nat20 сетку не отдаёт,
   плашка честно говорит, что разметки нет, и показывает то, что есть:
   порядок ходов, раунд и подписи укрытий из списка действий движка. */
'use strict';
(()=>{
 // Слова состояний — ОДИН источник для ленты и карты: game-screen.js берёт
 // словарь отсюда, поэтому «схвачен» на клетке и в ленте пишется одинаково.
 const CONDITIONS={poisoned:'отравление',prone:'лежит',unconscious:'без сознания',stunned:'ошеломление',restrained:'опутан',grappled:'схвачен',blinded:'ослепление',frightened:'испуг'};
 // Степени укрытия — дословно из solo/nat20_content.py:COVER_NAMES: карта не
 // переводит их по-своему, иначе лента и клетка назовут одно по-разному.
 const COVER={half:'половинное',three_quarters:'три четверти',total:'полное'};
 window.SOLO_CONDITIONS=CONDITIONS;
 const condition=id=>CONDITIONS[id]||id;
 const coverWord=degree=>COVER[degree]||String(degree);

 function cellKey(x,y){return x+','+y;}

 function moveCells(commands){
  const set=new Set();
  for(const command of commands||[]){
   const parameters=command&&command.parameters;
   if(!parameters||parameters.kind!=='move'||!parameters.position)continue;
   const position=parameters.position;
   set.add(cellKey(Number(position.x),Number(position.y)));
  }
  return set;
 }

 // Что пакет реально принёс про поле: две известные формы, третьей нет.
 function geometry(battle){
  if(!battle||!battle.active)return null;
  const map=battle.map;
  if(map&&Array.isArray(map.cells)&&map.cells.length&&Number.isFinite(map.width)&&Number.isFinite(map.height)){
   return {
    width:map.width,height:map.height,cellFt:5,name:map.name||'',
    cells:map.cells.map(cell=>({x:cell.x,y:cell.y,terrain:cell.terrain||'floor',
     blocked:Boolean(cell.blocked),cover:cell.cover||0,visible:cell.visible!==false})),
    actors:(battle.actors||[]).filter(actor=>actor.position).map(actor=>({id:actor.id,name:actor.name,
     team:actor.team,hp:actor.hp,max_hp:actor.max_hp,conditions:actor.conditions||[],
     x:actor.position.x,y:actor.position.y})),
   };
  }
  const grid=battle.grid;
  const actors=Array.isArray(battle.actors)?battle.actors:[];
  if(grid&&Number.isFinite(grid.width)&&Number.isFinite(grid.height)&&actors.length&&actors.every(actor=>actor.position)){
   const blocked=new Set(grid.blocked||grid.blocked_cells||[]);
   const cover=grid.cover||grid.cover_cells||{};
   const cells=[];
   for(let y=0;y<grid.height;y++)for(let x=0;x<grid.width;x++){
    const key=cellKey(x,y);
    cells.push({x,y,terrain:blocked.has(key)?'wall':'floor',blocked:blocked.has(key),cover:cover[key]||0,visible:true});
   }
   return {
    width:grid.width,height:grid.height,cellFt:grid.cell_size_ft||grid.cells_ft||5,name:grid.name||'',
    cells,actors:actors.map(actor=>({id:actor.id,name:actor.name,team:actor.team,hp:actor.hp,
     max_hp:actor.max_hp,conditions:actor.conditions||[],x:actor.position.x,y:actor.position.y})),
   };
  }
  return null;
 }

 function mount(){
  const play=document.getElementById('play');
  const layout=play&&play.querySelector('.workspace-layout');
  if(!layout)return null;
  let board=document.getElementById('board');
  if(!board){
   board=el('aside',undefined,'board');
   board.id='board';
   board.setAttribute('aria-label','Поле боя');
   board.hidden=true;
   layout.append(board);
  }
  return board;
 }

 function turnsList(battle){
  const list=el('ol',undefined,'board__turns');
  list.setAttribute('aria-label','Очередь ходов');
  const actors=[...(battle.actors||[])].sort((first,second)=>(second.initiative??0)-(first.initiative??0));
  actors.forEach((actor,index)=>{
   const row=el('li',undefined,'board__turn');
   if(actor.id===battle.current_actor)row.classList.add('is-active');
   row.append(el('span',String(index+1),'board__turn-n'));
   row.append(el('span',actor.name,'board__turn-name'));
   const hp=el('span',(actor.hp??'—')+'/'+(actor.max_hp??'—'),'board__turn-hp');
   if(Number.isFinite(actor.hp)&&Number.isFinite(actor.max_hp)&&actor.hp<actor.max_hp)hp.classList.add('is-hurt');
   row.append(hp);
   if(actor.conditions&&actor.conditions.length)row.append(el('span',actor.conditions.map(condition).join(' · '),'board__turn-cond'));
   list.append(row);
  });
  return list;
 }

 function grid(battle,shape,moves){
  const wrap=el('div',undefined,'board__map');
  const cells=el('div',undefined,'board__grid');
  cells.style.setProperty('--cols',String(shape.width));
  cells.setAttribute('role','img');
  cells.setAttribute('aria-label','Сетка '+shape.width+' на '+shape.height+'. Клетка '+shape.cellFt+' футов.');
  const byCell=new Map();
  for(const actor of shape.actors||[]){
   const key=cellKey(actor.x,actor.y);
   if(!byCell.has(key))byCell.set(key,[]);
   byCell.get(key).push(actor);
  }
  for(const cell of shape.cells){
   const node=el('div',undefined,'board__cell');
   node.dataset.x=String(cell.x);
   node.dataset.y=String(cell.y);
   if(cell.blocked)node.classList.add('board__cell--wall');
   else if(cell.cover)node.classList.add('board__cell--cover');
   if(!cell.visible)node.classList.add('board__cell--unseen');
   const key=cellKey(cell.x,cell.y);
   if(moves.has(key)&&!cell.blocked)node.classList.add('board__cell--move');
   const title=[];
   if(cell.blocked)title.push('непроходимо');
   if(cell.cover)title.push('укрытие: '+coverWord(cell.cover));
   for(const actor of byCell.get(key)||[]){
    const token=el('span',actor.name.slice(0,1).toUpperCase(),'board__token board__token--'+
     (actor.team==='hero'?'hero':actor.team==='ally'?'ally':'foe'));
    token.title=actor.name;
    if(actor.hp!==undefined&&actor.hp<=0)token.classList.add('board__token--dead');
    node.append(token);
    title.push(actor.name);
   }
   if(title.length)node.title=title.join(' · ');
   cells.append(node);
  }
  wrap.append(cells);
  return wrap;
 }

 function legend(shape,moves){
  const items=[];
  const actors=(shape&&shape.actors)||[];
  const has=team=>actors.some(actor=>actor.team===team);
  if(has('hero')||!shape)items.push(['is-hero','ты']);
  if(has('ally'))items.push(['is-ally','союзник']);
  if(has('foe')||!shape)items.push(['is-foe','противник']);
  if(shape&&shape.cells.some(cell=>cell.cover))items.push(['is-cover','укрытие']);
  if(shape&&shape.cells.some(cell=>cell.blocked))items.push(['is-cover','непроходимо']);
  if(moves.size)items.push(['is-move','можно дойти']);
  const list=el('ul',undefined,'board__legend');
  for(const [kind,text] of items){
   const item=el('li');
   const mark=el('i',undefined,kind);
   item.append(mark,document.createTextNode(text));
   list.append(item);
  }
  return list;
 }

 function render(state,commands){
  const battle=state&&state.combat;
  const play=document.getElementById('play');
  if(!play)return;
  const active=Boolean(battle&&battle.active);
  play.classList.toggle('in-battle',active);
  const board=mount();
  if(!board)return;
  if(!active){
   board.hidden=true;
   board.replaceChildren();
   return;
  }
  const shape=geometry(battle);
  const moves=moveCells(commands);
  board.hidden=false;
  const head=el('div',undefined,'board__head');
  head.append(el('p','поле боя','board__eyebrow'));
  head.append(el('h2',(state.scene&&state.scene.title)||'Поле боя','board__title'));
  const meta=[];
  if(Number.isFinite(battle.round))meta.push('раунд '+battle.round);
  if(shape)meta.push(shape.width+' × '+shape.height+' · клетка '+shape.cellFt+' футов');
  if(battle.terrain)meta.push(battle.terrain);
  head.append(el('span',meta.join(' · ')||'поле боя не размечено','board__meta'));
  board.replaceChildren(head,turnsList(battle));
  if(shape){
   board.append(grid(battle,shape,moves));
   board.append(legend(shape,moves));
   const note=[];
   if(shape.cellFt)note.push('Клетка — '+shape.cellFt+' футов.');
   note.push('Идти можно только в подсвеченные клетки: их назвал движок.');
   board.append(el('p',note.join(' '),'board__note'));
  }else{
   // Честная пустота вместо нарисованной на глаз сетки: пакет экрана не несёт
   // ни клеток, ни позиций. Порядок ходов и подписи выше — из движка.
   const empty=el('div',undefined,'board__empty');
   empty.append(el('p','Разметки поля нет: движок не присылает сетку и позиции, показываем порядок ходов.'));
   if(battle.terrain)empty.append(el('p',battle.terrain));
   board.append(empty);
   board.append(legend(null,moves));
  }
 }

 document.addEventListener('solo:packet',event=>{
  const detail=event.detail||{};
  render(detail.snapshot||{},detail.commands||[]);
 });
})();

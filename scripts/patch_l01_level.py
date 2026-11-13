"""One-off patch script: wizard level control (L01). Keeps CRLF line endings."""
from pathlib import Path

ROOT = Path('/home/hermes/projects/SoloRealmsAI-Nat20')
WIZARD = ROOT / 'web/wizard.js'
CSS = ROOT / 'web/wizard.css'


def edit(path, pairs):
    text = path.read_bytes().decode('utf-8')
    for old, new in pairs:
        old = old.replace('\n', '\r\n')
        new = new.replace('\n', '\r\n')
        count = text.count(old)
        if count != 1:
            raise SystemExit(f'{path.name}: expected 1 match, got {count} for {old[:60]!r}')
        text = text.replace(old, new)
    path.write_bytes(text.encode('utf-8'))
    print('patched', path.name, len(pairs), 'sites')


LEVEL_FUNCS = """ // Уровень героя — параметр сборки, а не подпись: значение уходит в черновик
 // (`draft.level`), и сервер пересчитывает под него подкласс, повышения
 // характеристик, заклинания всех уровней и ячейки. Границы уровней и имя книги
 // приходят из определения мира (`world-readiness`), а не придумываются экраном.
 function levelBounds(){const world=state.world||{};const min=Math.min(Math.max(Number(world.minimum_level)||1,1),20);const max=Math.min(Math.max(Number(world.maximum_level)||20,min),20);return {min,max,rec:Number(world.hero_level)||null};}
 function levelNote(){const world=state.world;if(!world)return 'Свободная игра: уровень героя от 1 до 20.';const {min,max}=levelBounds();const book=String(world.book||'').trim();if(!min&&!max)return 'Книга не назвала уровень: герой с 1-го по 20-й.';const who=book?('Книга «'+book+'»'):'Книга';if(min===max)return who+' рассчитана на '+min+'-й уровень.';return who+' рассчитана на уровни с '+min+'-го по '+max+'-й.';}
 // Книга без рекомендации оставляет уровень 1: выдумывать требование нельзя.
 async function loadWorld(){const id=state.prepared?state.prepared.draft_id:state.sourceDraft;state.world=null;if(!id)return null;
  try{const world=await api('/creation/drafts/'+id+'/world-readiness');if(!world||typeof world!=='object')return null;state.world=Object.assign({draft_id:id},world);
   const bounds=levelBounds();const level=Number(state.draft.level)||1;
   if(level<bounds.min||level>bounds.max){state.draft.level=Math.min(Math.max(bounds.rec||bounds.min,bounds.min),bounds.max);await syncContract();}
   return state.world;}catch(error){return null;}}
 function drawLevel(){const host=el('section','','wizard-part wizard-level');host.dataset.step='level';host.append(el('h3','Уровень героя','wizard-part-title'),el('p',levelNote(),'hint'));
  const bounds=levelBounds(),row=el('label','','wizard-field');row.append(el('span','Уровень героя'));
  const select=el('select');select.id='wizard-level';select.setAttribute('aria-label','Уровень героя');
  for(let level=bounds.min;level<=bounds.max;level++){const option=el('option',String(level));option.value=String(level);select.append(option);}
  select.value=String(Number(state.draft.level)||bounds.min);
  select.onchange=protect(async()=>{state.draft.level=Math.min(Math.max(Number(select.value)||bounds.min,bounds.min),bounds.max);state.error='';state.errorSource='';await syncContract();});
  row.append(select);host.append(row);
  if(bounds.rec&&Number(state.draft.level)!==bounds.rec)host.append(el('p','Книга рассчитана на '+bounds.rec+'-й уровень, выбран '+String(state.draft.level)+'-й.','wizard-reason'));
  const dropped=(state.contract&&state.contract.dropped)||[];
  if(dropped.length)host.append(el('p','Уровень ниже прежнего — сняты выборы: '+dropped.map(item=>String(item.title||item.key)+(item.level?(' ('+item.level+' ур.)'):'')).join(', ')+'.','wizard-reason'));
  return host;}"""

edit(WIZARD, [
    # state field
    ("screen:0,drawing:false,queued:false}",
     "screen:0,drawing:false,queued:false,world:null}"),
    # replace adoptLevel with the level block + world loader
    (""" // The adventure states the level it is written for, so the hero opens there.
 // A book without a recommendation leaves level 1 alone instead of inventing one.
 async function adoptLevel(){if(!state.prepared||state.applied)return;
  try{const world=await api('/creation/drafts/'+state.prepared.draft_id+'/world-readiness');
   const level=Number(world&&world.hero_level);
   if(Number.isInteger(level)&&level>=1&&level<=20&&level!==state.draft.level){state.draft.level=level;await syncContract();}}
  catch(error){}}""",
     LEVEL_FUNCS),
    # draw the control on the first screen of the create flow
    ("""  if(state.mode==='level'&&state.levelUp&&screenIndex()===0&&state.levelUp.from_level){const delta=el('div');drawDelta(delta);host.append(delta);}""",
     """  if(state.mode==='level'&&state.levelUp&&screenIndex()===0&&state.levelUp.from_level){const delta=el('div');drawDelta(delta);host.append(delta);}
  if(state.mode!=='level'&&screenIndex()===0)host.append(drawLevel());"""),
    # reset world when a create flow starts
    ("else{state.prepared=options.prepared||null;state.sourceDraft=options.preparationDraft||'';state.levelUp=null;",
     "else{state.prepared=options.prepared||null;state.sourceDraft=options.preparationDraft||'';state.levelUp=null;state.world=null;"),
    # load the book's requirement after the first contract
    ("   await syncContract();const focus=ui.stage.querySelector('button,input,select');if(focus)focus.focus();return true;",
     "   await syncContract();await loadWorld();const focus=ui.stage.querySelector('button,input,select');if(focus)focus.focus();return true;"),
    # the prepared entry no longer adopts the level behind the player's back: start() does it
    ("window.soloOpenPreparedNat20=async selection=>{await start('create',{prepared:selection});await adoptLevel();};",
     "window.soloOpenPreparedNat20=async selection=>{await start('create',{prepared:selection});};"),
    # expose what the screen knows about the book
    ("state:()=>({mode:state.mode,draft:state.draft,answers:state.answers,contract:state.contract,levelUp:state.levelUp,",
     "state:()=>({mode:state.mode,draft:state.draft,answers:state.answers,contract:state.contract,levelUp:state.levelUp,world:state.world,"),
])

edit(CSS, [
    ("#wizard .wizard-part{margin:0 0 16px}",
     """#wizard .wizard-part{margin:0 0 16px}
#wizard .wizard-level{margin:0 0 18px;padding:12px 14px;border:1px solid var(--line);border-radius:var(--radius);background:var(--roll-bg)}
#wizard .wizard-level .wizard-field{flex-direction:row;align-items:center;gap:10px;margin:6px 0 0}
#wizard .wizard-level select{min-height:44px;min-width:84px;padding:0 10px;border:1px solid var(--line);border-radius:var(--radius);background:transparent;color:var(--text);font-family:var(--font-ui);font-size:15px}"""),
])

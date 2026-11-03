/* Step wizard: every option, count and verdict comes from the server contract. */
(()=>{
 'use strict';
 const KEY='solo-wizard-draft',PENDING='solo-wizard-pending';
 const STEP_TITLES={species:'Вид',lineage:'Наследие',class:'Класс',subclass:'Подкласс',background:'Предыстория',scores:'Характеристики',skills:'Выборы',magic:'Магия',equipment:'Снаряжение',final:'Итог и имя'};
 const ABILITY_ORDER=['strength','dexterity','constitution','intelligence','wisdom','charisma'];
 const ABILITY_TITLES={strength:'Сила',dexterity:'Ловкость',constitution:'Телосложение',intelligence:'Интеллект',wisdom:'Мудрость',charisma:'Харизма'};
 const STATUS_TITLES={complete:'готово',incomplete:'частично',empty:'не выбрано',invalid:'ошибка',unavailable:'расчёт недоступен'};
 const state={open:false,draft:null,answers:{},step:0,contract:null,seq:0,busy:false,error:'',mode:'create',prepared:null,levelUp:null,applied:false,advance:false,import:null,source:''};
 let ui=null,restoreFocus=null;
 function blank(){return {species_slug:null,species_base:null,class_slug:null,subclass_slug:null,background_slug:null,level:1,ability_score_method:null,ability_scores:{},selected_choices:[],equipment_kits:{},kit_picks:{},prepared_spells:[],name:'',backstory:'',choices:{}};}
 function merge(saved){const base=blank();if(!saved||typeof saved!=='object')return base;for(const key of Object.keys(base))if(Object.hasOwn(saved,key)&&saved[key]!==null&&saved[key]!==undefined)base[key]=saved[key];if(!Array.isArray(base.prepared_spells))base.prepared_spells=[];return base;}
 // A draft belongs to one adventure and one mode. The old key held a single
 // unnamed blob, so starting a new hero restored the previous one, complete with
 // its answers and step number. The record now names its owner; a record whose
 // owner, mode or schema version does not match is not this hero and is offered
 // for an explicit restore instead of being applied.
 const SCHEMA=2;
 function scope(){return {adventure:state.prepared?('prepared:'+state.prepared.draft_id):'sandbox',mode:state.mode,schema:SCHEMA};}
 function persist(){if(!state.open)return;try{localStorage.setItem(KEY,JSON.stringify(Object.assign(scope(),{draft:state.draft,answers:state.answers,step:state.step})));}catch(e){}}
 function stored(){try{const row=JSON.parse(localStorage.getItem(KEY));return row&&row.draft?row:null;}catch(e){return null;}}
 function recall(){const row=stored();if(!row)return null;const now=scope();if(row.adventure!==now.adventure||row.mode!==now.mode||row.schema!==now.schema)return null;return row;}
 // A draft made in another adventure is kept, not destroyed: the player chooses
 // whether to bring its answers here.
 function otherDraft(){const row=stored();if(!row||!row.draft)return null;const now=scope();if(row.adventure===now.adventure&&row.mode===now.mode&&row.schema===now.schema)return null;const filled=JSON.stringify(row.draft||{})!==JSON.stringify(blank());return filled?row:null;}
 function forget(){try{localStorage.removeItem(KEY);}catch(e){}}
 function contractPayload(){return {draft:state.draft,answers:state.answers};}
 function steps(){return (state.contract&&state.contract.steps)||[];}
 function visibleSteps(){const all=steps();return all.filter(step=>step.id!=='equipment');}
 function blockingIds(){return new Set((state.contract&&state.contract.blocking)||[]);}
 function activeStep(){const list=visibleSteps();return list[Math.min(state.step,Math.max(list.length-1,0))]||null;}
 function settle(){const contract=state.contract;if(!contract)return false;let changed=false;for(const step of contract.steps||[]){if(step.id!=='equipment')continue;for(const group of step.groups||[]){if(!state.draft.equipment_kits[group.id]&&group.recommended){state.draft.equipment_kits[group.id]=group.recommended;changed=true;}}}const live=new Set((contract.steps||[]).filter(step=>step.id==='equipment').flatMap(step=>(step.groups||[]).map(group=>group.id)));for(const key of Object.keys(state.draft.equipment_kits))if(!live.has(key)){delete state.draft.equipment_kits[key];delete state.draft.kit_picks[key];changed=true;}return changed;}
 async function syncContract(){const seq=++state.seq;state.busy=true;draw();try{const value=await api('/prototype/wizard/contract',contractPayload());if(seq!==state.seq)return;state.contract=value;state.draft=merge(value.draft);state.answers=Object.assign({},value.answers||state.answers);state.error='';if(settle()){await syncContract();return;}persist();}catch(error){if(seq!==state.seq)return;state.error=error.message;}finally{if(seq===state.seq){state.busy=false;draw();}}}
 function shell(){if(ui)return ui;const root=el('div');root.id='wizard';root.hidden=true;root.setAttribute('role','dialog');root.setAttribute('aria-modal','true');root.setAttribute('aria-labelledby','wizard-title');
  const box=el('div','','wizard-shell'),head=el('header','','wizard-head'),title=el('h1','Герой');title.id='wizard-title';
  const eyebrow=el('p','СОЗДАНИЕ ГЕРОЯ','eyebrow'),ready=el('p','','wizard-ready'),close=el('button','Закрыть','wizard-close');ready.setAttribute('role','status');
  // A player often arrives with the hero already written down; reading that text
  // is local, and nothing it finds is applied without an explicit confirmation.
  const paste=el('button','Вставить лист','wizard-paste');paste.type='button';
  head.append(eyebrow,title,ready,paste,close);
  const body=el('div','','wizard-body'),nav=el('nav','','wizard-steps'),stage=el('main','','wizard-stage'),sheet=el('aside','','wizard-sheet');
  nav.setAttribute('aria-label','Шаги создания героя');stage.tabIndex=-1;sheet.setAttribute('aria-label','Лист героя');
  body.append(nav,stage,sheet);
  const foot=el('footer','','wizard-foot'),back=el('button','Назад'),sheetToggle=el('button','Лист','wizard-sheet-toggle'),next=el('button','Далее'),recommendButton=el('button','Рекомендовать'),confirm=el('button','Подтвердить','primary'),spacer=el('span','','wizard-spacer');
  back.type=sheetToggle.type=next.type=recommendButton.type=confirm.type='button';
  recommendButton.title='Предложить законные варианты для незакрытых шагов. Ручные решения не стираются.';
  foot.append(spacer,sheetToggle,recommendButton,back,next,confirm);
  box.append(head,body,foot);root.append(box);document.body.append(root);
  ui={root,box,title,eyebrow,ready,paste,close,nav,stage,sheet,back,next,confirm,sheetToggle,recommend:recommendButton};
  close.onclick=()=>shut(false);
  paste.onclick=protect(()=>{state.import=state.import?null:{text:'',result:null,busy:false,error:''};draw();});back.onclick=()=>{if(state.step>0){state.step--;persist();draw();}};
  next.onclick=()=>{const list=visibleSteps();if(state.step<list.length-1){state.step++;persist();draw();}};
  sheetToggle.onclick=()=>{const shown=root.classList.toggle('is-sheet');sheetToggle.setAttribute('aria-expanded',String(shown));};
  confirm.onclick=protect(()=>submit());
  root.addEventListener('keydown',trap);
  return ui;}
 function trap(event){if(!state.open)return;if(event.key==='Escape'){event.preventDefault();shut(false);return;}if(event.key!=='Tab')return;const nodes=focusable();if(!nodes.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
 function focusable(){if(!ui)return [];return Array.from(ui.root.querySelectorAll('button,input,select,textarea,summary,[href],[tabindex]:not([tabindex="-1"])')).filter(node=>!node.disabled&&!node.hidden&&node.offsetParent!==null);}
 function card(tag,extra){const node=el(tag,undefined,('wizard-card '+(extra||'')).trim());return node;}
 function chip(text,cls){return el('span',text,('wizard-chip '+(cls||'')).trim());}
 function countLine(step){if(step.status==='unavailable')return 'Расчёт недоступен';if(step.missing>0)return 'Осталось: '+step.missing;if(step.count>1)return 'Выбрано: '+step.count;return STATUS_TITLES[step.status]||'';}
 function drawSteps(){if(!ui)return;const list=visibleSteps(),done=new Set((state.contract&&state.contract.blocking)||[]);ui.nav.replaceChildren();const heading=el('p',state.mode==='level'?'ПОВЫШЕНИЕ УРОВНЯ':'ШАГИ','eyebrow');ui.nav.append(heading);const ol=el('ol','','wizard-list');
  list.forEach((step,index)=>{const item=el('li');if(index===state.step)item.className='is-current';const button=el('button');button.type='button';const dot=chip(step.status==='complete'?'✓':(step.status==='invalid'||step.status==='unavailable'?'!':'○'),'is-'+(step.status||'empty'));button.append(dot,el('span',step.title||STEP_TITLES[step.id]||step.id,'wizard-step-title'),el('span',countLine(step),'wizard-step-note'));button.setAttribute('aria-current',index===state.step?'step':'false');button.onclick=()=>{state.step=index;persist();draw();};item.append(button);ol.append(item);});
  ui.nav.append(ol);const problems=(state.contract&&state.contract.issues)||[];if(problems.length){const box=el('div','','wizard-issues');box.append(el('p','ЧТО НЕ ТАК','eyebrow'));for(const text of problems.slice(0,8))box.append(el('p',String(text),'hint'));ui.nav.append(box);}}
 function drawSheet(){if(!ui)return;ui.sheet.replaceChildren();if(state.mode==='level'&&state.levelUp){drawDelta(ui.sheet);}
  const sheet=state.mode==='level'&&state.levelUp?state.levelUp.after:(state.contract&&state.contract.sheet);
  if(!sheet){ui.sheet.append(el('h2','Лист героя'),el('p',sheetReason(),'hint'));return;}
  const head=el('div','','wizard-sheet-head');head.append(el('h2',state.draft.name||'Без имени'),el('p',(state.draft.class_slug||'')+(state.draft.subclass_slug?' · '+state.draft.subclass_slug:'')+' · уровень '+String(state.draft.level),'hint'));ui.sheet.append(head);
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
 function optionCard(step,row,selected,onclick,{multi=false,disabled=false}={}){const node=card('button','wizard-option');node.type='button';node.disabled=disabled;node.dataset.id=String(row.id);node.setAttribute('role',multi?'checkbox':'radio');node.setAttribute('aria-checked',String(selected));if(selected)node.classList.add('is-chosen');node.append(el('span',row.name||row.id,'wizard-option-name'));if(row.detail&&row.detail!==row.name)node.append(el('span',row.detail,'wizard-option-detail'));if(row.disabled_reason)node.append(el('span',row.disabled_reason,'wizard-option-block'));if(row.hint)node.append(el('span',row.hint,'wizard-option-detail'));node.onclick=protect(async()=>{if(disabled)return;await onclick();});return node;}
 function stageHead(step,lead){const box=el('div','','wizard-stage-head');box.append(el('h2',step.title||STEP_TITLES[step.id]||step.id));if(step.hint)box.append(el('p',step.hint,'hint'));if(lead)box.append(el('p',lead,'hint'));for(const reason of step.reasons||[])if(!(step.missing===0))box.append(el('p',reason,'wizard-reason'));return box;}
 function singleStep(step,field,apply,{lead}={}){const host=el('div','','wizard-cards');host.append(stageHead(step,lead));const grid=el('div','','wizard-grid');for(const row of step.options||[]){const chosen=(step.chosen||[]).includes(String(row.id).split(',')[0])||String(state.draft[field])===String(row.id);grid.append(optionCard(step,row,chosen,async()=>{await apply(row);}));}host.append(grid);return host;}
 async function chooseSpecies(row){const group=row;if(group.lineage_required){state.draft.species_base=group.id;if(!(group.variants||[]).some(v=>v.slug===state.draft.species_slug))state.draft.species_slug=null;}else{const only=(group.variants||[])[0];state.draft.species_base=group.id;state.draft.species_slug=only?only.slug:null;}state.advance=true;await syncContract();}
 async function chooseLineage(row){state.draft.species_slug=row.id;state.advance=true;await syncContract();}
 async function chooseClass(row){if(state.draft.class_slug!==row.id){state.draft.class_slug=row.id;state.draft.subclass_slug=null;state.draft.selected_choices=[];state.answers={};state.draft.choices={};state.draft.equipment_kits={};state.draft.kit_picks={};state.draft.prepared_spells=[];}state.advance=true;await syncContract();}
 async function chooseSubclass(row){state.draft.subclass_slug=state.draft.subclass_slug===row.id?null:row.id;state.advance=true;await syncContract();}
 async function chooseBackground(row){if(state.draft.background_slug!==row.id){state.draft.background_slug=row.id;for(const key of Object.keys(state.draft.equipment_kits))if(key.startsWith('background:'))delete state.draft.equipment_kits[key];}state.advance=true;await syncContract();}
 function drawScores(step){const host=el('div');host.append(stageHead(step,step.chosen_method?'Бюджет считает сервер по таблицам Nat20.':'Сначала выберите способ генерации: от него зависит допустимость значений.'));
  const methods=el('div','','wizard-grid');for(const row of step.options||[])methods.append(optionCard(step,row,step.chosen_method===row.id,async()=>{
    // Numbers typed for one method are not numbers for another: the array is a set
    // of six printed values, the budget is a purchase. Carrying one into the other
    // leaves a remainder the new method cannot pay for, so the switch is explicit.
    if(state.draft.ability_score_method&&state.draft.ability_score_method!==row.id)state.draft.ability_scores={};
    state.draft.ability_score_method=row.id;if(row.id==='standard_array'&&!Object.keys(state.draft.ability_scores).length)state.draft.ability_scores={};await syncContract();}));host.append(methods);
  if(step.chosen_method){const budget=step.budget||{},line=el('p','','wizard-budget');if(budget.kind==='point_buy')line.textContent='Очки: '+budget.spent+' из '+budget.total+' · осталось '+budget.left;else if(budget.kind==='standard_array')line.textContent='Осталось разложить: '+((budget.left||[]).join(', ')||'ничего');host.append(line);
   const grid=el('div','','wizard-scores');const bonuses=new Map((step.bonuses||[]).map(row=>[row.id,Number(row.bonus)||0]));
   // The array is a set of values to place, so a value can move: choosing a free value
   // fills the first empty score, and choosing a value already held by another score
   // exchanges the two. Nothing is typed to fix an order mistake, and a value can
   // never be duplicated or invented.
   const values=Object.assign({},state.draft.ability_scores||{});
   const free=new Map();for(const value of budget.array||[])free.set(value,(free.get(value)||0)+1);
   for(const value of Object.values(values))if(value!==null&&value!==undefined)free.set(value,(free.get(value)||0)-1);
   for(const ability of step.abilities||[]){const row=el('label','','wizard-score');row.append(el('span',ability.name));
    const bonus=bonuses.get(ability.id)||0;const base=ability.value===null||ability.value===undefined?null:Number(ability.value);
    const input=el('input');input.type='number';input.min=String(ability.minimum||1);input.max=String(ability.maximum||20);
    input.value=base===null?'':String(base);input.setAttribute('aria-label',ability.name+(bonus?' база, без бонуса':'')+' ');
    // Clearing a score removes it from the draft. Sending null would be rejected as
    // an invalid integer, so the key is deleted instead of being set to empty.
    input.onchange=protect(async()=>{const raw=input.value.trim();if(raw==='')delete state.draft.ability_scores[ability.id];else state.draft.ability_scores[ability.id]=Number(raw);await syncContract();});
    row.append(input);
    if(bonus)row.append(el('span','+'+bonus+' от предыстории','hint'));
    if(bonus)row.append(el('strong',String((base===null?0:base)+bonus),'wizard-total'));
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
  if(step.kind==='increase'||step.kind==='asi'){const cap=Number(step.cap)||1,points=Number(step.points)||Number(step.count)||1;const current=Object.assign({},answer&&typeof answer==='object'?answer:(step.increases||{}));const spent=Object.values(current).reduce((sum,value)=>sum+Number(value||0),0);host.append(stageHead(step,'Распределите '+points+' очко(в), не более '+cap+' на характеристику. Всего очков: '+spent+' из '+points+'.'));const grid=el('div','','wizard-scores');for(const slug of step.options.map(row=>row.id)){const row=el('div','','wizard-score');row.append(el('span',ABILITY_TITLES[slug]||slug));const value=el('strong',String(current[slug]||0));const plus=el('button','+','wizard-step-button'),minus=el('button','−','wizard-step-button');plus.type=minus.type='button';plus.onclick=protect(async()=>{if((current[slug]||0)>=cap||spent>=points)return;current[slug]=(current[slug]||0)+1;await answerChoice(step,current);});minus.onclick=protect(async()=>{if(!current[slug])return;current[slug]-=1;if(!current[slug])delete current[slug];await answerChoice(step,current);});row.append(minus,value,plus);grid.append(row);}host.append(grid);return host;}
  host.append(stageHead(step,'Выберите '+limit+' вариант(ов) из списка: осталось '+Number(step.missing||0)+'.'));const grid=el('div','','wizard-grid');const options=step.options||[];
  for(const row of options){const selected=chosen.has(String(row.id));grid.append(optionCard(step,row,selected,async()=>{const next=new Set(chosen);if(next.has(String(row.id)))next.delete(String(row.id));else{if(next.size>=limit)throw new Error('Можно выбрать не больше '+limit+'. Снимите другой вариант.');next.add(String(row.id));}await answerChoice(step,Array.from(next));},{multi:true}));}
  if(step.locked&&step.locked.length)host.append(el('p','Уже занято из другого источника: '+step.locked.join(', ')+'. Повторно потратить нельзя.','hint'));
  host.append(grid);return host;}
 async function answerChoice(step,value){state.answers[step.choice_key]=value;state.draft.choices[step.choice_key]=value;await syncContract();}
 function drawMagic(step){const host=el('div');const limits=step.limits||[];const caps={};for(const row of limits)caps[row.name]=Number(row.value);host.append(stageHead(step,limits.map(row=>row.name+': '+row.value).join(' · ')||null));const chosen=new Set(state.draft.prepared_spells||[]);
  const block=(title,rows,kind,cap)=>{const box=el('div','','wizard-block');const count=(rows||[]).filter(row=>chosen.has(row.id)).length;box.append(el('h3',title+(cap?' (выбрано '+count+' из '+cap+')':'')));const grid=el('div','','wizard-grid');for(const row of rows||[]){const picked=chosen.has(row.id);grid.append(optionCard(step,row,picked,async()=>{if(chosen.has(row.id))chosen.delete(row.id);else{if(cap&&count>=cap)throw new Error(title+': можно выбрать не больше '+cap+'. Снимите другой вариант.');chosen.add(row.id);}state.draft.prepared_spells=Array.from(chosen);await syncContract();},{multi:true,disabled:!picked&&Boolean(cap)&&count>=cap}));}box.append(grid);return box;};
  if((step.cantrips||[]).length)host.append(block('Заговоры',step.cantrips,'cantrip',caps['cantrips-known']));
  if((step.spells||[]).length)host.append(block('Заклинания',step.spells,'spell',caps['max-prepared']));
  if(!(step.cantrips||[]).length&&!(step.spells||[]).length)host.append(el('p','Для этого класса корпус не публикует ячейки на этом уровне, поэтому список пуст.','hint'));
  if(step.note)host.append(el('p',step.note,'hint'));return host;}
 function drawEquipment(host,step){const box=el('div','','wizard-block');box.append(el('h3','Снаряжение'),el('p',step.note||'','hint'));for(const group of step.groups||[]){const fieldset=el('fieldset','','wizard-kits');fieldset.append(el('legend','Комплект: '+group.title));const grid=el('div','','wizard-grid');for(const row of group.options||[]){const node=card('button','wizard-option');node.type='button';node.setAttribute('role','radio');node.setAttribute('aria-checked',String(group.chosen===row.id));if(group.chosen===row.id)node.classList.add('is-chosen');node.append(el('span',row.name,'wizard-option-name'),el('span','Золото: '+row.gold_gp+' зм','wizard-option-detail'));if((row.items||[]).length)node.append(el('span',(row.items||[]).map(item=>item.slug+(item.count>1?' × '+item.count:'')).join(', '),'wizard-option-detail'));if(row.recommended)node.append(chip('рекомендуем','is-complete'));node.onclick=protect(async()=>{state.draft.equipment_kits[group.id]=row.id;await syncContract();});grid.append(node);}fieldset.append(grid);const picks=group.picks||{};const chosenRow=(group.options||[]).find(row=>row.id===group.chosen);for(const pick of (chosenRow&&chosenRow.picks)||[]){const select=el('select');select.setAttribute('aria-label',pick.label);const empty=el('option','Выберите…');empty.value='';select.append(empty);for(const option of pick.options||[]){const item=el('option',option.name);item.value=option.slug;select.append(item);}select.value=picks[pick.group]||'';select.onchange=protect(async()=>{if(!state.draft.kit_picks[group.id])state.draft.kit_picks[group.id]={};state.draft.kit_picks[group.id][pick.group]=select.value;await syncContract();});fieldset.append(field(pick.label,select));}box.append(fieldset);}host.append(box);}
 function drawFinal(step){const host=el('div');host.append(stageHead(step,state.mode==='level'?'':null));
  if(state.mode!=='level'){const nameRow=el('label','','wizard-field');nameRow.append(el('span','Имя героя'));const name=el('input');name.type='text';name.maxLength=80;name.value=state.draft.name||'';name.oninput=()=>{state.draft.name=name.value;};name.onchange=protect(async()=>{await syncContract();});nameRow.append(name);host.append(nameRow);
   const storyRow=el('label','','wizard-field');storyRow.append(el('span','Предыстория героя'));const story=el('textarea');story.rows=6;story.maxLength=4000;story.value=state.draft.backstory||'';story.oninput=()=>{state.draft.backstory=story.value;};story.onchange=protect(async()=>{await syncContract();});storyRow.append(story);host.append(storyRow,el('p','Предыстория уходит Мастеру отдельно от листа: она хранится как ваши сведения о герое и не выдаётся NPC автоматически.','hint'));}
  const equipment=steps().find(item=>item.id==='equipment');if(equipment)drawEquipment(host,equipment);
  const gate=el('div','','wizard-gate');if(state.contract&&state.contract.ready)gate.append(el('p','Всё обязательное выбрано. Подтверждение создаёт героя.','wizard-ok'));else{gate.append(el('p','Подтверждение недоступно, пока не закрыты обязательные шаги:','wizard-reason'));for(const step of visibleSteps())if(step.status!=='complete')gate.append(el('p','• '+(step.title||step.id)+': '+(step.reasons||[])[0]||countLine(step),'hint'));}host.append(gate);return host;}
  // Reading a pasted sheet is local: the endpoint returns what the text states,
  // the lines it took each fact from, and every name that matched more than one
  // corpus entry. Applying it fills the draft; every limit is still checked by
  // the same contract the wizard always posts to.
  async function readSheet(text){state.import.busy=true;state.import.error='';draw();
   try{const result=await api('/prototype/wizard/import',{text});state.import.result=result;}
   catch(error){state.import.error=error.message||String(error);}
   finally{state.import.busy=false;draw();}}
 function drawImport(){const host=el('div','','wizard-import');host.append(stageHead({title:'Вставить готовый лист',hint:'Строки разбираются локально: значения не додумываются и не обрезаются, а исходный текст сохраняется целиком.'}));
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
   apply.onclick=protect(async()=>{if(!result.draft)return;state.source=result.source||state.import.text||'';state.draft=merge(result.draft);state.answers={};state.import=null;state.step=0;await syncContract();});
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
  take.onclick=protect(async()=>{state.draft=merge(other.draft);state.answers=other.answers||{};state.step=0;state.offer=null;persist();await syncContract();});
  fresh.onclick=protect(async()=>{state.offer=null;state.draft=blank();state.answers={};state.step=0;persist();await syncContract();});
  actions.append(take,fresh);host.append(actions,el('p','Ответы другого приключения не удаляются, пока вы не подтвердите героя здесь.','hint'));
  return host;}
 function drawStage(){if(!ui)return;const step=activeStep();ui.stage.replaceChildren();ui.stage.setAttribute('aria-busy',String(state.busy));if(state.offer){ui.stage.append(drawOffer());return;}if(state.import){ui.stage.append(drawImport());return;}if(!step){ui.stage.append(el('p','Шагов пока нет: сервер ещё не вернул договор.','hint'));return;}
  if(state.mode==='level'&&state.levelUp&&state.step===0){const host=el('div');host.append(stageHead(step,'Повышение уровня меняет только то, что открылось. Прежние выборы не переспрашиваются.'));if(state.levelUp.opened&&state.levelUp.opened.length)host.append(el('p','Открылось решений: '+state.levelUp.opened.length,'hint'));ui.stage.append(host);}
  if(step.id==='species')ui.stage.append(singleStep(step,'species_slug',chooseSpecies,{lead:'Наследие выбирается отдельным шагом, если корпус делит вид.'}));
  else if(step.id==='lineage')ui.stage.append(singleStep(step,'species_slug',chooseLineage,{lead:'Подвид задаёт свойства и навыки: он обязателен.'}));
  else if(step.id==='class')ui.stage.append(singleStep(step,'class_slug',chooseClass,{lead:'Класс задаёт кость хитов, владения и доступные выборы.'}));
  else if(step.id==='subclass')ui.stage.append(singleStep(step,'subclass_slug',chooseSubclass,{lead:step.opens_at_level?'Подкласс открывается на уровне '+step.opens_at_level+'.':'Подкласс доступен на этом уровне.'}));
  else if(step.id==='background')ui.stage.append(singleStep(step,'background_slug',chooseBackground,{lead:'Предыстория даёт три характеристики, навыки и черту.'}));
  else if(step.id==='scores')ui.stage.append(drawScores(step));
  else if(step.id==='magic')ui.stage.append(drawMagic(step));
  else if(step.id==='final')ui.stage.append(drawFinal(step));
  else if(step.choice_key)ui.stage.append(drawChoice(step));
  else ui.stage.append(stageHead(step));
  if((state.recommended||[]).length){const note=el('p','Предложение заполнило незакрытые шаги. Ваши решения сохранены: '+state.recommended.length+' шт. Ничего не подтверждено — проверьте и подтвердите сами.','hint');note.classList.add('wizard-recommended');ui.stage.prepend(note);}
  if(state.error){const box=el('div','','wizard-error');box.append(el('p',state.error));const retry=el('button','Повторить');retry.type='button';retry.onclick=protect(()=>syncContract());box.append(retry);ui.stage.append(box);}
  if(state.busy)ui.stage.append(el('p','Проверяем шаг…','wizard-loading'));}
 function draw(){if(!ui)return;ui.root.hidden=!state.open;if(!state.open)return;if(state.advance&&!state.busy){state.advance=false;const current=activeStep();const list=visibleSteps();if(current&&current.status==='complete'&&!current.choice_key&&state.step<list.length-1){state.step+=1;persist();}}const list=visibleSteps();ui.title.textContent=state.mode==='level'?'Повышение уровня':'Создание героя';ui.eyebrow.textContent=state.mode==='level'?'НОВЫЙ УРОВЕНЬ':(state.prepared?'ГЕРОЙ ДЛЯ ПОДГОТОВЛЕННОГО МИРА':'НОВАЯ ИГРА');
  const blocking=(state.contract&&state.contract.blocking)||[];ui.ready.textContent=!state.contract?'Загружаем договор…':(state.contract.ready?'Готово к подтверждению':('Осталось шагов: '+blocking.length));ui.ready.classList.toggle('is-loading',state.busy);
  ui.back.disabled=state.step<=0;ui.next.disabled=state.step>=list.length-1;if(ui.recommend){ui.recommend.hidden=state.mode==='level';ui.recommend.disabled=state.busy;}const ready=confirmReady();ui.confirm.disabled=!ready;ui.confirm.textContent=state.mode==='level'?'Подтвердить уровень':'Подтвердить героя';
  ui.root.setAttribute('aria-busy',String(state.busy));drawSteps();drawStage();drawSheet();persist();}
 function confirmReady(){if(!state.contract||state.busy)return false;if(state.mode==='level'){const opened=new Set((state.levelUp&&state.levelUp.opened)||[]);for(const step of visibleSteps()){if(step.id==='final')continue;if(!step.choice_key&&step.id!=='subclass')continue;if(opened.size&&!opened.has(step.choice_key))continue;if(Number(step.missing||0)>0)return false;}return !(state.contract.issues||[]).length;}return Boolean(state.contract.ready);}
 // An explicit suggestion, never an automatic choice. It fills only the steps
 // still owed, reports what it kept, and confirms nothing by itself.
 async function recommend(){if(state.busy||state.applied)return;state.busy=true;state.error='';draw();
  try{const value=await api('/prototype/wizard/recommend',contractPayload());
   if(!value.ok){state.error=value.reason||'Предложение не собралось.';}
   else{state.draft=merge(value.draft);state.answers={};state.recommended=(value.kept||[]).slice();await syncContract();}}
  catch(error){state.error=error.message;}
  finally{state.busy=false;draw();}}
 async function submit(){if(!confirmReady()||state.applied)return;state.applied=true;state.busy=true;draw();try{if(state.mode==='level')await submitLevel();else await submitCreate();}catch(error){state.error=error.message;state.applied=false;}finally{state.busy=false;draw();}}
 function pendingRecord(){try{return JSON.parse(localStorage.getItem(PENDING));}catch(e){return null;}}
 // The adventure states the level it is written for, so the hero opens there.
 // A book without a recommendation leaves level 1 alone instead of inventing one.
 async function adoptLevel(){if(!state.prepared||state.applied)return;
  try{const world=await api('/creation/drafts/'+state.prepared.draft_id+'/world-readiness');
   const level=Number(world&&world.hero_level);
   if(Number.isInteger(level)&&level>=1&&level<=20&&level!==state.draft.level){state.draft.level=level;await syncContract();}}
  catch(error){}}
 async function worldReady(){if(!state.prepared)return null;const world=await api('/creation/drafts/'+state.prepared.draft_id+'/world-readiness');if(!world.ready||!world.world_ready||world.run_id!==state.prepared.run_id)throw new Error('Мир изменился. Вернитесь к материалам и подтвердите его заново.');return world;}
 async function submitCreate(){const world=await worldReady();const build=state.contract.build;if(!build)throw new Error('Сервер не вернул собранного героя.');const revision=world?world.revision:(state.prepared?state.prepared.revision:null);
  const payload={name:(state.draft.name||'').trim(),build,prepared_spells:state.draft.prepared_spells||[],adventure:state.prepared?('prepared:'+state.prepared.draft_id):'sandbox',description:'',encounter_monsters:[],mechanics_ruleset:'dnd2024',rules_confirmed:true,hero_confirmed:true,background_confirmed:true,hero_background:(state.draft.backstory||'').slice(0,4000)};
  if(state.prepared)payload.adventure_revision=revision;
  // Starting a campaign is the one action that advances a prepared world, so a
  // re-send after a lost response would otherwise carry a new revision, miss the
  // server's idempotency record and start a second game (or be refused). A retry
  // of the same prepared world therefore resends the exact stored body, and a
  // deliberate change of hero or text still produces a new request.
  const old=pendingRecord();
  const same=row=>Boolean(row&&row.payload)&&row.adventure===payload.adventure&&row.run_id===(state.prepared?state.prepared.run_id:null);
  let body;
  if(same(old)){body=JSON.parse(old.payload);}
  else{const requestId=crypto.randomUUID();body=Object.assign({},payload,{request_id:requestId});
   localStorage.setItem(PENDING,JSON.stringify({signature:JSON.stringify(payload),request_id:requestId,adventure:payload.adventure,run_id:state.prepared?state.prepared.run_id:null,payload:JSON.stringify(body)}));}
  const created=await api('/prototype/campaigns',body);campaignId=created.id;localStorage.setItem('solo-campaign',campaignId);localStorage.removeItem(KEY);localStorage.removeItem(PENDING);await window.refresh();view('play');shut(true);}
 async function submitLevel(){const advance=state.levelUp;const pending=pendingRecord();const reuse=pending&&pending.campaign===advance.campaign?pending:null;const requestId=reuse?reuse.request_id:crypto.randomUUID();localStorage.setItem(PENDING,JSON.stringify({campaign:advance.campaign,request_id:requestId}));
  const result=await api('/campaigns/'+advance.campaign+'/level-up',{request_id:requestId,expected_version:advance.version,draft:state.contract.draft,answers:state.answers});localStorage.removeItem(PENDING);campaignId=advance.campaign;localStorage.setItem('solo-campaign',campaignId);await window.refresh();view('play');toast('Уровень '+result.result.to_level+' подтверждён.');shut(true);}
 function shut(done){if(!state.open)return;state.open=false;state.applied=false;if(ui){ui.root.hidden=true;ui.root.classList.remove('is-sheet');}if(restoreFocus&&restoreFocus.isConnected)restoreFocus.focus();if(!done)persist();}
 async function start(mode,options){options=options||{};state.mode=mode;state.applied=false;state.advance=false;state.error='';state.import=null;restoreFocus=document.activeElement;
  // Every start resets the screen first, so a late answer from the previous hero
  // cannot paint itself onto the new one.
  state.contract=null;state.seq++;state.busy=false;state.source='';
  if(mode==='level'){state.levelUp={campaign:options.campaign};state.prepared=null;state.draft=blank();state.answers={};state.step=0;state.offer=null;}
  // Opening the same adventure again continues the hero the player already
  // drafted; recall() compares adventure, mode and schema, so a draft from
  // another game is never applied here. Only an explicit fresh start skips it.
  else{state.prepared=options.prepared||null;state.levelUp=null;const saved=options.fresh?null:recall();state.draft=merge(saved&&saved.draft);state.answers=(saved&&saved.answers)||{};state.step=(saved&&saved.step)||0;state.offer=options.fresh?otherDraft():null;if(options.prepared){state.draft.name=state.draft.name||'';}}
  shell();state.open=true;ui.root.classList.remove('is-sheet');draw();ui.root.hidden=false;
  try{
   if(mode==='level'){const data=await api('/campaigns/'+options.campaign+'/level-up');if(!data.available)throw new Error(data.reason||'Повышение уровня недоступно.');state.levelUp=Object.assign({campaign:options.campaign},data);state.contract=data.contract;state.draft=merge(data.contract.draft);state.answers={};state.step=0;state.busy=false;state.error='';draw();const focus=ui.stage.querySelector('button,input,select');if(focus)focus.focus();return true;}
   await syncContract();const focus=ui.stage.querySelector('button,input,select');if(focus)focus.focus();return true;
  }catch(error){state.error=error.message;state.contract=null;draw();return false;}}
 window.soloWizard={open:options=>start(options&&options.levelUp?'level':'create',options||{}),levelUp:campaign=>start('level',{campaign}),close:()=>shut(false),state:()=>({mode:state.mode,draft:state.draft,answers:state.answers,contract:state.contract,levelUp:state.levelUp,error:state.error,import:state.import,source:state.source})};
 window.soloOpenPreparedNat20=async selection=>{await start('create',{prepared:selection});await adoptLevel();};
 window.soloOpenNewGame=()=>start('create',{prepared:null});
})();
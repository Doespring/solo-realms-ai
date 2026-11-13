'use strict';
// Shared shell: existing play requests, event history and saves stay authoritative.
const literaryFields={
 detail:['Подробность',['кратко','средне','подробно']],rhythm:['Ритм',['динамичный','сбалансированный','неторопливый']],
 emotion:['Эмоциональность',['сдержанная','выразительная','насыщенная']],dialogue:['Диалоги',['редко','по сцене','часто']],
 imagery:['Образность',['точная','умеренная','богатая']],initiative:['Инициативность подачи',['реактивная','сбалансированная','инициативная']],
 surprises:['Сюжетные неожиданности',['редко','по причинам','часто']],freedom:['Фокус мира',['сюжетный фокус','свободный мир','смешанный']],
 tension:['Атмосфера',['спокойная','приключенческая','мрачная']],pace:['Темп',['быстрый','средний','медленный']],
 npc_autonomy:['Самостоятельность NPC в диалоге',['низкая','средняя','высокая']],focus:['Акцент',['баланс','исследование','интриги','диалоги','сражения']],
 consequences:['Подача последствий',['мягкая подача','нейтральная подача','суровая подача']]};
const grid=el('div',undefined,'form-grid');
for(const[k,[label,options]]of Object.entries(literaryFields)){const s=el('select');s.id='literary-'+k;for(const value of options)s.append(el('option',value));grid.append(field(label,s));}
const themeInput=el('input');themeInput.id='literary-theme';themeInput.maxLength=300;grid.append(field('Тематика',themeInput));
const literaryPanel=el('details',undefined,'disclosure');literaryPanel.append(el('summary','Литературность и поведение'),el('p','Эти настройки управляют художественной подачей. Правила, броски и сложность остаются в игровом движке.','hint'),grid);
$('master-form').prepend(literaryPanel);
const uncertaintyNotes=el('input');uncertaintyNotes.type='checkbox';uncertaintyNotes.checked=localStorage.getItem('solo-uncertainty-notes')==='on';uncertaintyNotes.onchange=()=>{localStorage.setItem('solo-uncertainty-notes',uncertaintyNotes.checked?'on':'off');if(current)render();};literaryPanel.append(field('Показывать ненавязчивую пометку о художественных деталях',uncertaintyNotes));
$('max_tokens').max=8192;
function collectLiterary(){const values={};for(const k of [...Object.keys(literaryFields),'theme'])values[k]=$('literary-'+k).value;return values;}

const profileTools=el('div',undefined,'button-row');
for(const[id,label]of [['profile-edit','Открыть для редактирования'],['profile-copy','Создать копию'],['profile-update','Сохранить выбранный профиль'],['profile-delete','Удалить выбранный профиль'],['profile-history','История профиля']]){const b=el('button',label);b.id=id;b.type='button';profileTools.append(b);}
$('profile-select').closest('.inline-form').after(profileTools);
const profileHistory=el('div',undefined,'cards-list');profileHistory.id='profile-library-history';profileTools.after(profileHistory);
function selectedProfile(){const p=profiles.find(p=>p.id===$('profile-select').value);if(!p)throw new Error('Выберите профиль.');return p;}
// n02: значение старого профиля или импортированного JSON может не совпасть ни
// с одним пунктом списка. Браузер тогда оставляет select пустым, и сохранение
// уходило с rhythm:'' → 422 literal_error, а игрок видел «Не удалось
// обработать запрос». Непустое незнакомое значение добавляется своим пунктом и
// остаётся выбранным — данные профиля не теряются и отказа формы не возникает;
// пустое поле не выбирается вовсе, иначе форма отправила бы '' вместо умолчания.
function putChoice(node,value){if(!node)return;const text=String(value);if(!text)return;
 if(![...(node.options||[])].some(o=>o.value===text)){const option=document.createElement('option');option.value=text;option.textContent=text;node.append(option);}
 node.value=text;}
function fillMaster(master){
 for(const[k,v]of Object.entries(master))if($(k)){if(typeof v==='boolean')$(k).checked=v;else if(typeof v!=='object')$(k).value=v;}
 for(const k of [...Object.keys(literaryFields),'theme'])if(master.literary?.[k]!==undefined){const node=$('literary-'+k);if(k==='theme'){if(node)node.value=master.literary.theme;}else putChoice(node,master.literary[k]);}
}
// n02: у записи прежней версии значение поля вне допустимых — сервер отдаёт его
// как есть и называет причину в `repair`. Говорим словами: иначе сохранение снова
// упрётся в 422, а игрок не поймёт, какое поле просят выбрать заново.
$('profile-edit').onclick=protect(()=>{const p=selectedProfile();fillMaster({...current?.master,...p.master});
 toast(p.repair?'В профиле значения прежней версии: '+p.repair+'. Выберите их заново и нажмите «Сохранить выбранный профиль».'
  :'Профиль открыт в редакторе. Примените к кампании или сохраните в библиотеке.');});
$('profile-copy').onclick=protect(async()=>{const p=selectedProfile(),id=(await api('/master/profiles',{name:p.name+' — копия',master:p.master})).id;await loadProfiles();$('profile-select').value=id;fillMaster({...current?.master,...p.master,profile_name:p.name+' — копия'});});
$('profile-update').onclick=protect(async()=>{const p=selectedProfile(),master=readMaster();await api('/master/profiles/'+p.id,{name:master.profile_name,master},'PUT');await loadProfiles();$('profile-select').value=p.id;toast('Версия профиля сохранена. Кампания не изменена.');});
$('profile-delete').onclick=protect(async()=>{const p=selectedProfile();if(!confirm('Удалить профиль «'+p.name+'» из библиотеки? Настройки кампании сохранятся.'))return;await api('/master/profiles/'+p.id,undefined,'DELETE');await loadProfiles();$('profile-library-history').replaceChildren();toast('Профиль удалён.');});
$('profile-history').onclick=protect(async()=>{const p=selectedProfile(),rows=await api('/master/profiles/'+p.id+'/history');profileHistory.replaceChildren();for(const r of rows){const card=el('details',undefined,'disclosure');card.append(el('summary',r.name+' · '+new Date(r.created*1000).toLocaleString('ru-RU')),el('pre',JSON.stringify(r.master,null,2)));profileHistory.append(card);}if(!rows.length)profileHistory.append(el('p','Изменений в библиотеке пока нет.','hint'));});

const heroNav=el('button',undefined,'nav');heroNav.dataset.view='character';heroNav.append(el('span','♙'),document.createTextNode(' Персонаж'));heroNav.onclick=()=>view('character');document.querySelector('[data-view="journal"]').before(heroNav);
// n08: экран назван один раз — в шапке («Лист героя»). Надзаголовок
// «ЛИСТ ГЕРОЯ» и H1 «Персонаж» повторяли друг друга третьей подписью.
const character=el('section',undefined,'view');character.id='character';const characterContent=el('div');characterContent.id='character-content';character.append(characterContent);$('journal').before(character);
const workspace=el('div',undefined,'workspace-layout'),storyColumn=el('div',undefined,'literary-column'),rail=el('aside',undefined,'scene-rail');
const story=$('play').querySelector('.story-card'),bottom=$('play').querySelector('.bottom-note');story.before(workspace);storyColumn.append(story);if(bottom)storyColumn.append(bottom);workspace.append(storyColumn,rail);
for(const[id,title]of [['scene-hero','Ваш герой'],['scene-presence','В этой сцене'],['scene-session','Приключение']]){const d=el('details',undefined,'disclosure');d.id=id;d.open=localStorage.getItem('solo-panel-'+id)!=='closed';d.append(el('summary',title),el('div',undefined,'disclosure-body'));d.addEventListener('toggle',()=>localStorage.setItem('solo-panel-'+id,d.open?'open':'closed'));rail.append(d);}
function metric(value,label){const m=el('div',undefined,'metric');m.append(el('strong',String(value)),el('small',label));return m;}
function renderWorkspace(c){
 fillMaster(c.master);
 const h=c.state.hero,place=c.state.locations.find(p=>p.name===c.state.location),summary=($('scene-hero')||{}).lastElementChild||el('div');
 summary.replaceChildren(el('h3',h.name),el('p','Уровень '+h.level,'hint'),el('p',h.hp+' / '+h.max_hp+' хитов · КБ '+h.ac));
 const open=el('button','Открыть лист','quiet');open.onclick=()=>view('character');summary.append(open);
 const here=($('scene-presence')||{}).lastElementChild||el('div');here.replaceChildren();for(const n of (place?.npc||[]).filter(n=>n.present!==false))here.append(el('p',n.name));if(!here.childElementCount)here.append(el('p','Вы здесь одни.','hint'));
 ($('scene-session')||{}).lastElementChild?.replaceChildren(el('p','Ход '+c.state.turn),el('p',c.name,'hint'),el('p','Ходы сохраняются автоматически.','hint'));
 characterContent.replaceChildren(el('h2',h.name));const stats=el('div',undefined,'metrics-grid');for(const[v,label]of [[h.level,'Уровень'],[h.hp+' / '+h.max_hp,'Хиты'],[h.ac,'Класс брони']])stats.append(metric(v,label));characterContent.append(stats);
 const scores=el('div',undefined,'scores-grid');for(const[k,v]of Object.entries(h.abilities)){const mod=Math.floor((v-10)/2);scores.append(metric(v+' / '+(mod>=0?'+':'')+mod,abilities[k]));}characterContent.append(scores);
 const bio=el('details',undefined,'disclosure');bio.open=true;bio.append(el('summary','Предыстория'),el('p',h.background||'Предыстория пока не записана.','prose'));characterContent.append(bio,el('p','Лист показывает сохранённые параметры. Повышение уровня считает Nat20 по опыту кампании; эффекты экипировки подключаются отдельными механиками.','hint'));
}
document.addEventListener('solo:render',e=>renderWorkspace(e.detail));

const preferences=el('details',undefined,'disclosure shell-preferences');preferences.append(el('summary','Вид и масштаб'));const controls=el('div',undefined,'disclosure-body');
for(const[id,label,min,max,value]of [['ui-scale','Масштаб интерфейса',90,150,100],['prose-size','Размер литературного текста',16,23,18]]){const input=el('input');input.type='range';input.id=id;input.min=min;input.max=max;input.value=localStorage.getItem('solo-'+id)||value;const output=el('output');const apply=()=>{output.textContent=input.value+(id==='ui-scale'?'%':' px');document.documentElement.style.setProperty(id==='ui-scale'?'--ui-zoom':'--prose-size',id==='ui-scale'?Number(input.value)/100:input.value+'px');localStorage.setItem('solo-'+id,input.value);};input.oninput=apply;apply();controls.append(field(label,input),output);}
const distraction=el('button','Скрыть боковые панели');distraction.type='button';let focused=localStorage.getItem('solo-focus')==='on';function focusMode(){document.body.classList.toggle('reading-focus',focused);distraction.textContent=focused?'Показать боковые панели':'Скрыть боковые панели';localStorage.setItem('solo-focus',focused?'on':'off');}distraction.onclick=()=>{focused=!focused;focusMode();};focusMode();controls.append(distraction);preferences.append(controls);document.querySelector('header').after(preferences);

// r210: ручной трёхшаговый лист удалён вместе с legacy-созданием.
// Диалог #create-dialog ведёт источник → герой → редактор Nat20 (play.js).
if(current)renderWorkspace(current);

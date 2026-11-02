'use strict';
(()=>{
 let data=null,stamp=0;
 const panel=el('section',undefined,'view');panel.id='inventory';panel.append(el('p','СНАРЯЖЕНИЕ И СДЕЛКИ','eyebrow'),el('h1','Инвентарь и экипировка'),el('p','Осмотрите вещь и выберите её как контекст реплики. Чтобы надеть, использовать, передать или продать предмет, опишите намерение Мастеру.','subtext'));
 const nav=el('button','Инвентарь','nav');nav.dataset.view='inventory';nav.onclick=protect(async()=>{needCampaign();view('inventory');await load();});document.querySelector('[data-view="character"]').before(nav);document.querySelector('main').insertBefore(panel,document.querySelector('footer'));
 const summary=el('div',undefined,'metrics-grid'),equipment=el('div',undefined,'equipment-grid'),filter=el('input'),group=el('select'),sort=el('select'),items=el('div',undefined,'inventory-grid');filter.placeholder='Найти предмет';filter.setAttribute('aria-label','Поиск предметов');
 for(const[v,label]of[['all','Все предметы'],['weapon','Оружие'],['armor','Броня'],['consumable','Расходуемые'],['ammunition','Боеприпасы'],['gear','Снаряжение']]){const o=el('option',label);o.value=v;group.append(o);}group.setAttribute('aria-label','Группа предметов');
 for(const[v,label]of[['name','По названию'],['weight','По весу'],['quantity','По количеству']]){const o=el('option',label);o.value=v;sort.append(o);}sort.setAttribute('aria-label','Сортировка предметов');
 const filters=el('div',undefined,'form-grid');filters.append(filter,group,sort);panel.append(summary,equipment,filters,items);
 const shops=el('div',undefined,'merchant-list');panel.append(el('h2','Торговля'),el('p','Клик не покупает товар: условия сделки обсуждаются в диалоге, согласие подтверждается отдельной репликой.','hint'),shops);
 const icons={leather:'leather_armor',chain_mail:'leather_armor',arrows:'arrows',healing_potion:'healing_potion',longsword:'longsword',shortbow:'shortbow',club:'club',shield:'shield',rope:'rope',rations:'rations'};
 const money=n=>n+' CP · '+(n/100).toLocaleString('ru-RU')+' GP';
 const condition=value=>({intact:'Целое',damaged:'Повреждено',broken:'Сломано',worn:'Изношено'}[value]||value);
 function card(item,merchant=null){
  const b=el('button',undefined,'item-card');b.type='button';b.append(window.soloIcon?.(icons[item.catalog]||'inventory')||el('span','◇'),el('h3',item.name),el('p','× '+item.quantity+' · '+(item.quantity*item.weight_centilb/100).toLocaleString('ru-RU')+' фунтов','hint'),el('p',merchant?'Цена: '+money(item.price_cp):item.condition,'hint'));
  b.querySelector('p:last-child').textContent=merchant?'Цена: '+money(item.price_cp):condition(item.condition);
  b.onclick=()=>window.soloInspect?.({title:item.name,icon:icons[item.catalog]||'inventory',text:item.description,rows:[['Свойства',item.properties.join(' · ')||'—'],['Вес',item.weight_centilb/100+' фунтов / штука'],['Стоимость',money(item.price_cp??item.value_cp)],['Состояние',condition(item.condition)],['Источник',item.source],['Количество',item.quantity],['Владелец',merchant?.name||current.state.hero.name]],selection:{kind:merchant?'merchant_item':'item',id:item.id,...(merchant?{owner:merchant.id}:{})},label:item.name+(merchant?' · '+merchant.name:'')});return b;
 }
 function render(){if(!data)return;summary.replaceChildren(metric(money(data.wallet_cp),'Кошелёк'),metric(data.weight_centilb/100+' / '+data.capacity_centilb/100+' фунтов','Нагрузка'));equipment.replaceChildren();
  for(const[slot,label]of[['weapon','Оружие'],['armor','Броня'],['shield','Щит']]){const i=data.items.find(i=>i.id===data.equipment[slot]),box=el('section',undefined,'equipment-slot');box.append(el('h3',label));if(i)box.append(card(i));else box.append(el('p','Свободно','hint'));equipment.append(box);}
  const list=data.items.filter(i=>(group.value==='all'||group.value===i.type)&&i.name.toLowerCase().includes(filter.value.toLowerCase())).sort((a,b)=>sort.value==='weight'?b.quantity*b.weight_centilb-a.quantity*a.weight_centilb:sort.value==='quantity'?b.quantity-a.quantity:a.name.localeCompare(b.name,'ru'));
  items.replaceChildren(...list.map(i=>card(i)));if(!list.length)items.append(el('p','Предметов в этой группе нет.','hint'));
  shops.replaceChildren();for(const m of data.merchants){const shop=el('section',undefined,'page-card'),stock=el('div',undefined,'inventory-grid');shop.append(el('h3',m.name),el('p','Отношение: '+m.relationship,'hint'),stock);stock.append(...m.stock.map(i=>card(i,m)));shops.append(shop);}if(!data.merchants.length)shops.append(el('p','В этой сцене нет подтверждённого доступного торговца.','hint'));
 }
 async function load(){if(!current)return;const cid=campaignId,seq=++stamp;
  if(current.backend==='nat20'){const result=await api('/campaigns/'+cid+'/character');if(cid!==campaignId||seq!==stamp)return;data=null;summary.replaceChildren(el('p','Подтверждённое снаряжение Nat20. Использование предмета проверяет движок.','hint'));equipment.replaceChildren();shops.replaceChildren();items.replaceChildren();filters.hidden=true;
   for(const entry of result.equipment||[]){const row=typeof entry==='string'?{id:entry,name:entry}:entry,b=el('button',row.name||row.label||row.id,'item-card');b.type='button';b.onclick=()=>window.soloInspect?.({title:row.name||row.label||row.id,text:row.description||'Предмет подтверждён серверным листом. Выберите действие на игровой сцене или опишите намерение.',rows:[],selection:{kind:'item',id:row.id},label:row.name||row.id});items.append(b);}if(!items.childElementCount)items.append(el('p','В листе пока нет снаряжения.','hint'));return;}
  filters.hidden=false;const result=await api('/campaigns/'+cid+'/inventory');if(cid!==campaignId||seq!==stamp)return;data=result;render();}
 filter.oninput=group.onchange=sort.onchange=render;document.addEventListener('solo:render',()=>{if(panel.classList.contains('active'))load().catch(e=>toast(e.message));});
})();

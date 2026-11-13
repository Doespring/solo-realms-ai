'use strict';
// r217: смерть, сложность и награды — правила, которые выбирает игрок.
// Экран только читает и пишет три значения; числа боя считает движок по
// таблице solo/nat20_settings.py, а журнал называет, что именно изменилось.
(function(){
  const ids={death:'rules-death',difficulty:'rules-difficulty',rewards:'rules-rewards',effects:'rules-effects',save:'rules-save'};
  const nodes={};for(const[key,id]of Object.entries(ids))nodes[key]=document.getElementById(id);
  // В оболочке ?ui=chat панели нет — молча выходим, а не падаем на старте.
  if(!nodes.death||!nodes.save)return;
  function show(settings){
    if(!settings)return;
    for(const key of ['death','difficulty','rewards'])if(nodes[key])nodes[key].value=settings[key];
    // Пока значения не пришли из кампании, сохранять нечего: иначе клик по
    // кнопке записал бы дефолты экрана вместо правил игрока.
    nodes.save.disabled=false;
    if(nodes.effects)nodes.effects.textContent=[settings.death_text,settings.difficulty_text,settings.rewards_text].filter(Boolean).join(' ');
  }
  async function load(){
    if(typeof campaignId==='undefined'||!campaignId)return;
    try{show(await api('/campaigns/'+campaignId+'/settings'));}catch(error){if(nodes.effects)nodes.effects.textContent=error.message;}
  }
  nodes.save.onclick=protect(async()=>{
    if(typeof campaignId==='undefined'||!campaignId)throw new Error('Откройте или создайте кампанию.');
    const saved=await api('/campaigns/'+campaignId+'/settings',
      {death:nodes.death.value,difficulty:nodes.difficulty.value,rewards:nodes.rewards.value,
       request_id:'rules-'+Date.now().toString(36)},'PUT');
    show(saved.settings);
    toast(saved.changed?'Правила кампании сохранены и записаны в журнал.':'Правила не изменились.');
    if(typeof refresh==='function')await refresh();
  });
  document.querySelectorAll('[data-view="master"]').forEach(button=>button.addEventListener('click',()=>protect(load)()));
  document.addEventListener('solo:packet',event=>show(event.detail&&event.detail.snapshot&&event.detail.snapshot.settings));
  protect(load)();
})();

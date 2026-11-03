/* A single library of player adventures. Existing advanced tools keep their routes. */
(()=>{
 'use strict';window.soloUnifiedChronicles=true;let stamp=0;
 const oldDrafts=$('creation-drafts');if(oldDrafts)oldDrafts.hidden=true;
 $('campaigns').querySelector('h1').textContent='Мои приключения';
 const labels={draft:'Черновик',importing:'Извлечение материалов',preparing:'AI готовит приключение',ready:'Готово к созданию героя',needs_review:'Подготовка требует внимания'};
 loadCampaigns=async function(){const seq=++stamp,result=await api('/chronicles');if(seq!==stamp)return;const box=$('campaign-list');box.replaceChildren();
  function card(row,status){const article=el('article',undefined,'chronicle-card'),top=el('div',undefined,'chronicle-card-top');top.append(el('h2',row.name),el('span',status,'chronicle-status'));article.append(top);if(row.description)article.append(el('p',row.description,'chronicle-description'));article.append(el('p',row.hero?(row.hero+(row.class_name?' · '+row.class_name:'')+(row.level?' · уровень '+row.level:'')):'Герой ещё не создан','hint'));if(row.scene)article.append(el('p','Сейчас: '+row.scene));article.append(el('small',new Date(row.updated*1000).toLocaleString('ru-RU')));box.append(article);return article;}
  // A long list of identical primary buttons has no focus. Only the most
  // recently played adventure carries the primary accent; the rest are quiet.
  const recent=result.campaigns[0]?.id;
  if(result.campaigns.length)box.append(el('h2','Продолжить приключение','chronicle-runs-title'));
  for(const row of result.campaigns){const article=card(row,'Прохождение'),resume=el('button','Продолжить',row.id===recent?'primary':'quiet');resume.onclick=protect(async()=>{if(busy)throw new Error('Дождитесь завершения текущего ответа.');campaignId=row.id;localStorage.setItem('solo-campaign',row.id);await refresh();view('play');});article.append(resume);}
  if(result.drafts.length)box.append(el('h2','Незавершённые приключения','chronicle-drafts-title'));
  for(const row of result.drafts){
    const article=card(row,labels[row.status]||'Черновик');
    // One hero service. A ready adventure always continues in the single Native
    // wizard, which also imports a sheet and levels a hero up; the older
    // character screen stays reachable only for drafts that already hold a hero
    // there, so no draft falls into a second source of character rules.
    const action=el('button',row.status==='ready'?'Создать героя':'Продолжить',row.status==='ready'?'primary':undefined);
    action.onclick=protect(async()=>{
      if(row.status==='ready'){await window.soloOpenPreparedWorkflow(row);return;}
      if(row.character_mode)await window.soloOpenCharacter(row.id,row.character_mode);
      else await window.soloOpenCreation(row.id);
    });
    article.append(action);}
  if(!result.campaigns.length&&!result.drafts.length)box.append(el('p','Загрузите материалы или опишите новую историю. Здесь сохранятся ваши приключения и черновики.','hint'));
 };
})();

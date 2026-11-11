"""Exercise the application's real DOM handlers with an explicit static fixture."""
import json,time

SCRIPT=r"""
(async()=>{
 const result={mock_only:true,paid_calls:0,steps:[]};window.__creationSmoke={running:true};
 const wait=async(test)=>{for(let i=0;i<180;i++){if(test())return;await new Promise(r=>setTimeout(r,50));}throw Error('UI timeout: '+test);};
 const button=(root,text)=>{const b=[...document.querySelectorAll(root+' button')].find(x=>x.textContent.trim()===text);if(!b)throw Error('Missing button '+text);if(b.disabled)throw Error('Disabled button '+text);b.click();};
 try{
  window.soloOpenCreation();await wait(()=>document.querySelector('#creation.active textarea'));
  const input=document.querySelector('#creation-description');input.value='Оригинальное тестовое приключение о северном караване, D&D 2014';input.dispatchEvent(new Event('input'));
  button('#creation','Сохранить и выйти');await wait(()=>document.querySelector('#campaigns.active .chronicle-card'));
  const draft=localStorage.getItem('solo-creation-draft');if(!draft)throw Error('No persisted draft');
  await window.soloOpenCreation(draft);if(document.querySelector('#creation-description').value!==input.value)throw Error('Draft lost');result.adventure_save_resume=true;
  button('#creation','Подготовить приключение');await wait(()=>document.querySelector('#preparation-confirm[open]'));
  const edition=document.querySelector('#preparation-confirm select');edition.value='dnd2014';edition.dispatchEvent(new Event('change'));
  button('#preparation-confirm','Подготовить через API');await wait(()=>!document.querySelector('#creation .character-entry').hidden);
  result.preparation_ready=true;button('#creation','Создать героя по описанию');await wait(()=>document.querySelector('#character-creation.active .character-step textarea'));
  const description=document.querySelector('#character-creation .character-step textarea');description.value='Арден, человек-воин, служитель храма.';description.dispatchEvent(new Event('input'));
  const consent=document.querySelector('#character-creation .character-consent input');consent.checked=true;consent.dispatchEvent(new Event('change'));
  button('#character-creation','Подготовить проект героя');await wait(()=>document.querySelector('#character-creation .character-valid'));
  result.text_proposal_valid=true;button('#character-creation','Перейти в конструктор');
  await wait(()=>document.querySelector('#character-creation .character-steps button'));
  for(let i=0;i<10;i++){
   document.querySelectorAll('#character-creation .character-steps button')[i].click();
   await wait(()=>document.querySelector('#character-creation .character-step').textContent.includes('Шаг '+(i+1)+' из 10'));
   result.steps.push(document.querySelector('#character-creation .character-step h2').textContent);
  }
  if(!document.querySelector('#character-creation .character-step').textContent.includes('КД 19'))throw Error('Incorrect armor preview');
  button('#character-creation','Сохранить и выйти');await wait(()=>document.querySelector('#campaigns.active'));
  await window.soloOpenCharacter(draft,'visual');await wait(()=>document.querySelector('#character-creation .character-step h2')?.textContent==='Подтверждение');
  result.character_save_resume=true;
  const agree=document.querySelector('#character-creation .character-consent input');agree.checked=true;agree.dispatchEvent(new Event('change'));
  button('#character-creation','Подтвердить героя и начать историю');await wait(()=>document.querySelector('#play.active'));
  const id=localStorage.getItem('solo-campaign');const response=await fetch('/api/campaigns/'+id);const campaign=await response.json();
  if(campaign.state.hero.name!=='Арден'||campaign.state.hero.ac!==19||campaign.state.hero.hp!==12)throw Error('Confirmed mechanics differ');
  result.confirmed_hero={name:campaign.state.hero.name,hp:12,ac:19};result.campaign=id;
  const action=document.querySelector('#player-input');action.value='Открываю дверь';action.dispatchEvent(new Event('input'));document.querySelector('#send').click();
  await wait(()=>!document.querySelector('#send').disabled);
  const played=await(await fetch('/api/campaigns/'+id)).json();
  const door=played.state.locations.find(p=>p.name===played.state.location).interactions.find(o=>o.id==='smoke-door');
  if(!door?.opened||played.history.length!==1||played.history[0].narrative_status!=='complete')throw Error('Prepared object free-text turn did not complete');
  result.first_turn_prepared_object=true;result.narrator_stream_complete=true;
  await window.soloOpenCharacter(draft,'visual');await wait(()=>document.querySelector('#play.active'));if(localStorage.getItem('solo-campaign')!==id)throw Error('Duplicate launch');
  result.confirmation_resume_idempotent=true;result.ok=true;
 }catch(e){result.ok=false;result.error=String(e);result.active=document.querySelector('.view.active')?.id;result.status=[...document.querySelectorAll('[role=status]')].map(x=>x.textContent);}
 window.__creationSmoke=result;
})();true
"""

def creation_smoke(client,window):
    window.evaluate_js(SCRIPT);deadline=time.monotonic()+32
    while time.monotonic()<deadline:
        value=window.evaluate_js('JSON.stringify(window.__creationSmoke)')
        result=json.loads(value) if isinstance(value,str) else value
        if result and not result.get('running'):
            assert result.get('ok'),result
            cards=client.get('/api/chronicles').json()
            assert any(c['id']==result['campaign'] for c in cards['campaigns'])
            assert 'SYNTHETIC_GM_PRIVATE' not in json.dumps(cards)
            result['public_cards_no_private_marker']=True
            return result
        time.sleep(.05)
    raise AssertionError('Native creation UI did not finish: '+str(result))

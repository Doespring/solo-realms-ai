import asyncio,copy
import pytest
from solo.app import PlayRequest
from solo.selection import Selection,resolve,player_text
from solo.game import Game
from solo.llm import MockLLM
from solo.contracts import Plan,Step
from test_inventory import setup,grant

def test_master_address_executes_item_action_once_and_question_stays_gm(tmp_path):
    store,cid,service,mid=setup(tmp_path);id=grant(service,cid,'longsword');game=Game(store,MockLLM())
    request=PlayRequest(request_id='equip-text',text='Мастер, я беру в руки меч.',selection=Selection(kind='item',id=id))
    response=asyncio.run(game.play(cid,request));assert response['kind']=='event'
    assert store.campaign(cid)['state']['economy']['equipment']['weapon']==id
    assert asyncio.run(game.play(cid,request))['event']['id']==response['event']['id']
    assert asyncio.run(game.play(cid,PlayRequest(request_id='question',text='Мастер, я могу ли атаковать отсюда?')))['kind']=='gm'

def test_selection_is_read_only_owned_and_public(tmp_path):
    store,cid,service,mid=setup(tmp_path);id=grant(service,cid,'rope');state=store.campaign(cid)['state'];before=copy.deepcopy(state)
    assert resolve(state,Selection(kind='item',id=id))['name']=='Верёвка 50 футов'
    merchant=resolve(state,Selection(kind='merchant_item',owner=mid,id=next(i['id'] for i in state['economy']['items'] if i['owner']=='merchant:'+mid)))
    assert set(merchant)=={'kind','id','name','owner'}
    for selection in [Selection(kind='item',id='secret'),Selection(kind='merchant_item',owner='other',id=id),Selection(kind='npc',id='Невидимый враг'),Selection(kind='cell',x=1,y=1,z=0)]:
        with pytest.raises(ValueError):resolve(state,selection)
    assert state==before

def test_selected_context_only_reaches_interpreter_as_data(tmp_path):
    store,cid,service,mid=setup(tmp_path);id=grant(service,cid,'rope')
    class Capture(MockLLM):
        async def interpret(self,text,scene):
            self.scene=scene
            return Plan(summary='Уточнить',steps=[],unclear=['Что именно?'])
    llm=Capture();state=copy.deepcopy(store.campaign(cid)['state'])
    result=asyncio.run(Game(store,llm).play(cid,PlayRequest(request_id='selected',text='Сначала посмотрю вот это, а потом решу, пригодится ли оно.',selection=Selection(kind='item',id=id))))
    assert result['kind']=='reply' and llm.scene['player_selection']['id']==id
    assert store.campaign(cid)['state']==state

@pytest.mark.parametrize('text,gm',[('Мастер, почему тут помеха?',True),('Мастер, я осматриваю дверь.',False),('Мастер, я не атакую стража.',False),('Мастер, я спрашиваю Ирину: кто пришёл?',False),('Мастер, объясни правила отдыха.',True)])
def test_address_routing(text,gm):assert player_text(text)[1]==gm

def test_selected_item_and_cell_require_explicit_action_and_keep_delayed_intention(tmp_path):
    store,cid,service,mid=setup(tmp_path);id=grant(service,cid,'longsword');game=Game(store,MockLLM())
    r=asyncio.run(game.play(cid,PlayRequest(request_id='selected-equip',text='Беру в руки выбранный предмет',selection=Selection(kind='item',id=id))))
    assert r['kind']=='event' and store.campaign(cid)['state']['economy']['equipment']['weapon']==id
    from solo.combat_service import CombatService,StartEncounter
    from test_rulesets import Dice
    CombatService(store,rng=Dice(20,1)).start(cid,StartEncounter(request_id='start',confirm=True))
    state=store.campaign(cid)['state'];before=copy.deepcopy(state)
    from solo.combat_view import project
    battle=project(state);hero=next(a for a in battle['actors'] if a['team']=='hero');cell=next(c for c in battle['map']['cells'] if c['visible'] and not c['blocked'] and c['z']==hero['position']['z'] and abs(c['x']-hero['position']['x'])+abs(c['y']-hero['position']['y'])==1)
    selection=Selection(kind='cell',x=cell['x']+1,y=cell['y']+1,z=cell['z'])
    assert resolve(state,selection)['kind']=='cell' and state==before
    response=asyncio.run(game.play(cid,PlayRequest(request_id='selected-move',text='Мастер, я иду на выбранную клетку.',selection=selection)))
    assert response['kind']=='event' and store.campaign(cid)['state']['combat']['actors'][0]['position']=={'x':cell['x'],'y':cell['y'],'z':cell['z']}
    assert not game.llm.metrics

def test_actor_selection_and_fog_cannot_include_unseen_enemy(tmp_path):
    from solo.combat_service import CombatService,StartEncounter
    from test_rulesets import Dice
    store,cid,service,mid=setup(tmp_path);CombatService(store,rng=Dice(20,1)).start(cid,StartEncounter(request_id='start',confirm=True))
    state=store.campaign(cid)['state']
    state['combat']['actors'][1]['position']={'x':10,'y':8,'z':0};state['combat']['map']['cells']=[{**c,'light':'dark'} for c in state['combat']['map']['cells']]
    with pytest.raises(ValueError):resolve(state,Selection(kind='actor',id='foe-1'))
    with pytest.raises(ValueError):resolve(state,Selection(kind='cell',x=11,y=9,z=0))

def test_prepared_encounter_confirmation_keeps_player_text_and_replay(tmp_path):
    from solo.world_service import WorldService,WorldImport
    store,cid,service,mid=setup(tmp_path)
    WorldService(store).import_world(cid,WorldImport(request_id='author',confirm=True,package={'name':'Тест','source':'Авторский тест','events':[{'id':'fight','kind':'encounter','after_minutes':0,'text':'Незнакомец выходит на дорогу.','encounter':{'environment':'room','seed':1}}]}))
    game=Game(store,MockLLM());asyncio.run(game.play(cid,PlayRequest(request_id='observe',text='Осматриваюсь')))
    request=PlayRequest(request_id='consent',text='Подтверждаю столкновение')
    event=asyncio.run(game.play(cid,request))['event'];assert store.request(cid,'consent')['original']==request.text
    assert asyncio.run(game.play(cid,request))['event']['id']==event['id']

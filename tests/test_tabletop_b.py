import asyncio,copy,json,sqlite3
import pytest,httpx
from fastapi.testclient import TestClient
from solo.app import create_app,PlayRequest
from solo.contracts import Hero,Step,Plan
from solo.storage import Store,START
from solo.rulesets import BUILTINS,RulesetEngine
from solo.llm import MockLLM
from solo.game import Game
from solo.inventory import new_item,InventoryEngine
from solo.interactions import SceneObject
from solo.world import WorldLocation,ScenarioPackage
from solo import natural
from test_rulesets import Dice

def scene():
    potion=new_item('healing_potion',1,'scene','Подтверждённый сценарий B')
    a=dict(name='Комната',description='Пустая комната.',npc=[dict(name='Ирина',character='Хозяйка')],objects=[],facts=[],known_to_hero=True,interactions=[
        SceneObject(id='open-door',name='дверь',kind='door',lock='unlocked',destination='Кладовая',description='Деревянная дверь без защёлки.').model_dump(),
        SceneObject(id='locked-door',name='железная дверь',aliases=['железную дверь'],kind='door',lock='locked').model_dump(),
        SceneObject(id='potion',name='лечебное зелье',kind='item',item=potion,description='Запечатанный пузырёк.').model_dump()])
    b=dict(name='Кладовая',aliases=['кладовую'],description='Полки.',npc=[],objects=[],facts=[],known_to_hero=True)
    return dict(location='Комната',locations=[a,b],facts=[],deferred=[],turn=0,world={'settings':{'map_travel':False}},geography={'nodes':[{'id':'a','name':'Комната','coordinates':[0,0]},{'id':'b','name':'Кладовая','coordinates':[1,0]}],'routes':[{'from':'a','to':'b','minutes':1,'meters':2,'description':'дверной проём'}]})
def setup_store(tmp_path):
    store=Store(tmp_path/'game.sqlite3');c=store.create('Stage B',Hero(name='Лира',hp=2),BUILTINS['dnd2014'],initial_state=scene());return store,c['id']

@pytest.mark.parametrize('name,word',[('железная дверь','железную дверь'),('синяя дверь','синюю дверь'),('старая железная дверь','старую железную дверь')])
def test_declined_door_name_from_real_interpreter_packet_needs_no_manual_alias(tmp_path,name,word):
    state=scene();door=state['locations'][0]['interactions'][1];door.update(name=name,aliases=[])
    store=Store(tmp_path/'game.sqlite3');campaign=store.create('Склонение',Hero(name='Лира'),BUILTINS['dnd2014'],initial_state=state)
    event=execute(store,campaign['id'],[Step(kind='use',text='Пытаюсь открыть '+word,target=name),Step(kind='speak',text='Ирина, что случилось?',target='Ирина')])
    assert event['results'][0]['status']=='impossible' and 'заперта' in event['results'][0]['text']
    assert not any(r.get('type')=='needs_context' for r in event['results'])

def test_declension_cannot_substitute_another_named_door(tmp_path):
    store,cid=setup_store(tmp_path)
    event=execute(store,cid,[Step(kind='use',text='Пытаюсь открыть деревянную дверь',target='железная дверь')])
    assert event['results'][0]['status']=='clarification'
def execute(store,cid,steps,rng=None,key='one'):
    action=store.stage(cid,key,'Тест B',Plan(summary='Тест',steps=steps),store.campaign(cid)['version'],{'natural':True});return store.apply(action['id'],rng)

@pytest.mark.parametrize('edition',['dnd2014','dnd2024'])
@pytest.mark.parametrize('expert',[False,True])
@pytest.mark.parametrize('text',['Убеждаю Ирину помочь','Уговариваю Ирину помочь','Пытаюсь убедить Ирину помочь'])
def test_natural_skill_equals_direct_engine(tmp_path,edition,expert,text):
    hero=Hero(name='Лира',level=5,skill_proficiencies=['persuasion'],expertise=['persuasion'] if expert else [],abilities=dict(strength=10,dexterity=10,constitution=10,intelligence=10,wisdom=10,charisma=16))
    store=Store(tmp_path/'skills.sqlite3');c=store.create('Навыки',hero,BUILTINS[edition]);cid=c['id']
    event=execute(store,cid,[Step(kind='speak',text=text,target='Ирина')],Dice(10))
    actual=event['results'][0];expected=RulesetEngine(BUILTINS[edition]).skill(hero,'persuasion',12,rng=Dice(10))
    for key in expected:assert actual[key]==expected[key]
    assert actual['bonus']==(9 if expert else 6)

def test_scenario_skill_advantage_and_no_model_proficiency(tmp_path):
    store,cid=setup_store(tmp_path);state=store.campaign(cid)['state'];state['locations'][0]['checks']={'look':{'triggers':['ищу следы'],'ability':'wisdom','skill':'survival','dc':14,'advantage':True}}
    state['hero']['skill_proficiencies']=['survival'];state['hero']['expertise']=['survival'];state['hero']['level']=5
    model=Plan(summary='Следы',steps=[Step(kind='check',text='Ищу следы',skill='athletics',ability='strength',dc=1,proficient=True)])
    checked,_=natural.guard(model,'Ищу следы',state);assert not checked.steps[0].skill
    result=natural.resolve(state,checked,Dice(4,12))[0][0]
    expected=RulesetEngine(BUILTINS['dnd2014']).skill(Hero.model_validate(state['hero']),'survival',14,advantage=True,rng=Dice(4,12))
    for key in expected:assert result[key]==expected[key]

def test_mapless_six_interactions_and_undo_idempotence(tmp_path):
    store,cid=setup_store(tmp_path);game=Game(store,MockLLM());before=store.campaign(cid)['state']
    def play(text,key):return asyncio.run(game.play(cid,PlayRequest(text=text,request_id=key)))
    locked=play('Пытаюсь открыть железную дверь','locked')['event'];assert locked['results'][0]['status']=='impossible'
    looked=play('Осматриваю дверь','look')['event'];assert looked['results'][0]['text']=='Деревянная дверь без защёлки.'
    blocked=play('Иду в кладовую','blocked')['event'];assert blocked['results'][0]['type']=='travel_stop'
    opened=play('Открываю дверь','open')['event'];assert opened['results'][0]['action']=='open'
    assert play('Открываю дверь','open')['event']['id']==opened['id']
    picked=play('Поднимаю лечебное зелье','pick')['event'];assert picked['results'][0]['action']=='pickup'
    assert play('Поднимаю лечебное зелье','pick')['event']['id']==picked['id']
    assert play('Поднимаю лечебное зелье','pick-again')['event']['results'][0]['status']=='impossible'
    used=play('Использую лечебное зелье','use')['event'];assert used['results'][0]['kind']=='inventory' and store.campaign(cid)['state']['hero']['hp']>2  # existing inventory command path
    moved=play('Иду в кладовую','move')['event'];assert moved['results'][0]['arrived']
    assert store.campaign(cid)['state']['location']=='Кладовая' and not game.llm.metrics
    while any(not e['undone'] for e in store.history(cid)):store.undo(cid)
    assert store.campaign(cid)['state']==before

def test_compound_pick_use_is_one_atomic_event_with_one_consumption(tmp_path):
    store,cid=setup_store(tmp_path)
    event=execute(store,cid,[Step(kind='other',text='Поднимаю лечебное зелье',target='лечебное зелье',action_id='a'),Step(kind='use',text='Использую лечебное зелье',item='лечебное зелье',action_id='b',after=['a'])],Dice(2,3))
    assert [r['status'] for r in event['results']]==['supported','supported']
    assert event['after_state']['hero']['hp']==9
    assert event['after_state']['economy']['items'][0]['quantity']==0
    assert store.apply(event['action'],Dice())['id']==event['id']
    store.undo(cid);assert store.campaign(cid)['state']==event['before_state']

def test_failed_pickup_does_not_leak_mutation_and_blocks_use(tmp_path):
    store,cid=setup_store(tmp_path)
    def heavy(s):s['locations'][0]['interactions'][2]['item']['weight_centilb']=100000;return [{'type':'scripted','text':'Heavy fixture'}]
    store.record_scenario(cid,'heavy','Fixture',heavy)
    before=store.campaign(cid)['state'];event=execute(store,cid,[Step(kind='other',text='Беру лечебное зелье',target='лечебное зелье',action_id='a'),Step(kind='use',text='Использую лечебное зелье',item='лечебное зелье',after=['a'])])
    assert [r['type'] for r in event['results']]==['impossible','halted']
    assert not event['after_state']['locations'][0]['interactions'][2]['taken']
    assert event['after_state']['hero']==before['hero'] and 'economy' not in event['after_state']

def test_optional_unsupported_preserves_supported_independent_intent(tmp_path):
    store,cid=setup_store(tmp_path)
    event=execute(store,cid,[Step(kind='other',text='По возможности левитирую',action_id='optional'),Step(kind='wait',text='В любом случае жду 1 минуту')])
    assert event['results'][0]['status']=='optional_unsupported' and event['results'][1]['type']=='wait'
    assert event['after_state']['elapsed_minutes']==1
    event=execute(store,cid,[Step(kind='other',text='Необязательно левитирую',action_id='optional'),Step(kind='wait',text='В любом случае жду 1 минуту',after=['optional'])],key='dependent')
    assert event['results'][1]['type']=='halted'

def test_critical_unsupported_preflight_has_no_roll_or_mutation(tmp_path):
    store,cid=setup_store(tmp_path);before=store.campaign(cid)['state']
    e=execute(store,cid,[Step(kind='other',text='Открываю дверь',target='дверь'),Step(kind='other',text='Применяю неизвестное заклинание'),Step(kind='move',text='Иду в кладовую',target='Кладовая')],Dice())
    assert e['results'][1]['status']=='critical_unsupported' and e['after_state']==before

def test_ambiguous_unknown_hidden_objects_and_negation_are_not_success(tmp_path):
    store,cid=setup_store(tmp_path)
    state=store.campaign(cid)['state'];state['locations'][0]['interactions'][0]['lock']='unknown'
    out=natural.resolve(state,Plan(summary='Дверь',steps=[Step(kind='other',text='Открываю дверь',target='дверь')]))[0]
    assert out[0]['status']=='clarification' and not state['locations'][0]['interactions'][0]['opened']
    out=natural.resolve(state,Plan(summary='Отказ',steps=[Step(kind='other',text='Не открываю дверь',target='дверь',disposition='negated')]))[0]
    assert out[0]['type']=='denied'
    state['locations'][0]['interactions'][0]['visible']=False
    from solo.world import location_view
    assert 'дверь' not in location_view(state['locations'][0])['objects']

def test_character_gate_notes_dice_preserve_canonical_state_and_no_ai(tmp_path):
    app=create_app(tmp_path/'api.sqlite3',MockLLM())
    with TestClient(app) as c:
        cid=c.post('/api/campaigns',json={'name':'B','hero':{'name':'Лира'},'ruleset':'dnd2014'}).json()['id'];path='/api/campaigns/'+cid
        before=app.state.store.campaign(cid);hero=copy.deepcopy(before['state']['hero']);hero.update(level=20,spells=['arbitrary'])
        body={'request_id':'edit','hero':hero}
        assert c.put(path+'/character',json=body).status_code==403
        assert c.post('/api/advanced/session',json={'confirm':False}).status_code==422
        assert c.post('/api/advanced/session',json={'confirm':True}).status_code==200
        assert c.put(path+'/character',json=body).status_code==200
        c.post(path+'/undo');c.delete('/api/advanced/session')
        assert c.put(path+'/character',json={**body,'request_id':'denied'}).status_code==403
        canonical=app.state.store.campaign(cid)
        note={'request_id':'note','text':'Моя догадка: дверь волшебная <script>','expected_revision':0}
        assert c.put(path+'/notes',json=note).json()['revision']==1
        assert c.put(path+'/notes',json=note).json()['revision']==1
        assert c.put(path+'/notes',json={**note,'request_id':'conflict'}).status_code==409
        roll={'request_id':'roll','sides':20,'count':2,'modifier':3}
        a=c.post(path+'/dice',json=roll).json();assert a==c.post(path+'/dice',json=roll).json() and a['total']==sum(a['dice'])+3
        assert c.post(path+'/dice',json={**roll,'sides':6}).status_code==409
        assert c.post(path+'/dice',json={**roll,'request_id':'bad','count':1000}).status_code==422
        assert app.state.store.campaign(cid)==canonical and not app.state.game.llm.metrics
        packet=app.state.game.compiler.interpreter(canonical);assert note['text'] not in json.dumps(packet,ensure_ascii=False)
    with sqlite3.connect(tmp_path/'api.sqlite3') as db:
        assert db.execute('pragma integrity_check').fetchone()[0]=='ok' and not db.execute('pragma foreign_key_check').fetchall()

def test_real_cloud_adapter_validator_engine_narrator_regenerate_and_cancel(tmp_path):
    from test_ai_roles import setup,packet
    from test_cloud import sse
    requests=[]
    actions=[dict(id='a',category='ACTION_ATTEMPT',kind='other',text='Открываю дверь',target='дверь',item='',disposition='now',condition='',condition_state='not_applicable',after=[]),dict(id='b',category='ACTION_ATTEMPT',kind='move',text='затем иду в кладовую',target='Кладовая',item='',disposition='now',condition='',condition_state='not_applicable',after=['a'])]
    def respond(req):
        data=json.loads(req.content);requests.append(data)
        answer=json.dumps(packet(actions),ensure_ascii=False) if len(requests)==1 else 'Дверь отворяется. За ней виднеются полки кладовой.'
        return sse([{'choices':[{'delta':{'content':answer[:12]}}]},{'choices':[{'delta':{'content':answer[12:]}}]}])
    llm,profile=setup(tmp_path,respond);store,cid=setup_store(tmp_path);game=Game(store,llm)
    def secret(s):s['locations'][0]['secrets']=['GM_SECRET_SENTINEL'];s['locations'][0]['interactions'].append(SceneObject(id='secret',name='SECRET_OBJECT',visible=False).model_dump());return [{'type':'scripted','text':'Fixture'}]
    store.record_scenario(cid,'secret','Fixture',secret)
    async def run():
        request=PlayRequest(request_id='compound',text='Открываю дверь, затем иду в кладовую')
        result=await game.play(cid,request);assert result['kind']=='event',result
        eid=result['event']['id'];canonical=store.campaign(cid)
        stream=game.narrate(eid)
        async for part in stream:
            if part['type']=='token':break
        await stream.aclose()
        assert store.campaign(cid)==canonical
        assert (await game.play(cid,request))['event']['id']==eid
        assert [p async for p in game.narrate(eid,regenerate=True)]
        assert store.campaign(cid)==canonical and len(requests)==3
        assert store.action(result['event']['action'])['plan']['steps'][1]['after']==['a']
        assert result['event']['results'][0]['status']=='supported' and result['event']['results'][1]['arrived']
        await llm.close()
    asyncio.run(run())
    wire=json.dumps(requests,ensure_ascii=False);assert 'GM_SECRET_SENTINEL' not in wire and 'SECRET_OBJECT' not in wire

def test_note_retries_are_independent_between_campaigns_and_preserve_newer_edits(tmp_path):
    with TestClient(create_app(tmp_path/'solo.sqlite3',MockLLM())) as client:
        ids=[client.post('/api/campaigns',json=dict(name=name,hero=dict(name=name))).json()['id'] for name in ('A','B')]
        before=[client.get('/api/campaigns/'+cid).json()['state'] for cid in ids]
        urls=['/api/campaigns/'+cid+'/notes' for cid in ids]
        first=dict(request_id='save',text='Заметка A',expected_revision=0)
        assert client.put(urls[0],json=first).json()['revision']==1
        assert client.put(urls[1],json=dict(first,text='Заметка B')).json()['revision']==1
        assert client.put(urls[0],json=first).json()['revision']==1
        newer=dict(request_id='newer',text='Новая заметка A',expected_revision=1)
        assert client.put(urls[0],json=newer).json()['revision']==2
        assert client.put(urls[0],json=first).json()['text']==newer['text']
        assert client.get(urls[1]).json()['text']=='Заметка B'
        assert [client.get('/api/campaigns/'+cid).json()['state'] for cid in ids]==before


def test_pc_guard_does_not_change_npc_contract():
    assert Hero(name='Великан',level=20,size='Large',abilities={k:30 for k in Hero(name='x').abilities}).abilities['strength']==30

def test_model_cannot_substitute_object_or_drop_condition(tmp_path):
    store,cid=setup_store(tmp_path);state=store.campaign(cid)['state']
    plan=Plan(summary='Подмена',steps=[Step(kind='other',text='Открываю железную дверь',target='дверь')])
    result=natural.resolve(state,plan)[0][0];assert result['status']=='clarification'
    assert not state['locations'][0]['interactions'][0]['opened']
    dropped=Plan(summary='Условие',steps=[Step(kind='other',text='Открываю дверь',target='дверь')])
    assert natural.guard(dropped,'Открываю дверь, если Ирина уйдёт',state)[0] is None
    from solo.interactions import perform
    inv=InventoryEngine(state);item=new_item('healing_potion',1,'hero','Fixture');inv.data.items.append(item);inv.flush()
    assert perform(state,Step(kind='use',text='Использую верёвку',item=item.id))['status']=='clarification'
    assert state['hero']['hp']==2

def test_optional_unknown_target_passes_validator_without_blocking_other_intent(tmp_path):
    from solo.action_validator import ActionValidator
    from test_ai_roles import action,packet
    a={**action(),'kind':'other','target':'неизвестный механизм','text':'По возможности включаю неизвестный механизм'}
    b={**action(),'id':'b','kind':'wait','target':'','text':'В любом случае жду 1 минуту'}
    store,cid=setup_store(tmp_path);state=store.campaign(cid)['state']
    plan=ActionValidator.validate(packet([a,b]),a['text']+'; '+b['text'],Game(store,MockLLM()).compiler.interpreter(store.campaign(cid)))
    results=natural.resolve(state,plan)[0]
    assert [r['type'] for r in results]==['unsupported','wait']

def test_frozen_tabletop_probe_uses_public_endpoints_without_network(tmp_path):
    from solo.desktop_observations import tabletop_smoke
    app=create_app(tmp_path/'frozen-probe.sqlite3',MockLLM())
    with TestClient(app) as client:assert tabletop_smoke(client)['mapless_six_interactions']
    assert not app.state.game.llm.metrics

def test_observable_door_condition_and_critical_conditional_preflight(tmp_path):
    store,cid=setup_store(tmp_path)
    event=execute(store,cid,[Step(kind='other',text='Открываю дверь',target='дверь'),Step(kind='move',text='Иду в кладовую',target='Кладовая',condition='если дверь открыта')])
    assert event['results'][1]['arrived'] and event['after_state']['location']=='Кладовая'
    store.undo(cid);before=store.campaign(cid)['state']
    event=execute(store,cid,[Step(kind='other',text='Открываю дверь',target='дверь'),Step(kind='other',text='Телепортируюсь',condition='если дверь открыта')],Dice(),key='unknown-conditional')
    # A condition can become true inside the sequence; its unsupported effect rolls back the transition.
    assert event['results'][1]['status']=='critical_unsupported'
    assert event['after_state']==before and event['results'][0]['status']=='rolled_back'
    # Known unsupported conditional in an unresolved condition is rejected before rolls.
    state=copy.deepcopy(before)
    out=natural.resolve(state,Plan(summary='Будущий успех',steps=[Step(kind='wait',text='Жду 1 минуту'),Step(kind='other',text='Телепортируюсь',condition='если проверка успешна')]),Dice())[0]
    assert out[1]['status']=='critical_unsupported' and state==before

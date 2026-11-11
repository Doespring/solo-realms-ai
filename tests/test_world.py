import asyncio,copy,json,sqlite3
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app,PlayRequest
from solo.contracts import Hero,Plan,Step
from solo.storage import Store
from solo.game import Game,SceneCompiler
from solo.llm import MockLLM
from solo.world import ScenarioPackage,project,known,tick,import_package
from solo.world_service import WorldService,WorldCommand,WorldImport
from solo.combat_service import CombatService,StartEncounter
from solo.rulesets import BUILTINS
from solo import travel,natural

def setup(tmp_path):
    store=Store(tmp_path/'world.sqlite3');cid=store.create('Мир',Hero(name='Мирон'))['id'];service=WorldService(store)
    service.execute(cid,WorldCommand(request_id='mode',kind='settings',confirm=True,settings=dict(mode='mixed',map_travel=True)))
    return store,cid,service

def package(**changes):
    raw=dict(name='Лесной эпизод',source='Собственный тестовый сценарий',locations=[dict(name='Тайный лес',description='Зелёная поляна',secrets=['СКРЫТЫЙ_ТАЙНИК'],npc=[dict(name='Эль',character='Тихая',private_knowledge=['ПРИВАТНЫЙ_ПАРОЛЬ'])])],nodes=[dict(id='hidden',name='Тайный лес',coordinates=[0,2],known=False,environment='forest_road')],routes=[dict(**{'from':'tavern'},to='hidden',meters=100,minutes=3,description='Неизвестная тропа',explorable=True)],events=[])
    raw.update(changes);return ScenarioPackage.model_validate(raw)

def install(service,cid,p=None):return service.import_world(cid,WorldImport(request_id='import',confirm=True,package=p or package()))
def go(service,cid,target,key='walk'):return service.execute(cid,WorldCommand(request_id=key,kind='travel',target=target),from_map=True)

def test_map_and_free_text_share_clock_canon_and_no_llm(tmp_path):
    store,cid,service=setup(tmp_path)
    other=tmp_path/'text.sqlite3'
    with store.connection() as c,sqlite3.connect(other) as dest:c.backup(dest)
    text_store=Store(other);llm=MockLLM()
    preview=service.preview(cid,'Старая башня');assert (preview['minutes'],preview['meters'])==(30,2000)
    event=go(service,cid,'Старая башня')
    response=asyncio.run(Game(text_store,llm).play(cid,PlayRequest(request_id='walk',text='Иду к старой башне.')))
    assert response['kind']=='event' and not llm.metrics
    assert store.campaign(cid)['state']==text_store.campaign(cid)['state']
    assert go(service,cid,'Старая башня')['id']==event['id']
    assert store.campaign(cid)['state']['elapsed_minutes']==30
    with pytest.raises(ValueError):service.execute(cid,WorldCommand(request_id='walk',kind='travel',target='Берег реки'))

def test_unknown_world_data_hidden_until_explicit_exploration(tmp_path):
    store,cid,service=setup(tmp_path);install(service,cid)
    before=store.campaign(cid);public=json.dumps(project(before['state']),ensure_ascii=False)
    assert all(s not in public for s in ['Тайный лес','hidden','СКРЫТЫЙ_ТАЙНИК','ПРИВАТНЫЙ_ПАРОЛЬ'])
    assert project(before['state'])['exits']==[dict(ref='0',direction='юг',label='Неисследованная дорога')]
    with pytest.raises(ValueError):go(service,cid,'Тайный лес')
    service.execute(cid,WorldCommand(request_id='explore',kind='explore',exit='0'),from_map=True)
    after=store.campaign(cid)['state'];assert after['location']=='Тайный лес' and after['elapsed_minutes']==3 and known(after,'Тайный лес')
    with TestClient(create_app(store.path,MockLLM())) as client:
        exposed=json.dumps(client.get('/api/campaigns/'+cid).json(),ensure_ascii=False)
        assert 'Тайный лес' in exposed and 'Зелёная поляна' in exposed
        assert 'СКРЫТЫЙ_ТАЙНИК' not in exposed and 'ПРИВАТНЫЙ_ПАРОЛЬ' not in exposed
    store.undo(cid);assert store.campaign(cid)['state']==before['state']

def test_remote_npc_events_private_in_http_history_prompts_and_memory(tmp_path):
    store,cid,service=setup(tmp_path)
    p=package(events=[dict(id='move-secret',after_minutes=1,kind='npc_move',npc='Эль',target='Берег реки'),dict(id='relation-secret',after_minutes=1,kind='relationship',npc='Эль',relationship=-20,text='ЗАКРЫТАЯ_ПРИЧИНА')])
    install(service,cid,p);event=go(service,cid,'Старая башня')
    assert 'Эль' not in json.dumps(Game.public_event(event),ensure_ascii=False)
    state=store.campaign(cid)['state'];assert next(n for p in state['locations'] if p['name']=='Берег реки' for n in p['npc'] if n['name']=='Эль')['present']
    ids=store.memory.identities(cid,state)
    hero=store.memory.retrieve(cid,audience=[ids['hero']],query='Эль переместился',limit=20,budget=10000)
    npc=store.memory.retrieve(cid,audience=[ids['npc']['Эль']],query='Эль переместился',limit=20,budget=10000)
    assert 'Эль переместился' not in json.dumps(hero,ensure_ascii=False) and 'Эль переместился' in json.dumps(npc,ensure_ascii=False)
    with TestClient(create_app(store.path,MockLLM())) as client:
        exposed=json.dumps(client.get('/api/campaigns/'+cid).json(),ensure_ascii=False)
        assert 'Эль переместился' not in exposed and 'ЗАКРЫТАЯ_ПРИЧИНА' not in exposed and 'ПРИВАТНЫЙ_ПАРОЛЬ' not in exposed
    compiler=SceneCompiler(store);c=store.campaign(cid);_,prompt,_=compiler.narrator(c,event)
    assert 'Эль переместился' not in prompt and 'ПРИВАТНЫЙ_ПАРОЛЬ' not in prompt
    assert 'ЗАКРЫТАЯ_ПРИЧИНА' not in json.dumps(compiler.interpreter(c),ensure_ascii=False)
    assert natural.predicate('Эль ушла',state,[]) is None
    go(service,cid,'Берег реки','arrive')
    assert 'Эль' in json.dumps(client_state(store,cid),ensure_ascii=False)

def client_state(store,cid):
    from solo.combat_view import public_campaign
    return public_campaign(store.campaign(cid))

def test_scene_encounter_stops_later_edges_and_is_confirmed_with_prior_risk(tmp_path):
    store,cid,service=setup(tmp_path)
    install(service,cid,package(events=[dict(id='ambush',after_minutes=20,location='Старая башня',kind='encounter',lethal_risk=True,encounter=dict(environment='ruins',seed=819),text='Засада на дороге')]))
    event=go(service,cid,'Берег реки');s=store.campaign(cid)['state']
    assert s['location']=='Старая башня' and s['elapsed_minutes']==30 and not s.get('combat')
    assert event['results'][0]['travel']['type']=='travel_stop'
    pending=project(s)['pending_encounter'];assert pending['lethal_risk_announced'] and 'seed' not in pending['description']
    assert 'смертельный риск' in json.dumps(project(s)['chronicle'],ensure_ascii=False)
    with pytest.raises(ValueError):CombatService(store).start(cid,StartEncounter(request_id='battle',proposal='ambush'))
    battle=CombatService(store).start(cid,StartEncounter(request_id='battle',confirm=True,proposal='ambush'))
    s=store.campaign(cid)['state'];assert s['combat']['active'] and s['combat']['lethal_risk_declared'] and s['world']['pending_encounter'] is None
    store.undo(cid);assert store.campaign(cid)['state']['world']['pending_encounter']['id']=='ambush'
    assert Store(store.path).campaign(cid)['state']==store.campaign(cid)['state']

def test_timed_events_once_after_rest_or_wait_and_undo_reactivates(tmp_path):
    store,cid,service=setup(tmp_path)
    install(service,cid,package(events=[dict(id='weather',after_minutes=10,kind='notice',text='Начался дождь')]))
    before=copy.deepcopy(store.campaign(cid)['state'])
    def clock(state):state['elapsed_minutes']=20;return [dict(type='scripted',text='Проверка часов')]
    event=store.record_scenario(cid,'clock','Время',clock)
    assert store.campaign(cid)['state']['world']['triggered']==['weather']
    assert tick(copy.deepcopy(store.campaign(cid)['state']))==([],[])
    store.undo(cid);assert store.campaign(cid)['state']==before
    store.record_scenario(cid,'new-clock','Время',clock)
    assert len(store.campaign(cid)['state']['world']['chronicle'])==1

def test_blocked_route_and_disabled_map_never_teleport(tmp_path):
    store,cid,service=setup(tmp_path)
    def block(state):state['geography']=travel.geography(state);state['geography']['routes'][0].update(blocked=True,obstacle='Упал мост');return [dict(type='scripted')]
    store.record_scenario(cid,'block','Препятствие',block);before=store.campaign(cid)['state']
    e=go(service,cid,'Старая башня');s=store.campaign(cid)['state']
    assert s['location']==before['location'] and s['elapsed_minutes']==0 and e['results'][0]['travel']['meters']==0
    assert 'Упал мост' in e['results'][0]['text']
    service.execute(cid,WorldCommand(request_id='off',kind='settings',confirm=True,settings=dict(map_travel=False)))
    with pytest.raises(ValueError,match='отключён'):go(service,cid,'Старая башня','disabled')

def test_import_graph_references_no_overwrite_and_rollback(tmp_path):
    store,cid,service=setup(tmp_path);before=store.campaign(cid)
    for p in [package(nodes=[]),package(locations=[dict(name='Старая башня')]),package(routes=[dict(**{'from':'fake'},to='hidden',meters=1,minutes=1,description='x')]),package(nodes=[dict(id='other',name='Старая башня',coordinates=[1,1])])]:
        with pytest.raises(ValueError):install(service,cid,p)
        assert store.campaign(cid)==before
    with pytest.raises(ValueError):ScenarioPackage.model_validate({**package().model_dump(),'script':'eval(1)'})
    with pytest.raises(ValueError):service.import_world(cid,WorldImport(request_id='no',package=package()))
    original=store.memory.ingest
    store.memory.ingest=lambda *args:(_ for _ in ()).throw(RuntimeError('TX failure'))
    with pytest.raises(RuntimeError):install(service,cid)
    assert store.campaign(cid)==before
    store.memory.ingest=original;install(service,cid)

def test_stale_settings_and_markers_are_journaled_not_canonical_places(tmp_path):
    store,cid,service=setup(tmp_path);before=store.campaign(cid)
    service.execute(cid,WorldCommand(request_id='marker',kind='marker',coordinates=[1,2],text='Вернуться позже',expected_version=before['version']))
    assert store.campaign(cid)['state']['locations']==before['state']['locations']
    with pytest.raises(ValueError):service.execute(cid,WorldCommand(request_id='stale',kind='settings',confirm=True,settings=dict(mode='sandbox'),expected_version=before['version']))
    store.undo(cid);assert not project(store.campaign(cid)['state'])['markers']

def test_travel_pace_is_explicit_legacy_minutes_kept_and_2024_slow_rule(tmp_path):
    store,cid,service=setup(tmp_path);s=store.campaign(cid)['state'];s['ruleset']=BUILTINS['dnd2024'].model_dump()
    edge=dict(meters=9144,minutes=500,timing='srd_pace',pace='normal')
    assert travel.duration(edge,30,s)==100 and travel.duration(edge,15,s)==150
    assert travel.duration({**edge,'difficult':True},30,s)==200
    assert travel.duration({**edge,'timing':'scenario'},15,s)==500
    s['ruleset']=BUILTINS['dnd2014'].model_dump();assert travel.duration(edge,15,s)==100

def test_story_cannot_explore_hidden_route_and_oneway_blocks_return(tmp_path):
    store,cid,service=setup(tmp_path);p=package();p.routes[0].one_way=True;install(service,cid,p)
    service.execute(cid,WorldCommand(request_id='story',kind='settings',confirm=True,settings=dict(mode='story',map_travel=True)))
    with pytest.raises(ValueError):service.execute(cid,WorldCommand(request_id='explore',kind='explore',exit='0'),from_map=True)
    assert project(store.campaign(cid)['state'])['exits']==[]
    service.execute(cid,WorldCommand(request_id='sandbox',kind='settings',confirm=True,settings=dict(mode='sandbox',map_travel=True)))
    service.execute(cid,WorldCommand(request_id='go',kind='explore',exit='0'),from_map=True)
    with pytest.raises(ValueError):go(service,cid,'Таверна «Последний огонь»')

def test_reveal_remote_place_does_not_reveal_its_unobserved_contents(tmp_path):
    store,cid,service=setup(tmp_path);install(service,cid,package(events=[dict(id='reveal',after_minutes=1,kind='reveal',target='Тайный лес')]))
    go(service,cid,'Старая башня')
    view=json.dumps(client_state(store,cid),ensure_ascii=False)
    assert 'Тайный лес' in view and 'Зелёная поляна' not in view and 'ПРИВАТНЫЙ_ПАРОЛЬ' not in view

@pytest.mark.parametrize('conditions',[['grappled'],['unconscious'],['restrained']])
def test_incapacitated_travel_does_not_change_state(tmp_path,conditions):
    store,cid,service=setup(tmp_path)
    def status(state):state['hero']['conditions']=conditions;return [dict(type='scripted')]
    store.record_scenario(cid,'status','Состояние',status);before=store.campaign(cid)
    with pytest.raises(ValueError):go(service,cid,'Старая башня')
    assert store.campaign(cid)==before

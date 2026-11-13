import copy,json
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero,Plan,Step
from solo.llm import MockLLM
from solo.combat_service import CombatService,StartEncounter
from solo.geometry import rectangular_map,Position
from solo.play_screen import screen
from test_rulesets import Dice


def test_screen_probes_are_read_only_and_commands_are_versioned(tmp_path):
    app=create_app(tmp_path/'solo.sqlite3',MockLLM());store=app.state.store
    cid=store.create('UI',Hero(name='Мирон',weapon_proficiencies=['martial'],resources={'second_wind':dict(current=1,maximum=1)}))['id']
    CombatService(store,rng=Dice(20,1)).start(cid,StartEncounter(request_id='start',confirm=True,hero_weapon='longsword',imported_map=rectangular_map(5,5),hero_position=Position(x=1,y=1),enemy_position=Position(x=2,y=1)))
    before=copy.deepcopy(store.campaign(cid));history=store.history(cid)
    with TestClient(app) as client:
        url='/api/campaigns/'+cid+'/screen';packet=client.get(url).json()
        assert packet==client.get(url).json() and store.campaign(cid)==before and store.history(cid)==history
        assert packet['snapshot']['hero']['resources'][0]['current']==1
        assert any(c['parameters'].get('kind')=='attack' for c in packet['commands'])
        assert not any(c['parameters'].get('kind')=='npc_turn' for c in packet['commands'])
        command=next(c for c in packet['commands'] if c['parameters'].get('kind')=='end_turn')
        body={k:command[k] for k in ('kind','parameters','expected_version')};body['request_id']='end'
        applied=client.post(url+'/commands',json=body);assert applied.status_code==200,applied.text
        assert client.post(url+'/commands',json=body).json()==applied.json()
        stale=client.post(url+'/commands',json={**body,'request_id':'stale'})
        assert stale.status_code==409 and stale.json()['detail']['code']=='version_conflict'
        assert any(c['parameters'].get('kind')=='npc_turn' for c in client.get(url).json()['commands'])
        assert 'engine_snapshot' not in json.dumps(packet)


def test_screen_check_rest_restore_and_feedback(tmp_path):
    app=create_app(tmp_path/'solo.sqlite3',MockLLM());store=app.state.store;cid=store.create('UI',Hero(name='Мирон',hp=6,max_hp=12))['id']
    action=store.stage(cid,'check','Осматриваю дорогу',Plan(summary='Проверка',steps=[Step(kind='check',text='Проверка',ability='wisdom',dc=10)]),0,{})
    store.apply(action['id'],rng=Dice(15));checkpoint=store.save(cid,'До отдыха')
    with TestClient(app) as client:
        url='/api/campaigns/'+cid+'/screen';packet=client.get(url).json();assert packet['snapshot']['last_roll']['success']
        body=dict(request_id='rest',kind='rest',expected_version=packet['snapshot']['version'],parameters=dict(kind='long'))
        assert client.post(url+'/commands',json=body).status_code==200
        assert client.get(url).json()['snapshot']['hero']['hp']==12
        version=client.get(url).json()['snapshot']['version']
        assert client.post(url+'/commands',json=dict(request_id='restore',kind='restore',expected_version=version,parameters=dict(checkpoint=checkpoint))).status_code==200
        assert client.get(url).json()['snapshot']['hero']['hp']==6
        store.converse(cid,'clarify','Пробую','Что именно вы хотите сделать?','clarification')
        assert client.get(url).json()['feedback']['kind']=='clarification'
        store.converse(cid,'unsupported','Телепортируюсь','Эта механика не поддерживается.','notice')
        assert client.get(url).json()['feedback']['kind']=='unsupported'


def test_shared_fixture_matches_screen_envelope(tmp_path):
    fixture=json.loads((Path(__file__).parent/'fixtures/game_screen.json').read_text(encoding='utf-8'))
    app=create_app(tmp_path/'solo.sqlite3',MockLLM());cid=app.state.store.create('Fixture',Hero(name='Мирон'))['id'];actual=screen(app.state.store,cid)
    assert fixture.keys()==actual.keys()
    assert fixture['snapshot'].keys()==actual['snapshot'].keys()
    assert fixture['snapshot']['hero'].keys()==actual['snapshot']['hero'].keys()


def test_screen_uses_engine_commands_and_projects_engine_rolls(tmp_path):
    from scripts.command_smoke import setup
    from solo.game import Game
    store,cid,game=setup(tmp_path)
    app=create_app(tmp_path/'commands.sqlite3',game.llm)
    before=copy.deepcopy(store.campaign(cid))
    with TestClient(app) as client:
        url='/api/campaigns/'+cid+'/screen'
        packet=client.get(url).json()
        assert packet['snapshot']['command_turn']
        assert store.campaign(cid)==before and store.history(cid)==[]
        command=next(c for c in packet['commands'] if c['parameters']['kind']=='check')
        body={k:command[k] for k in ('kind','parameters','expected_version')};body['request_id']='screen-check'
        applied=client.post(url+'/commands',json=body)
        assert applied.status_code==200,applied.text
        assert client.post(url+'/commands',json=body).json()==applied.json()
        packet=client.get(url).json()
        assert packet['snapshot']['last_roll']['label']=='Проверка'
        assert packet['events'][-1]['roll']==packet['snapshot']['last_roll']
        assert store.campaign(cid)['state']['engine_snapshot']['event_id']=='command:screen-check'

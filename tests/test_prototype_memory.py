"""prototype-3: synthetic heterogeneous DTO fixtures + a short real Nat20 smoke."""
import os
from pathlib import Path

STAND = Path(__file__).resolve().parents[1] / 'reports' / 'prototype-3'
os.environ.setdefault('SOLO_TEST', '1')
os.environ.setdefault('SOLO_DATA_DIR', str(STAND / 'profile'))

import copy
import json
import subprocess
import sys
import socket
import pytest
from solo.contracts import Hero
from solo.engine_memory import EngineMemoryAdapter
from solo.inventory import InventoryEngine, Item
from solo.storage import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw: pytest.fail('No network in prototype-3 memory smoke'))


def fixture_story(path):
    """Explicit contract fixture: no D&D outcomes are calculated in this fixture."""
    store = Store(path)
    state = dict(location='Обсерватория', turn=0, facts=[], deferred=[], locations=[
        dict(name='Обсерватория', description='Купол обсерватории.', objects=['телескоп'], facts=[],
             npc=[dict(id='astronomer-7', name='Эша', known='Эша наблюдает звёзды.')]),
        dict(name='Кузница', description='Тихая кузница за рекой.', objects=[], facts=[],
             npc=[dict(id='smith-2', name='Даган')])])
    cid = store.create('Разные субъекты — fixture', Hero(name='Мэй', class_name='wizard'), initial_state=state)['id']
    def seed(target):
        target['world'] = dict(events=[dict(id='esha-departs', after_minutes=1, kind='npc_move', npc='Эша', target='Кузница')])
        return [dict(type='scripted', memory_evidence=[dict(text='Эша хранит личный код АЛЬТАИР.',
            provenance='scenario', truth='fact', scope='private', known_by=['astronomer-7'],
            subjects=['astronomer-7'], source_ref='fixture:astronomer/private')])]
    store.record_scenario(cid, 'fixture-seed', 'Fixture setup', seed)
    access = dict(scope='public', known_by=['hero', 'companion-8', 'warden-4'])
    actors = []
    for identity, name in [('hero','Мэй'), ('companion-8','Соль'), ('warden-4','Каменный страж')]:
        own = dict(scope='private', known_by=[identity])
        actors.append(dict(id=identity, kind='hero' if identity=='hero' else 'npc', name=name, **access,
            hp=dict(current=10, maximum=10, **access), resources={'focus':dict(current=2, **own)},
            position=dict(coordinates=[0,0,0], **access), conditions=[]))
    snapshot = dict(campaign_id=cid, event_id='fixture-open', branch_id='main', sequence=1,
        location='Обсерватория', actors=actors, effects=[],
        combat=dict(id='fixture-meeting', active=True, round=1, turn='hero', **access))
    event = dict(id='fixture-open', campaign_id=cid, branch_id='main', sequence=1, parent_event_id=None,
        type='combat_started', payload=dict(combat_id='fixture-meeting', participants=[a['id'] for a in actors]),
        subjects=[a['id'] for a in actors], source_ref='fixture:meeting/start', game_minutes=0, **access)
    return store, cid, event, snapshot


def advance(event, snapshot, identity, kind, payload, **access):
    event, snapshot = copy.deepcopy(event), copy.deepcopy(snapshot)
    event.update(id=identity, parent_event_id=snapshot['event_id'], sequence=snapshot['sequence']+1,
                 type=kind, payload=payload, **access)
    snapshot.update(event_id=identity, sequence=event['sequence'])
    return event, snapshot


def context_in_new_process(store, cid):
    code = '''
import os,json,sys
os.environ['SOLO_TEST']='1'
os.environ['SOLO_DATA_DIR']=sys.argv[3]
from solo.storage import Store
from solo.engine_memory import EngineMemoryAdapter
adapter=EngineMemoryAdapter(Store(sys.argv[1]))
print(json.dumps(adapter.context(sys.argv[2],npc_id='astronomer-7',limit=10,budget=16000)))
'''
    process = subprocess.run([sys.executable, '-B', '-c', code, str(store.path), cid, str(STAND/'child-profile')],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=True, timeout=30)
    return json.loads(process.stdout)


def test_generic_fixture_inventory_world_acl_optional_events_restart_and_fork(tmp_path):
    store, cid, event, snapshot = fixture_story(tmp_path/'heterogeneous.sqlite3')
    adapter = EngineMemoryAdapter(store)
    hidden=copy.deepcopy(snapshot['actors'][2]);hidden.update(id='unseen-12',name='Скрытый субъект',scope='secret',known_by=['unseen-12'])
    snapshot['actors'].append(hidden);event['subjects'].append('unseen-12');event['payload']['participants'].append('unseen-12')
    opened = adapter.deliver(event, snapshot)
    assert 'unseen-12' not in json.dumps(adapter.context(cid,limit=10,budget=16000))
    event, snapshot = advance(event, snapshot, 'fixture-focus', 'resource',
        dict(target='companion-8', resource='focus', before=2, after=1), scope='public', known_by=['hero','companion-8'])
    snapshot['actors'][1]['resources']['focus']['current'] = 1
    source = adapter.deliver(event, snapshot)
    assert adapter.deliver(event, snapshot)['id'] == source['id']
    assert any(r['source_event']=='fixture-focus' for r in adapter.context(cid,npc_id='companion-8')['memories'])
    assert all(r.get('source_event')!='fixture-focus' for r in adapter.context(cid)['memories'])
    event,snapshot=advance(event,snapshot,'fixture-effect','effect',dict(target='companion-8',
        effect_id='ward-1',origin='fixture:ward',active=True,concentration=False),scope='private',known_by=['hero'])
    snapshot['effects']=[dict(id='ward-1',origin='fixture:ward',target='companion-8',caster='hero',name='Fixture ward',
        concentration=False,duration_limit={},scope='private',known_by=['hero']),
        dict(id='hidden-ward',origin='fixture:hidden',target='unseen-12',caster='hero',name='UNSEEN_WARD',
        concentration=False,duration_limit={},scope='private',known_by=['hero'])]
    adapter.deliver(event,snapshot)
    visible=adapter.context(cid,limit=10,budget=16000)
    assert any(r['kind']=='effect' for r in visible['memories'])
    assert 'unseen-12' not in json.dumps(visible) and 'UNSEEN_WARD' not in json.dumps(visible)
    def item(target):
        inventory = InventoryEngine(target)
        inventory.data.items.append(Item(id='notes-9', name='Свиток наблюдений', owner='npc:astronomer-7', source='fixture:notes'))
        inventory.flush()
        return [dict(type='scripted')]
    store.record_scenario(cid,'fixture-item','Confirmed inventory fixture',item)
    def transfer(target):
        inventory=InventoryEngine(target)
        inventory.transfer('notes-9',1,'hero',owner='npc:astronomer-7',source='fixture:confirmed-transfer')
        inventory.flush();target['elapsed_minutes']=1
        return [dict(type='scripted')]
    transferred = store.record_scenario(cid,'fixture-transfer','Confirmed transfer',transfer)
    assert store.record_scenario(cid,'fixture-transfer','Confirmed transfer',transfer)['id']==transferred['id']
    hero = adapter.context(cid,limit=10,budget=16000)
    astronomer = adapter.context(cid,npc_id='astronomer-7',limit=10,budget=16000)
    smith = adapter.context(cid,npc_id='smith-2',limit=10,budget=16000)
    assert hero['inventory'][0]['name']=='Свиток наблюдений'
    assert 'АЛЬТАИР' not in json.dumps(hero,ensure_ascii=False)
    assert 'АЛЬТАИР' in json.dumps(astronomer,ensure_ascii=False)
    assert 'АЛЬТАИР' not in json.dumps(smith,ensure_ascii=False)
    assert astronomer['scene']['location']=='Кузница' and hero['scene']['location']=='Обсерватория'
    assert astronomer['npc_profile']['id']=='astronomer-7' and 'звёзды' in astronomer['npc_profile']['known']
    assert any(r['source_ref']=='state.world.events:esha-departs' for r in astronomer['memories'])
    assert all('Эша переместился: Кузница' not in r['content'] for r in hero['memories'])
    with store.connection() as db:
        count=db.execute('SELECT COUNT(*) FROM memory_records WHERE campaign=?',(cid,)).fetchone()[0]
    store.narrative(transferred['id'],'Эша узнала все чужие секреты. У героя миллион золота.','complete')
    with store.connection(True) as db:
        store.memory.ingest(db,transferred['id'])
        assert db.execute('SELECT COUNT(*) FROM memory_records WHERE campaign=?',(cid,)).fetchone()[0]==count
    assert adapter.context(cid,limit=10,budget=16000)==hero
    assert adapter.context(cid,npc_id='astronomer-7',limit=10,budget=16000)==astronomer
    checkpoint=store.save(cid,'prototype-3 fixture')
    assert context_in_new_process(store,cid)==astronomer
    event,snapshot=advance(event,snapshot,'fixture-optional','resource',
        dict(target='hero',resource='focus',before=2,after=1),scope='private',known_by=['hero'])
    snapshot['actors'][0]['resources']['focus']['current']=1
    def raw_fixture(target):
        target['engine_snapshot']=snapshot
        return [dict(type='scripted',game_event=event,game_snapshot=snapshot,native_events=[
            dict(type='future_optional_notice',required=False,secret='DO_NOT_SERIALIZE'),
            dict(type='temphp_applied',target_id='hero',amount=3)])]
    unknown=store.record_scenario(cid,'fixture-optional','Explicit unknown-native fixture',raw_fixture)
    issues=adapter.context(cid)['projection_issues']
    assert {(r['event_type'],r['required']) for r in issues}=={('future_optional_notice',False),('temphp_applied',True)}
    assert 'DO_NOT_SERIALIZE' not in json.dumps(adapter.context(cid))
    assert 'projection_issues' not in adapter.context(cid,npc_id='astronomer-7')
    with store.connection() as db:
        assert 'DO_NOT_SERIALIZE' in db.execute('SELECT results FROM events WHERE id=?',(unknown['id'],)).fetchone()[0]
        assert not db.execute("SELECT 1 FROM memory_records WHERE kind='future_optional_notice'").fetchone()
    store.undo(cid)
    assert 'projection_issues' not in adapter.context(cid)
    event['id']=snapshot['event_id']='fixture-fork'
    event['branch_id']=snapshot['branch_id']='alternative'
    adapter.deliver(event,snapshot)
    assert adapter.context(cid)['branch_id']=='alternative'
    store.restore(cid,checkpoint)
    assert context_in_new_process(store,cid)==astronomer
    assert adapter.context(cid,as_of=opened['seq'],current_snapshot=snapshot)['source_event']=='fixture-open'
    assert not store.memory.audit()['foreign_key_errors']


def test_real_native_healing_resource_and_restart(tmp_path):
    from solo.nat20_api import Nat20GameEngine
    from solo.game_commands import GameCommand
    store=Store(tmp_path/'native.sqlite3');api=Nat20GameEngine(store)
    cid=api.create_campaign('Мирена — реальный native',seed=5,hp_current=7)['id']
    def run(identity,**action):
        return api.execute(cid,GameCommand(request_id=identity,expected_version=store.campaign(cid)['version'],action=action))
    run('prototype3-start',kind='start_combat',encounter_id='basic')
    used=run('prototype3-wind',kind='use_feature',feature_id='second-wind')
    assert any(e['type']=='healing_applied' for e in used['native_events'])
    facts=EngineMemoryAdapter(store).context(cid,limit=10,budget=16000)['memories']
    assert {'healing','resource'} <= {r['kind'] for r in facts}
    assert all(r['source_ref'].startswith('Nat20 0.7.0') and r['truth']=='fact' for r in facts)
    runtime=copy.deepcopy(store.campaign(cid)['state']['nat20'])
    assert Nat20GameEngine(Store(store.path)).restore(cid)['runtime']==runtime
    original=GameCommand.model_validate(used['journal']['results'][0]['game_command'])
    assert api.execute(cid,original)['journal']['id']==used['journal']['id']
    store.undo(cid)
    alternate=run('prototype3-alternative-wind',kind='use_feature',feature_id='second-wind')
    assert alternate['event'].branch_id!=used['event'].branch_id
    assert all(r.get('source_event')!='nat20:prototype3-wind' for r in EngineMemoryAdapter(store).context(cid)['memories'])


def test_real_experimental_wizard_multiple_native_targets_restart_continue(tmp_path):
    from solo.nat20_api import Nat20GameEngine
    from solo.game_commands import GameCommand
    store=Store(tmp_path/'wizard.sqlite3');api=Nat20GameEngine(store)
    build=legal(species_slug='human',class_slug='wizard',level=1,
        equipment=['dagger','potion-of-healing'])
    encounter=dict(grid=dict(width=9,height=6),hero_position=dict(x=1,y=1,z=0),actors=[
        dict(id='rat-a',monster='rat',position=dict(x=2,y=1,z=0)),
        dict(id='rat-b',monster='rat',position=dict(x=5,y=1,z=0))])
    cid=api.create_experimental_campaign('Тэя',build=build,prepared_spells=['magic-missile'],seed=5,
        title='Архив',description='Проверка разнообразного native мира.',encounters={'archive':encounter})['id']
    def run(identity,**action):
        return api.execute(cid,GameCommand(request_id=identity,expected_version=store.campaign(cid)['version'],action=action))
    run('prototype3-archive',kind='start_combat',encounter_id='archive')
    cast=run('prototype3-missiles',kind='native_action',operation='cast_spell',spell_id='magic-missile',
             target_ids=['rat-a','rat-b','rat-b'],slot_level=1)
    assert len([e for e in cast['native_events'] if e['type']=='damage_applied'])==3
    assert any(e['type']=='spell_cast' for e in cast['native_events'])
    adapter=EngineMemoryAdapter(store);before=adapter.context(cid,limit=10,budget=16000)
    assert len(before['mechanics']['actors'])==3
    assert next(a for a in before['mechanics']['actors'] if a['id']=='hero')['name']=='Тэя'
    assert {'damage','resource'} <= {r['kind'] for r in before['memories']}
    assert all(not a['resources'] for a in before['mechanics']['actors'] if a['id']!='hero')
    for target in ('rat-a','rat-b'):
        assert any(target in r['content'] and r['kind']=='damage' for r in before['memories'])
    runtime=copy.deepcopy(store.campaign(cid)['state']['nat20'])
    checkpoint=store.save(cid,'prototype-3 real wizard')
    program='''
import json,os,sys
os.environ['SOLO_TEST']='1';os.environ['SOLO_DATA_DIR']=sys.argv[3]
from solo.storage import Store
from solo.engine_memory import EngineMemoryAdapter
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
store=Store(sys.argv[1]);api=Nat20GameEngine(store);cid=sys.argv[2]
restored=api.restore(cid)['runtime'];before=EngineMemoryAdapter(store).context(cid,limit=10,budget=16000)
api.execute(cid,GameCommand(request_id='prototype3-continued',expected_version=store.campaign(cid)['version'],action={'kind':'end_turn'}))
print(json.dumps({'runtime':restored,'before':before,'after':EngineMemoryAdapter(store).context(cid,limit=10,budget=16000)}))
'''
    process=subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(store.path),cid,str(STAND/'wizard-child')],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',check=True,timeout=60)
    resumed=json.loads(process.stdout)
    assert resumed['runtime']==runtime and resumed['before']==before
    assert resumed['after']['source_event']=='nat20:prototype3-continued'
    store.restore(cid,checkpoint)
    assert adapter.context(cid,limit=10,budget=16000)==before
    assert api.execute(cid,GameCommand.model_validate(cast['journal']['results'][0]['game_command']))['journal']['id']==cast['journal']['id']

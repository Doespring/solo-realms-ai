"""Stage3 routing only: real pinned Native, host journal, disposable profiles."""
from copy import deepcopy
import json,os,random,socket,subprocess,sys
import pytest
from solo.command_engine import CommandEngine,StateVersionError
from solo.command_backend import command_backend
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store
from test_nat20_prototype import build


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('No network in common routing')
    monkeypatch.setattr(socket.socket,'connect',forbidden)


def command(store,cid,key,**action):
    return GameCommand(request_id=key,expected_version=store.campaign(cid)['version'],action=action)


def database_bytes(store):
    with store.connection() as db:return '\n'.join(db.iterdump())


def native_only(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Native used legacy mechanics/RNG')
    monkeypatch.setattr('solo.command_engine.RulesetEngine.check',forbidden)
    monkeypatch.setattr('solo.command_engine.RulesetEngine.rest',forbidden)
    monkeypatch.setattr('solo.command_engine.CombatService.start_transition',forbidden)
    monkeypatch.setattr('solo.command_engine.CombatService.execute_transition',forbidden)


@pytest.mark.parametrize('cls',['wizard','monk'])
def test_pure_native_resolve_and_common_execute_roll_rest_battle_feature_spell(tmp_path,monkeypatch,cls):
    native_only(monkeypatch)
    store=Store(tmp_path/'game.sqlite3');api=Nat20GameEngine(store)
    # The monk case needs a legal level-2 character: raising level alone would
    # contradict the classes map and CharacterBuildSpec rightly rejects it,
    # so the level goes through the builder that keeps both in step.
    chosen=build(cls,level=2 if cls=='monk' else 1)
    assert chosen['level']==chosen['classes'][cls]
    cid=api.create_experimental_campaign(cls,build=chosen,prepared_spells=['magic-missile'] if cls=='wizard' else [],
        encounters={'rat':dict(grid=dict(width=8,height=6),hero_position=dict(x=1,y=1,z=0),
            actors=[dict(id='rat',monster='rat',position=dict(x=3,y=1,z=0))])},seed=5)['id']
    engine=CommandEngine(store);probe_engine=CommandEngine(None)
    assert isinstance(command_backend(store,store.campaign(cid)),Nat20GameEngine)
    from solo.play_screen import commands_for
    before=database_bytes(store);rng=random.getstate();current=store.campaign(cid);original=deepcopy(current)
    assert commands_for(current,None)
    assert current==original and database_bytes(store)==before and random.getstate()==rng
    def run(key,**action):
        current=store.campaign(cid);original=deepcopy(current);db=database_bytes(store);rng=random.getstate()
        journal_count=len(store.history(cid))
        body=command(store,cid,key,**action)
        preview=probe_engine.resolve(current,body)
        assert isinstance(preview,list) and len(preview)==1 and preview[0]['kind']=='nat20_command'
        assert current==original and database_bytes(store)==db and random.getstate()==rng
        executed=engine.execute(cid,body)
        assert executed['result']==preview[0]['native_result']
        assert executed['native_events']==preview[0]['native_events']
        assert store.campaign(cid)['version']==current['version']+1
        assert len(store.history(cid))==journal_count+1
        with store.connection() as conn:
            assert conn.execute('SELECT count(*) FROM memory_engine_events WHERE campaign=?',(cid,)).fetchone()[0]==len(store.history(cid))
        row=executed['journal']['results'][0]
        assert row['game_event']['id']==row['game_snapshot']['event_id']==executed['event'].id
        assert row['game_snapshot']==executed['snapshot'].model_dump(mode='json')
        return executed
    run('roll',kind='check',check_id='save-dex')
    run('short',kind='short_rest',hit_dice=0)
    run('long',kind='long_rest',food_water=True)
    run('battle',kind='start_combat',encounter_id='rat')
    view=store.campaign(cid)['state']['nat20']['combat']['view']
    if view['initiative'][view['current_turn_index']]['entity_id']!='hero':run('turn',kind='end_turn')
    if cls=='wizard':
        used=run('spell',kind='native_action',operation='cast_spell',spell_id='magic-missile',target_id='rat',slot_level=1)
        assert any(e['type']=='spell_cast' for e in used['native_events'])
        run('close',kind='end_combat')
    else:
        run('feature',kind='native_action',operation='use_feature',feature_id='monks-focus',activity_id='7xj7b6e8tDznDSrE')
        assert store.campaign(cid)['state']['nat20']['hero']['custom_counters']['feature_use:monks-focus']['spent']==1
        run('next-turn',kind='end_turn')
    assert store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])


def test_common_execute_duplicate_stale_atomic_undo_fork_and_replay(tmp_path,monkeypatch):
    store=Store(tmp_path/'routing.sqlite3');api=Nat20GameEngine(store);engine=CommandEngine(store)
    cid=api.create_experimental_campaign('Rogue',build=build('rogue'),encounters={},seed=5)['id']
    first=command(store,cid,'first',kind='check',check_id='save-dex')
    resolved=engine.execute(cid,first,original='Проверка');after=deepcopy(store.campaign(cid));db=database_bytes(store)
    assert engine.execute(cid,first)['journal']['id']==resolved['journal']['id']
    assert database_bytes(store)==db
    with pytest.raises(StateVersionError):engine.execute(cid,first.model_copy(update={'request_id':'stale'}))
    assert store.campaign(cid)==after
    def fail(*args,**kwargs):raise RuntimeError('routing memory fault')
    next_cmd=command(store,cid,'next',kind='check',check_id='save-wis')
    with monkeypatch.context() as fault:
        fault.setattr(store.memory,'ingest',fail)
        with pytest.raises(RuntimeError,match='routing memory fault'):engine.execute(cid,next_cmd)
    assert store.campaign(cid)==after and database_bytes(store)==db
    engine.execute(cid,next_cmd)
    store.undo(cid)
    with pytest.raises(ValueError):engine.execute(cid,next_cmd)
    fork=engine.execute(cid,command(store,cid,'fork',kind='check',check_id='save-wis'))
    assert fork['snapshot'].branch_id!=resolved['snapshot'].branch_id
    assert api.restore(cid)['runtime']==store.campaign(cid)['state']['nat20']
    store.save(cid,'After fork');version=store.campaign(cid)['version']
    program='''
import json,sys
from solo.command_engine import CommandEngine
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store
s=Store(sys.argv[1]);cid=sys.argv[2];before=Nat20GameEngine(s).restore(cid)
r=CommandEngine(s).execute(cid,dict(request_id='child',expected_version=before['version'],action=dict(kind='check',check_id='save-dex')))
print(json.dumps(dict(version=s.campaign(cid)['version'],event=r['event'].id,audit=s.memory.audit())))
'''
    child=subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(store.path),cid],
        capture_output=True,text=True,encoding='utf-8',check=True,timeout=45,
        env=dict(os.environ,SOLO_TEST='1',PYTHONDONTWRITEBYTECODE='1'))
    assert json.loads(child.stdout)==dict(version=version+1,event='nat20:child',audit=dict(dangling_events=[],foreign_key_errors=[]))


def test_corrupt_native_presence_never_falls_back_and_probe_stale_refuses(tmp_path,monkeypatch):
    native_only(monkeypatch)
    store=Store(tmp_path/'corrupt.sqlite3');api=Nat20GameEngine(store);engine=CommandEngine(store)
    cid=api.create_experimental_campaign('Rogue',build=build('rogue'),encounters={},seed=5)['id']
    current=store.campaign(cid);body=command(store,cid,'probe',kind='check',check_id='save-dex')
    with pytest.raises(StateVersionError):engine.resolve(current,body.model_copy(update={'expected_version':current['version']+1}))
    corrupted=deepcopy(current);corrupted['state']['nat20']=None
    with pytest.raises(ValueError):engine.resolve(corrupted,body)
    with store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?',(json.dumps(corrupted['state']),cid))
    before=database_bytes(store)
    with pytest.raises(ValueError):engine.execute(cid,body)
    assert database_bytes(store)==before

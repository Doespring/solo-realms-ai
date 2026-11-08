"""Release seam regression: real Nat20, isolated SQLite, no cloud requests."""
import copy
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_screen import Nat20Screen
from solo.preview_mock import PreviewMock
from solo.storage import Store


def command(store, cid, key, **action):
    return GameCommand(request_id=key, expected_version=store.campaign(cid)['version'], action=action)


def test_lantern_launch_dialogue_check_reward_restore(tmp_path, monkeypatch):
    monkeypatch.setattr('solo.nat20_api.secrets.randbits', lambda bits: 5)
    app=create_app(tmp_path/'scene.sqlite3', PreviewMock());store=app.state.store
    with TestClient(app) as client:
        cid=client.post('/api/nat20/campaigns',json=dict(name='Мирон',scene='lantern-post')).json()['id']
        url='/api/campaigns/'+cid
        state=store.campaign(cid)['state']
        assert sorted(state['hero']['abilities'].values())==[8,10,12,13,14,15]
        packet=client.get(url+'/screen').json()
        assert packet['snapshot']['scene']['stage']=='arrival'
        assert not any(c['parameters']['kind']=='start_combat' for c in packet['commands'])
        version=store.campaign(cid)['version']
        before_talk=copy.deepcopy(store.campaign(cid)['state'])
        talk=client.post(url+'/command-turn',json=dict(request_id='talk',expected_version=version,text='Поговорю с Ладой')).json()
        assert talk['status']=='conversation' and 'следы' in talk['narrative']
        assert 'сестры' not in str(talk)
        assert talk['state_version']==store.campaign(cid)['version']==version+1
        after_talk=copy.deepcopy(store.campaign(cid))
        for field in ('hero','nat20','economy','facts'):
            assert after_talk['state'][field]==before_talk[field]
        repeated=client.post(url+'/command-turn',json=dict(request_id='talk',expected_version=version,text='Поговорю с Ладой')).json()
        assert repeated['narrative']==talk['narrative'] and repeated['state_version']==version+1
        assert store.campaign(cid)==after_talk
        api=Nat20GameEngine(store)
        def run(key,**action):return api.execute(cid,command(store,cid,key,**action))
        run('check',kind='check',check_id='perception')
        run('start',kind='start_combat',encounter_id='basic')
        run('move',kind='move',position=dict(x=2,y=1,z=0))
        run('wind',kind='use_feature',feature_id='second-wind')
        saved=store.save(cid,'Середина боя');before=copy.deepcopy(store.campaign(cid)['state'])
        for i in range(25):
            native=store.campaign(cid)['state']['nat20'];view=native['combat']['view']
            if view['tracked_hp']['road-raider']<=0:break
            kind='attack' if view['initiative'][view['current_turn_index']]['entity_id']=='hero' else 'end_turn'
            run('turn-'+str(i),kind=kind,**(dict(target='road-raider') if kind=='attack' else {}))
        else:pytest.fail('Battle did not conclude')
        ended=command(store,cid,'end',kind='end_combat');first=api.execute(cid,ended)
        assert api.execute(cid,ended)['journal']['id']==first['journal']['id']
        finished=store.campaign(cid)['state']
        assert finished['lantern_scene']['letter_received'] and finished['nat20']['experience']==50
        letters=[i for i in finished['economy']['items'] if i['name']=='Благодарственное письмо Лады' and i['owner']=='hero' and i['quantity']>0]
        assert len(letters)==1 and letters[0]['quantity']==1
        with pytest.raises(ValueError):run('repeat-reward',kind='start_combat',encounter_id='basic')
        run('rest',kind='long_rest',food_water=False)
        store.restore(cid,saved)
        assert store.campaign(cid)['state']['nat20']==before['nat20']
        assert not store.campaign(cid)['state']['lantern_scene']['letter_received']
        assert Nat20GameEngine(Store(store.path)).restore(cid)['runtime']==before['nat20']


def test_zero_hp_advances_native_death_saves_and_keeps_checkpoint(tmp_path):
    from solo.command_engine import CommandEngine
    store=Store(tmp_path/'defeat.sqlite3');api=Nat20GameEngine(store)
    cid=api.create_campaign('Поражение',seed=5,hp_current=1)['id']
    saved=store.save(cid,'До поражения')
    engine=CommandEngine(store)
    def run(key,**action):return engine.execute(cid,command(store,cid,key,**action))
    run('start',kind='start_combat',encounter_id='basic')
    for i in range(20):
        if store.campaign(cid)['state']['hero']['hp']<=0:break
        run('wait-'+str(i),kind='end_turn')
    else:pytest.fail('Defeat fixture never reached zero HP')
    before=copy.deepcopy(store.campaign(cid))
    packet=Nat20Screen().project(before)
    assert packet['terminal'] is None
    assert {c['parameters']['kind'] for c in packet['commands']}=={'end_turn'}
    for action in (dict(kind='attack',target='goblin'),dict(kind='use_feature',feature_id='second-wind')):
        with pytest.raises(ValueError,match='0 HP'):run('blocked-'+action['kind'],**action)
    assert store.campaign(cid)==before
    assert api.restore(cid)['runtime']==before['state']['nat20']
    death_save=run('death-save',kind='end_turn')
    assert any(event['type']=='death_save_rolled' for event in death_save['native_events'])
    for i in range(12):
        if 'hero' in store.campaign(cid)['state']['nat20']['combat']['view']['dead_ids']:break
        run('dying-'+str(i),kind='end_turn')
    else:pytest.fail('Native death sequence did not conclude')
    closing=Nat20Screen().project(store.campaign(cid))
    assert {c['parameters']['kind'] for c in closing['commands']}=={'end_combat'}
    finished=run('end-defeat',kind='end_combat')
    assert finished['result']['outcome']['ended_reason']=='defeat_tpk'
    assert store.campaign(cid)['state']['nat20']['experience']==0
    assert store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    store.restore(cid,saved)
    assert store.campaign(cid)['state']['hero']['hp']==1

"""Public PDF/world/Nat20 HTTP bridge on isolated synthetic fixtures only."""
import os
os.environ['SOLO_TEST']='1'
import json
from copy import deepcopy
import socket

import pytest
from fastapi.testclient import TestClient
from test_pdf_nat20_start import prepare
from test_nat20_prototype import build

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    original=socket.socket.connect
    def local(sock,address):
        if address[0] not in ('127.0.0.1','::1','localhost'):raise AssertionError('External AI forbidden')
        return original(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local)

def confirmation(client,did,readiness):
    response=client.post('/api/creation/drafts/'+did+'/confirm-world',json=dict(
        expected_revision=readiness['revision'],run_id=readiness['run_id'],confirm=True))
    assert response.status_code==200,response.text
    return response.json()

def new_game(did,revision,**changes):
    return dict(name='Рив',request_id='world-start',build=build('rogue'),
        adventure='prepared:'+did,adventure_revision=revision,encounter_monsters=[],
        mechanics_ruleset='dnd2024',rules_confirmed=True,hero_confirmed=True,
        background_confirmed=True,hero_background='Ищет безопасную дорогу.',**changes)

def test_native_catalog_detail_http_exposes_selected_profile_and_rejects_unknown(tmp_path):
    from solo.app import create_app
    from solo.preview_mock import PreviewMock
    with TestClient(create_app(tmp_path/'game.sqlite3',PreviewMock(),experimental=True,offline_mock=True)) as client:
        response=client.get('/api/prototype/catalog/monsters/wolf')
        assert response.status_code==200,response.text
        detail=response.json();profile=detail['attack_profile']
        assert detail['slug']=='wolf' and 'known_issue' in detail
        assert profile['status']=='native_derived' and profile['attack_bonus']==4
        assert len(profile['monster_sha256'])==64
        for path in ('monsters/not-a-real-monster','unsupported/wolf'):
            rejected=client.get('/api/prototype/catalog/'+path)
            assert rejected.status_code==409 and rejected.json()['detail'],rejected.text
        assert client.get('/api/campaigns').json()==[]


def test_local_world_readiness_and_confirmation_are_safe_explicit_and_idempotent(tmp_path):
    app,c,did,run_id,_,requests=prepare(tmp_path)
    try:
        url='/api/creation/drafts/'+did;before=app.state.preparation.get(did);calls=len(requests)
        ready=c.get(url+'/world-readiness').json()
        assert ready['world_ready'] and not ready['ready'] and ready['source_ruleset']=='dnd2014'
        assert all(a['id']!='prepared:'+did for a in c.get('/api/prototype/options').json()['adventures'])
        for packet in [dict(expected_revision=ready['revision'],run_id=run_id,confirm=False),
                       dict(expected_revision=ready['revision']-1,run_id=run_id,confirm=True)]:
            assert c.post(url+'/confirm-world',json=packet).status_code==409
            assert app.state.preparation.get(did)==before
        no_consent=c.post(url+'/prepare',json=dict(request_id='forbidden',ruleset='dnd2014',world_structure=True,consent_to_send=False))
        assert no_consent.status_code==409 and len(requests)==calls
        confirmed=confirmation(c,did,ready)
        assert confirmed['revision']>before['revision'] and confirmed['world_readiness']['ready']
        assert 'body' not in confirmed and 'GM_SECRET' not in json.dumps([ready,confirmed]) and 'quote' not in json.dumps(confirmed)
        assert confirmation(c,did,ready)['revision']==confirmed['revision']
        private=app.state.preparation.get(did)['body']
        assert private['ruleset']=='dnd2014' and private['world_confirmation']['run_id']==run_id
        assert not private['background_confirmed'] and not private['hero']
        row=next(a for a in c.get('/api/prototype/options').json()['adventures'] if a['id']=='prepared:'+did)
        assert row['scope']=='world' and row['mechanics_ruleset']=='dnd2024' and 'hero_name' not in row
        assert len(requests)==calls
    finally:c.__exit__(None,None,None)

def test_confirmed_2014_world_http_starts_explicit_2024_hero_without_battle_and_replays(tmp_path,monkeypatch):
    app,c,did,run_id,_,requests=prepare(tmp_path)
    try:
        ready=c.get('/api/creation/drafts/'+did+'/world-readiness').json()
        confirmed=confirmation(c,did,ready);before=deepcopy(app.state.preparation.get(did));calls=len(requests)
        packet=new_game(did,confirmed['revision'])
        for field in ('rules_confirmed','hero_confirmed','background_confirmed'):
            denied=c.post('/api/prototype/campaigns',json={**packet,field:False})
            assert denied.status_code==409,denied.text
            assert app.state.preparation.get(did)==before
        assert c.post('/api/prototype/campaigns',json={**packet,'adventure_revision':confirmed['revision']-1}).status_code==409
        assert app.state.preparation.get(did)==before
        def no_legacy(*args,**kwargs):raise AssertionError('No legacy mechanics fallback')
        monkeypatch.setattr('solo.command_engine.CommandEngine.resolve',no_legacy)
        created=c.post('/api/prototype/campaigns',json=packet);assert created.status_code==200,created.text
        cid=created.json()['id'];current=app.state.store.campaign(cid);state=current['state'];saved=app.state.preparation.get(did)
        # A prepared adventure now reaches the table with its verified encounter
        # (`prototype_prepared.default_encounters`, commit f908c61), so the
        # start asserts "an encounter is prepared, no fight has begun, and the
        # legacy resolver is never consulted" instead of "no encounter exists".
        encounters=state['nat20']['definition']['encounters']
        assert list(encounters)==['first-blood'],encounters
        assert [actor['monster'] for actor in encounters['first-blood']['actors']]==['goblin-warrior'],encounters
        assert state['nat20']['combat'] is None
        assert state['preparation']['source_ruleset']=='dnd2014' and state['preparation']['mechanics_ruleset']=='dnd2024'
        assert saved['body']['ruleset']=='dnd2014' and saved['body']['world_confirmation']==before['body']['world_confirmation']
        assert saved['body']['hero']['name']=='Рив' and saved['body']['background_confirmed']
        assert len(requests)==calls
        # The encounter is offered as a command now; what must stay true is that
        # the game starts with no fight running (asserted above on `combat`).
        offered=[row for row in c.get('/api/campaigns/'+cid+'/screen').json()['commands']
                 if row['parameters'].get('kind')=='start_combat']
        assert [row['parameters'].get('encounter_id') for row in offered]==['first-blood'],offered
        action=dict(request_id='native-check',expected_version=current['version'],action=dict(kind='check',check_id='save-dex'))
        first=c.post('/api/campaigns/'+cid+'/commands',json=action);assert first.status_code==200,first.text
        after=app.state.store.campaign(cid)
        repeat=c.post('/api/campaigns/'+cid+'/commands',json=action);assert repeat.status_code==200,repeat.text
        assert app.state.store.campaign(cid)==after
        assert c.post('/api/prototype/campaigns',json=packet).json()['id']==cid
        different=c.post('/api/prototype/campaigns',json={**packet,'request_id':'different','build':build('wizard')})
        assert different.status_code==409 and app.state.store.campaign(cid)==after
        assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
        assert len(requests)==calls
    finally:c.__exit__(None,None,None)
    from solo.app import create_app
    from solo.preview_mock import PreviewMock
    with TestClient(create_app(tmp_path/'game.sqlite3',PreviewMock(),experimental=True,offline_mock=True)) as restarted:
        replay=restarted.post('/api/prototype/campaigns',json={**packet,'request_id':'restarted'})
        assert replay.status_code==200 and replay.json()['id']==cid,replay.text
        assert len(restarted.get('/api/campaigns').json())==1

def test_failed_native_start_rolls_back_only_staged_identity(tmp_path,monkeypatch):
    app,c,did,_,_,requests=prepare(tmp_path)
    try:
        confirmed=confirmation(c,did,c.get('/api/creation/drafts/'+did+'/world-readiness').json())
        before=deepcopy(app.state.preparation.get(did));calls=len(requests)
        packet=new_game(did,confirmed['revision'])
        for change in [dict(name='   '),dict(build={**build('rogue'),'class_slug':'not-a-class'})]:
            # A body the declared model cannot accept is refused by validation
            # (422) before the route runs; a legal body that fails a rule is
            # refused by the route (409). Either way nothing may be staged.
            assert c.post('/api/prototype/campaigns',json={**packet,**change}).status_code in (409,422)
            assert app.state.preparation.get(did)==before
        def failed(*args,**kwargs):raise ValueError('Synthetic worker refusal')
        monkeypatch.setattr('solo.nat20_api.Nat20GameEngine.create_experimental_campaign',failed)
        result=c.post('/api/prototype/campaigns',json=new_game(did,confirmed['revision']))
        assert result.status_code==409 and 'Synthetic' in result.text
        assert app.state.preparation.get(did)==before and not app.state.store.list_campaigns()
        assert len(requests)==calls
    finally:c.__exit__(None,None,None)

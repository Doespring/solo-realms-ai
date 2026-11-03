"""One source smoke for the bundled factory; host/frozen wiring belongs to Assembly."""
import os
os.environ['SOLO_TEST']='1'

import json
import socket
import sys
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from solo.app import create_app
from solo.cloud import CloudLLM
from scripts.smoke_fixture import make_pdf_world_smoke_llm,smoke_pdf_bytes,PDF_SMOKE_KNOWLEDGE

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nat20_builds import legal  # noqa: E402


def test_bundled_pdf_transport_through_registered_api_and_continue(tmp_path,monkeypatch):
    connect=socket.socket.connect
    def no_external(sock,address):
        if address[0] not in ('127.0.0.1','::1'):raise AssertionError('Smoke factory attempted an external network connection')
        return connect(sock,address)  # Windows asyncio's internal loopback socketpair.
    monkeypatch.setattr(socket.socket,'connect',no_external)
    with pytest.raises(ValueError,match='explicit'):
        make_pdf_world_smoke_llm(tmp_path/'not-authorized')
    assert not (tmp_path/'not-authorized').exists()
    with pytest.raises(ValueError,match='previous'):
        make_pdf_world_smoke_llm(tmp_path,smoke_test=True,phase='continue')
    model=make_pdf_world_smoke_llm(tmp_path,smoke_test=True,phase='prepare')
    assert isinstance(model,CloudLLM) and isinstance(model.client._transport,httpx.MockTransport)
    assert model.pdf_world_smoke is True and model.pdf_smoke_packets is model.smoke_packet_log
    db=tmp_path/'solo.sqlite3'
    with TestClient(create_app(db,model,experimental=True,offline_mock=True)) as client:
        draft=client.post('/api/creation/drafts').json();did=draft['id'];url='/api/creation/drafts/'+did
        client.put(url,json=dict(description='Explicit synthetic PDF, D&D 2014; Nat20 rules confirmed separately.',revision=draft['revision'])).raise_for_status()
        uploaded=client.post(url+'/files',files={'file':('synthetic-forge.pdf',smoke_pdf_bytes(),'application/pdf')})
        uploaded.raise_for_status();job=uploaded.json()['files'][0]['job']['id'];deadline=time.monotonic()+25
        while client.get('/api/adventure/jobs/'+job).json()['status']!='complete':
            assert time.monotonic()<deadline;time.sleep(.02)
        denied=client.post(url+'/prepare',json=dict(request_id='no-consent',ruleset='dnd2014',world_structure=True))
        assert denied.status_code==409 and not model.pdf_smoke_packets
        run=client.post(url+'/prepare',json=dict(request_id='prepare',ruleset='dnd2014',world_structure=True,consent_to_send=True))
        run.raise_for_status();run_id=run.json()['id']
        while True:
            status=client.get('/api/creation/runs/'+run_id).json()
            if status['status'] not in ('queued','running'):break
            assert time.monotonic()<deadline;time.sleep(.02)
        assert status['ready'] and status['preparation_mode']=='world_structure',status
        assert sum(row['kind']=='preparation_world' for row in model.pdf_smoke_packets)==1
        assert model.pdf_smoke_packets[0]['spans']==1
        current=client.get(url).json()
        confirmed=client.post(url+'/confirm-world',json=dict(expected_revision=current['revision'],run_id=run_id,confirm=True))
        confirmed.raise_for_status();confirmed=confirmed.json()
        build=legal(class_slug='rogue',species_slug='human',level=1,
            equipment=['dagger','leather-armor'])
        created=client.post('/api/prototype/campaigns',json=dict(name='Synthetic rogue',request_id='start',
            adventure='prepared:'+did,adventure_revision=confirmed['revision'],build=build,encounter_monsters=[],
            mechanics_ruleset='dnd2024',rules_confirmed=True,hero_confirmed=True,background_confirmed=True,
            hero_background='Explicit synthetic smoke approval.'))
        created.raise_for_status();cid=created.json()['id'];campaign='/api/campaigns/'+cid
        for key,text,expected in [('look','Осматриваюсь','observation'),('talk','Спрашиваю Dorn про bridge','conversation'),('check','Проверяю ловкость','resolved')]:
            version=client.get(campaign).json()['version']
            response=client.post(campaign+'/command-turn',json=dict(request_id=key,expected_version=version,text=text))
            response.raise_for_status();value=response.json()
            assert value['status']==expected,value
            assert 'GM_SECRET' not in value['narrative']
            if key=='talk':assert PDF_SMOKE_KNOWLEDGE in value['narrative']
        client.post(campaign+'/saves',json=dict(name='PDF smoke checkpoint')).raise_for_status()
        before=client.get(campaign).json()
        before_snapshot=client.get(campaign+'/screen').json()['snapshot']
    with pytest.raises(ValueError,match='new isolated'):
        make_pdf_world_smoke_llm(tmp_path,smoke_test=True,phase='prepare')
    continued=make_pdf_world_smoke_llm(tmp_path,smoke_test=True,phase='continue')
    with TestClient(create_app(db,continued,experimental=True,offline_mock=True)) as client:
        state=client.get(campaign).json()
        assert state['version']==before['version']
        assert client.get(campaign+'/screen').json()['snapshot']==before_snapshot
        value=client.post(campaign+'/command-turn',json=dict(request_id='continue',expected_version=state['version'],text='Проверяю wis'))
        value.raise_for_status();assert value.json()['status']=='resolved',value.text
        assert not any(row['kind']=='preparation_world' for row in continued.pdf_smoke_packets)
    packets=[json.loads(line) for line in continued.smoke_packet_log_path.read_text(encoding='utf8').splitlines()]
    assert {row['phase'] for row in packets}=={'prepare','continue'}
    assert sum(row['kind']=='preparation_world' for row in packets)==1
    assert all(row['transport']=='httpx.MockTransport' and row['external_calls']==0 for row in packets)
    evidence=json.dumps(packets)
    assert 'GM_SECRET' not in evidence and 'synthetic-pdf-smoke-memory-only' not in evidence
    assert not list((tmp_path/'pdf-world-smoke').rglob('*.dpapi'))

"""Regression of the native Preview8 unsupported -> clarification loop."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.preview_mock import PreviewMock
from solo.storage import Store
from test_cloud import sse
from scripts.playable_ai_readiness import mock_cloud


def start(path):
    model = PreviewMock()
    app = create_app(path, model)
    client = TestClient(app)
    client.__enter__()
    return client, model


def turn(client, cid, rid, text, version):
    return client.post(f'/api/campaigns/{cid}/command-turn',json=dict(request_id=rid,expected_version=version,text=text)).json()


def test_native_reproducer_then_known_action(tmp_path):
    client, model = start(tmp_path/'reproducer.sqlite3')
    try:
        cid = client.post('/api/nat20/campaigns',json=dict(name='Preview9 regression',scene='lantern-post')).json()['id']
        first = turn(client,cid,'unsupported','Применяю Bless',1)
        assert first['status'] in ('unsupported','clarification')
        assert first['state_version'] == 1
        second = turn(client,cid,'new-action','Осматриваю следы',1)
        assert second['status'] == 'resolved'
        assert second['state_version'] == 2
        assert first['status'] == 'unsupported'
        packets=len(model.packets)
        assert turn(client,cid,'unsupported','Применяю Bless',1)['status']=='unsupported'
        assert len(model.packets)==packets
        assert Store(tmp_path/'reproducer.sqlite3').open_question(cid) is None
    finally:
        client.__exit__(None,None,None)


def test_previous_unknown_question_does_not_swallow_new_action(tmp_path):
    path=tmp_path/'unknown.sqlite3'
    client, model = start(path)
    try:
        cid=client.post('/api/nat20/campaigns',json=dict(name='Unknown first',scene='lantern-post')).json()['id']
        assert turn(client,cid,'unknown','Неизвестная mock-фраза',1)['status']=='clarification'
    finally:
        client.__exit__(None,None,None)
    # A new application/model must recover pending context, without retaining a loop.
    client, model = start(path)
    try:
        result=turn(client,cid,'known','Осматриваю следы',1)
        assert result['status']=='resolved' and result['state_version']==2
        assert model.packets[0]['packet']['player_intent']=='Неизвестная mock-фраза\nУточнение игрока: Осматриваю следы'
        before=Store(path).campaign(cid)
        count=len(model.packets)
        repeated=turn(client,cid,'known','Осматриваю следы',1)
        assert repeated['events']==result['events'] and repeated['state_version']==2
        assert len(model.packets)==count and Store(path).campaign(cid)==before
        assert turn(client,cid,'known','Проверяю силу',2)['status']=='rejected'
        assert turn(client,cid,'stale','Осматриваю следы',1)['status']=='state_version_error'
        assert len(model.packets)==count and Store(path).campaign(cid)==before
        assert Store(path).open_question(cid) is None
    finally:
        client.__exit__(None,None,None)


def test_true_clarification_uses_joined_answer_not_standalone(tmp_path):
    client, model=start(tmp_path/'answer.sqlite3')
    try:
        cid=client.post('/api/nat20/campaigns',json=dict(name='True question',scene='lantern-post')).json()['id']
        assert turn(client,cid,'ask','Осматриваю',1)['status']=='clarification'
        result=turn(client,cid,'answer','следы',1)
        assert result['status']=='resolved' and result['state_version']==2
        packets=[p['packet'] for p in model.packets if p['mode']=='interpreter']
        assert packets[-1]['player_intent']=='Осматриваю\nУточнение игрока: следы'
        assert Store(client.app.state.game.store.path).open_question(cid) is None
    finally:
        client.__exit__(None,None,None)


def test_cloud_mock_transport_preserves_joined_context_and_contract(tmp_path):
    seen=[]
    def respond(request):
        data=json.loads(request.content)
        seen.append(data)
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(dict(status='command',action=dict(kind='check',check_id='perception'),message=''))))])])
    async def run():
        llm, _=mock_cloud(tmp_path,respond)
        text='Осматриваю\nУточнение игрока: следы'
        intent=await llm.interpret_command(text,dict(game_snapshot={},memory_context={}))
        assert intent.action.check_id=='perception'
        packet=json.loads(seen[0]['messages'][-1]['content'])
        assert packet['player_intent']==text
        assert {t['function']['name'] for t in packet['tools']}=={'check','start_combat','move','dash','attack','end_turn','short_rest','long_rest','end_combat','use_feature','native_action','level_up'}
        assert seen[0]['messages'][0]['role']=='system'
    asyncio.run(run())


def test_pending_context_survives_real_process_and_retries(tmp_path):
    path=tmp_path/'real-process.sqlite3'
    client, _=start(path)
    try:
        cid=client.post('/api/nat20/campaigns',json=dict(name='Real process',scene='lantern-post')).json()['id']
        assert turn(client,cid,'question','Осматриваю',1)['status']=='clarification'
    finally:
        client.__exit__(None,None,None)
    program='''
import json,sys,socket
connect=socket.socket.connect
def local(sock,address):
    if address[0] not in ('127.0.0.1','::1'): raise RuntimeError('External calls forbidden')
    return connect(sock,address)
socket.socket.connect=local
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.preview_mock import PreviewMock
model=PreviewMock()
with TestClient(create_app(sys.argv[1],model)) as client:
    result=client.post('/api/campaigns/'+sys.argv[2]+'/command-turn',json=dict(request_id='child-answer',expected_version=1,text='следы')).json()
print(json.dumps(dict(result=result,interpreted=model.packets[0]['packet']['player_intent']),ensure_ascii=False))
'''
    child=subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(path),cid],
        cwd=Path(__file__).resolve().parents[1],env={**os.environ,'SOLO_TEST':'1'},capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert child.returncode==0,child.stderr
    actual=json.loads(child.stdout)
    assert actual['result']['status']=='resolved'
    assert actual['interpreted']=='Осматриваю\nУточнение игрока: следы'
    client, model=start(path)
    try:
        old=Store(path).campaign(cid)
        repeat=turn(client,cid,'child-answer','следы',1)
        assert repeat['events']==actual['result']['events'] and repeat['state_version']==2
        assert not model.packets and Store(path).campaign(cid)==old
        assert Store(path).open_question(cid) is None
    finally:
        client.__exit__(None,None,None)

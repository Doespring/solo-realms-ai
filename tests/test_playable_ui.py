"""UI HTTP seam: canonical native backend, closed legacy paths, Solo compatibility."""
import copy
import socket

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero,Plan,Step
from solo.llm import CommandMockLLM
from solo.command_engine import CommandEngine
from solo.game_commands import GameCommand


@pytest.fixture
def app(tmp_path,monkeypatch):
    connect=socket.socket.connect
    def local_only(sock,address):
        if address[0] in ('127.0.0.1','::1'):return connect(sock,address)
        raise AssertionError('External AI/network is forbidden')
    monkeypatch.setattr(socket.socket,'connect',local_only)
    import solo.nat20_api
    monkeypatch.setattr(solo.nat20_api.secrets,'randbits',lambda bits:5)
    model=CommandMockLLM({'Смотрю вокруг':dict(status='command',action=dict(kind='check',check_id='perception'),message=''),
        'Лечу':dict(status='unsupported',action=None,message='Полёт пока недоступен.'),
        'Не знаю\nУточнение игрока: Лечу':dict(status='unsupported',action=None,message='Полёт пока недоступен.')})
    model.plan=Plan(summary='Осмотр',steps=[Step(kind='observe',text='Осмотр')])
    return create_app(tmp_path/'ui.sqlite3',model)


def test_native_ui_shared_command_guard_and_checkpoint(app,monkeypatch):
    store=app.state.store
    with TestClient(app) as client:
        assert client.get('/api/nat20/options').json()['available']
        created=client.post('/api/nat20/campaigns',json={'name':'Мирон'})
        assert created.status_code==200,created.text
        cid=created.json()['id'];url='/api/campaigns/'+cid
        current=copy.deepcopy(store.campaign(cid))
        assert client.get(url).json()['backend']=='nat20'
        sheet=client.get(url+'/character').json()
        assert sheet['backend']=='nat20' and sheet['sheet']['ac']==18
        assert sheet['hero']['hp']['current']==12
        assert 'nat20' not in client.get(url).json()['state']
        for suffix in ('play','actions','combat/start','combat/command','rest','inventory/command','ruleset/apply'):
            response=client.post(url+'/'+suffix,json={})
            assert response.status_code==409,(suffix,response.text)
            assert response.json()['detail']['code']=='command_backend_required'
        assert client.put(url+'/character',json={}).status_code==409
        # Binding a reviewed book is the preparation layer, not a second
        # mechanics backend: the request reaches the handler and is stopped by
        # the closed review screen (403), never by the one-backend 409. A body
        # of the route's own shape is sent, so validation cannot mask the gate.
        attach=dict(bundle='ui-bundle',request_id='ui-attach',confirm=True)
        blocked=client.post('/api/adventure/review/campaigns/'+cid+'/attach',json=attach)
        assert blocked.status_code==403,blocked.text
        assert 'command_backend_required' not in blocked.text
        assert store.campaign(cid)==current and app.state.game.llm.metrics==[]
        def forbidden(*args,**kwargs):raise AssertionError('Legacy mechanics called for Nat20')
        monkeypatch.setattr(CommandEngine,'resolve',forbidden)
        body=GameCommand(request_id='ui-check',expected_version=0,action=dict(kind='check',check_id='athletics')).model_dump()
        check=client.post(url+'/commands',json=body)
        assert check.status_code==200,check.text
        assert check.json()['events'][0]['id']=='nat20:ui-check'
        GameCommand.model_validate(check.json()['command'])
        assert client.post(url+'/commands',json=body).json()==check.json()
        assert client.post(url+'/commands',json={**body,'request_id':'stale'}).json()['detail']['code']=='version_conflict'
        version=store.campaign(cid)['version']
        for text,status in [('Не знаю','clarification'),('Лечу','unsupported')]:
            response=client.post(url+'/command-turn',json=dict(request_id=status,text=text,expected_version=version))
            assert response.json()['status']==status,response.text
            assert store.campaign(cid)['version']==version
        text=client.post(url+'/command-turn',json=dict(request_id='free-check',text='Смотрю вокруг',expected_version=version))
        assert text.json()['status']=='resolved',text.text
        saved=client.post(url+'/saves',json=dict(name='Проверенный герой')).json()['id']
        before=copy.deepcopy(store.campaign(cid)['state']['nat20'])
        version=store.campaign(cid)['version']
        assert client.post(url+'/commands',json=dict(request_id='rest',expected_version=version,action=dict(kind='long_rest',food_water=False))).status_code==200
        assert client.post(url+'/restore/'+saved,json={}).status_code==200
        assert store.campaign(cid)['state']['nat20']==before
        packet=client.get(url+'/screen').json()
        assert packet['snapshot']['backend']=='nat20' and packet['snapshot']['hero']['hp']==12
        assert packet['snapshot']['last_roll']['label']=='Проверка'
        store.converse(cid,'abandoned-question','Пробую','Старое уточнение','clarification',
                       dict(command_cycle=True,command_status='clarification',expected_version=0))
        assert client.get(url+'/screen').json()['feedback'] is None
        # Obsolete mutation surfaces fail before request validation, including event IDs.
        event=store.history(cid)[0]['id']
        assert client.post('/api/events/'+event+'/narrate',json={}).status_code==409
        action=store.event(event)['action']
        assert client.post('/api/actions/'+action+'/confirm',json={}).status_code==409
        assert store.campaign(cid)['state']['nat20']==before

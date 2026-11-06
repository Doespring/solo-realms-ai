"""Profile library/history and real narrator context, without external inference."""
import json
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Master,Hero,Plan
from solo.game import SceneCompiler
from solo.storage import Store

def test_profile_crud_versions_and_import_do_not_change_campaign(tmp_path):
    app=create_app(tmp_path/'profiles.sqlite3')
    with TestClient(app) as client:
        campaign=client.post('/api/campaigns',json={'name':'Тест','hero':{'name':'Мирон'}}).json()['id']
        before=client.get('/api/campaigns/'+campaign).json()
        builtin=client.get('/api/master/profiles').json()[0]
        assert builtin['name']=='Живой мир, скрытая механика' and 'literary' in builtin['master']
        body={'name':'Копия','master':builtin['master']}
        profile=client.post('/api/master/profiles',json=body).json()['id']
        body['master']['literary']['rhythm']='неторопливый'
        assert client.put('/api/master/profiles/'+profile,json=body).status_code==200
        history=client.get('/api/master/profiles/'+profile+'/history').json()
        assert history[0]['master']['literary']['rhythm']=='сбалансированный'
        assert client.get('/api/campaigns/'+campaign).json()==before
        assert client.delete('/api/master/profiles/'+builtin['id']).status_code==409
        assert client.delete('/api/master/profiles/'+profile).status_code==200
        assert client.get('/api/campaigns/'+campaign).json()==before
        assert client.get('/api/master/profiles/'+profile+'/history').json()[0]['operation']=='delete'
        assert client.get('/api/ai/costs').json()['calls']==[]

def test_literary_preferences_enter_actual_prompt_without_state_effects(tmp_path):
    store=Store(tmp_path/'scene.sqlite3');cid=store.create('Сцена',Hero(name='Герой'))['id']
    # Long author instructions must be preserved in the actual API prompt.
    master=Master(system_prompt='А'*1000+'КОНЕЦ',literary={'rhythm':'неторопливый','focus':'интриги'})
    store.master(cid,master)
    action=store.stage(cid,'look','Смотрю на окно',Plan(summary='Осмотр',steps=[{'kind':'observe','text':'Смотрю на окно'}]),0,{'natural':True})
    event=store.apply(action['id']);before=store.campaign(cid)
    system,packet,_=SceneCompiler(store).narrator(before,event)
    assert master.system_prompt in system and 'неторопливый' in system and 'интриги' in system
    assert 'а не состоянием мира' in system and store.campaign(cid)==before
    assert 'secret' not in json.loads(packet)

def test_invalid_profile_never_overwrites_library(tmp_path):
    app=create_app(tmp_path/'invalid.sqlite3')
    with TestClient(app) as c:
        old=c.get('/api/master/profiles').json();profile=old[0]['id']
        invalid={'name':'Неверно','master':{'literary':{'rhythm':'игрок автоматически побеждает'}}}
        assert c.put('/api/master/profiles/'+profile,json=invalid).status_code==422
        assert c.get('/api/master/profiles').json()==old

def test_preview_port_keeps_origin_protection(tmp_path):
    app=create_app(tmp_path/'origin.sqlite3',local_port=8002)
    with TestClient(app) as c:
        body={'name':'Тест','hero':{'name':'Мирон'}}
        assert c.post('/api/campaigns',json=body,headers={'Origin':'http://127.0.0.1:8002'}).status_code==200
        assert c.post('/api/campaigns',json=body,headers={'Origin':'https://other.example'}).status_code==403
        assert c.post('/api/campaigns',json=body,headers={'Origin':'http://127.0.0.1:8000'}).status_code==403

"""Actual prototype UI/API -> Nat20 -> memory/checkpoint seam, offline only."""
import json,os,subprocess,sys
from copy import deepcopy
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import ROOT,create_app
from solo.preview_mock import PreviewMock

sys.path.insert(0,str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


def test_wizard_ui_api_magic_save_new_process(tmp_path,monkeypatch):
    monkeypatch.setattr('solo.nat20_api.secrets.randbits',lambda bits:5)
    db=tmp_path/'game.sqlite3'
    app=create_app(db,PreviewMock(),experimental=True,offline_mock=True)
    build=legal(class_slug='wizard',species_slug='human',level=1,
        equipment=['dagger','potion-of-healing'])
    with TestClient(app) as client:
        status=client.get('/api/prototype/status').json()
        assert set(status['components'].values())=={'registered'}
        preview=client.post('/api/prototype/build/preview',json=dict(build=build,prepared_spells=['magic-missile']))
        assert preview.status_code==200 and preview.json()['valid'],preview.text
        created=client.post('/api/prototype/campaigns',json=dict(name='Волшебник интеграции',request_id='new-wizard',
            adventure='authored',description='Подтверждённое игроком начало у пустого погреба.',
            build=build,prepared_spells=['magic-missile'],encounter_monsters=['rat','rat'],
            encounter_attack_bonuses={'rat':0},encounter_attack_sources={
                'rat':'Authored smoke encounter: explicit +0 selected by test author, not a verified SRD stat.'}))
        assert created.status_code==200,created.text
        cid=created.json()['id'];url='/api/campaigns/'+cid;store=app.state.store
        def command(key,**action):
            before=deepcopy(store.campaign(cid))
            response=client.post(url+'/commands',json=dict(request_id=key,expected_version=store.campaign(cid)['version'],action=action))
            if response.status_code!=200:
                assert store.campaign(cid)==before,'Rejected command changed campaign state'
            assert response.status_code==200,response.text
            return response.json()
        look=client.post(url+'/command-turn',json=dict(request_id='look',expected_version=store.campaign(cid)['version'],text='Осматриваюсь'))
        assert look.status_code==200 and look.json()['status']=='observation',look.text
        command('check',kind='check',check_id='save-dex')
        command('start',kind='start_combat',encounter_id='opening')
        assert len(store.campaign(cid)['state']['nat20']['combat']['view']['initiative'])==3
        command('potion',kind='native_action',operation='use_item',item_id='potion-of-healing',target_id='hero')
        save_response=client.post(url+'/saves',json=dict(name='Перед магией'))
        assert save_response.status_code==200,save_response.text
        assert any(s['name']=='Перед магией' for s in store.saves(cid))
        native=deepcopy(store.campaign(cid)['state']['nat20'])
        program='''
import os;os.environ['SOLO_TEST']='1'
import sys,json
from solo.app import create_app
from solo.preview_mock import PreviewMock
from fastapi.testclient import TestClient
app=create_app(sys.argv[1],PreviewMock(),experimental=True,offline_mock=True)
with TestClient(app) as c:
 r=c.get('/api/campaigns/'+sys.argv[2]+'/screen');assert r.status_code==200,r.text
 print(json.dumps(app.state.store.campaign(sys.argv[2])['state']['nat20']))
'''
        child=subprocess.run([sys.executable,'-B','-c',program,str(db),cid],cwd=ROOT,
            capture_output=True,text=True,encoding='utf-8',timeout=60,env={**os.environ,'SOLO_TEST':'1','PYTHONDONTWRITEBYTECODE':'1'})
        assert child.returncode==0,child.stderr
        assert json.loads(child.stdout)==native
        command('next',kind='end_turn')
        cast=command('spell',kind='native_action',operation='cast_spell',spell_id='magic-missile',target_ids=['encounter-0','encounter-1','encounter-1'],slot_level=1)
        state=store.campaign(cid)['state']['nat20']
        assert state['hero']['spell_slots']['1']==1
        assert state['combat']['view']['tracked_hp']['encounter-0']==0
        assert state['combat']['view']['tracked_hp']['encounter-1']==0
        command('finish',kind='end_combat')
        command('rest',kind='long_rest',food_water=True)
        assert store.campaign(cid)['state']['nat20']['hero']['spell_slots']['1']==2
        assert store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])


def test_prepared_source_rogue_social_memory_through_registered_api(tmp_path):
    app=create_app(tmp_path/'social.sqlite3',PreviewMock(),experimental=True,offline_mock=True)
    with TestClient(app) as c:
        draft=c.post('/api/preparation/drafts').json();did=draft['id']
        source=('Старт: Кузница\nОписание: У кузницы стоят открытые ворота. На столе лежат инструменты. '
                'Это явно размеченная авторская сцена для локальной проверки подготовки, а не анализ произвольной книги.\n'
                'NPC: Дорн @ Кузница\nОписание: Кузнец встречает посетителей у рабочего стола.\n'
                'Знание: Мост закрыт после вчерашнего дождя. Это подтверждённая автором информация кузнеца.\n')
        upload=c.post('/api/preparation/drafts/'+did+'/sources',files={'file':('world.txt',source.encode(),'text/plain')})
        assert upload.status_code==200,upload.text
        draft=c.get('/api/preparation/drafts/'+did).json();body=draft['body']
        body.update(name='Кузница Дорна',source_kind='custom',prepared=True,background_confirmed=True,
                    start_name='Кузница',opening='У кузницы стоят открытые ворота. Дорн встречает гостя.',
                    hero=dict(name='Рив',class_name='rogue',species='halfling'))
        saved=c.put('/api/preparation/drafts/'+did,json=dict(body=body,revision=draft['revision']))
        assert saved.status_code==200,saved.text
        build=legal(class_slug='rogue',species_slug='halfling',level=1,
                   equipment=['dagger','leather-armor'])
        response=c.post('/api/prototype/campaigns',json=dict(name='Рив',request_id='prepared-rogue',
            adventure='prepared:'+did,adventure_revision=saved.json()['revision'],build=build,
            encounter_monsters=['rat'],encounter_attack_bonuses={'rat':0},
            encounter_attack_sources={'rat':'Explicit author-selected smoke encounter; not an SRD stat assertion.'}))
        assert response.status_code==200,response.text
        cid=response.json()['id'];url='/api/campaigns/'+cid;store=app.state.store
        initial=deepcopy(store.campaign(cid)['state'])
        for key,text,status in [('look','Осматриваюсь','observation'),('talk','Спрашиваю Дорна про мост','conversation')]:
            packet=dict(request_id=key,expected_version=store.campaign(cid)['version'],text=text)
            result=c.post(url+'/command-turn',json=packet)
            assert result.status_code==200 and result.json()['status']==status,result.text
            if status=='conversation':assert 'Мост' in result.json()['narrative']
        state=store.campaign(cid)['state']
        assert state['hero']==initial['hero'] and state['facts']==initial['facts']
        assert state['nat20']['definition']['build']['class_slug']=='rogue'
        assert store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])

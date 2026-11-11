from solo.version import SCHEMA_VERSION
import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from solo.app import ActionRequest, create_app
from solo.contracts import Hero, Master, Plan, Step
from solo.game import Game, SceneCompiler
from solo.llm import CloudLLM, MockLLM
from solo.narration import Narrative, StreamPresentation, schema
from solo.profiles import PERSONAL, PERSONAL_NAME
from solo.storage import Store
from solo.gm import GmAnswer,render as gm_render

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'test.sqlite3')

@pytest.fixture
def campaign(store):
    return store.create('Контроль 1.5',Hero(name='Мирон'))['id']

def apply(store,campaign,request='one',kind='observe'):
    p=Plan(summary='Осмотр',steps=[Step(kind=kind,text='Осматриваю дверь',target='дверь')])
    a=store.stage(campaign,request,p.summary,p,store.campaign(campaign)['version'],{})
    return store.apply(a['id'])

def structure():
    return dict(description='Пламя дрожит.',dialogue=[dict(speaker='Ирина',text='Кого ищешь?')],
                proposals=[dict(category='discovery',text='На письме знак старой гильдии.')])

def test_old_database_migration_preserves_raw_campaign_and_plan(store,campaign):
    e=apply(store,campaign)
    with store.connection(True) as db:
        raw=dict(db.execute('SELECT * FROM campaigns').fetchone())
        old=json.loads(raw['master'])
        for k in ('profile_name','hide_mechanics','declared_lethal_risk','romance_detail'):old.pop(k)
        db.execute('UPDATE campaigns SET master=?',(json.dumps(old,ensure_ascii=False),))
        raw=dict(db.execute('SELECT * FROM campaigns').fetchone())
        db.execute('ALTER TABLE events DROP COLUMN narrative_data')
        db.execute('PRAGMA user_version=3')
    reopened=Store(store.path)
    with reopened.connection() as db:
        assert dict(db.execute('SELECT * FROM campaigns').fetchone())==raw
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
    assert reopened.event(e['id'])['narrative_data']=='{}'
    assert Master.model_validate(reopened.campaign(campaign)['master']).hide_mechanics is False
    assert Plan.model_validate({'summary':'Старый план','steps':[{'kind':'wait','text':'Жду'}]}).steps[0].constraints==[]

def test_forward_migration_guard(store):
    with store.connection(True) as db:db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError,match='новой версией'):Store(store.path)

@pytest.mark.parametrize('disposition',['now','intended'])
def test_typed_condition_never_executes(store,campaign,disposition):
    p=Plan(summary='План',steps=[Step(kind='move',text='Иду к башне',target='Старая башня',
                                  disposition=disposition,condition='Страж уйдёт')])
    a=store.stage(campaign,'condition','Условие',p,0,{})
    e=store.apply(a['id'])
    assert e['results'][0]['type']=='deferred'
    assert store.campaign(campaign)['state']['location'].startswith('Таверна')

def test_profile_application_archives_configuration_and_preserves_state(store,campaign):
    before=store.campaign(campaign)
    preset=next(p for p in store.profiles() if p['id']=='personal-gm-v1.1')
    assert preset['name']==PERSONAL_NAME and preset['master']['boundaries']==''
    assert preset['master']['hide_mechanics'] is True
    store.master(campaign,PERSONAL)
    assert store.master_history(campaign)[0]['master']==before['master']
    assert store.campaign(campaign)['state']==before['state']
    # Реинициализация не переписывает сохранённые предпочтения.
    store.create_profile('Личный вариант',Master(style='Мой буквальный стиль'))
    assert len(Store(store.path).profiles())==2

@pytest.mark.parametrize('text,mode,expected',[
    ('Мастер, как работает преимущество?','action','gm'),
    ('  мастер: объясни правило','action','gm'),
    ('Почему так?','gm','gm'),
    ('Мастер, вы здесь?','dialogue','action'),
    ('Я говорю Ирине: «Мастер, вы здесь?»','action','action'),
])
def test_gm_route_is_explicit(store,campaign,text,mode,expected):
    async def run():
        llm=MockLLM(Plan(summary='Слова',steps=[Step(kind='speak',text=text,target='Ирина')]))
        game=Game(store,llm)
        result=await game.route(campaign,ActionRequest(request_id='r',text=text,mode=mode,target='Ирина'))
        assert ('gm' in result)==(expected=='gm')
        if expected=='gm':assert len(llm.metrics)==0
    asyncio.run(run())

def test_direct_step_overrides_gm_and_gm_one_call_replay_no_state_change(store,campaign):
    async def run():
        llm=MockLLM();game=Game(store,llm)
        result=await game.route(campaign,ActionRequest(request_id='direct',text='Мастер, жду',mode='gm',step=Step(kind='wait',text='Жду')))
        assert 'event' in result and not llm.metrics
        before=store.campaign(campaign)
        result=await game.route(campaign,ActionRequest(request_id='gm',text='Мастер, обсудим стиль игры?'))
        mid=result['gm']['id']
        assert (await game.route(campaign,ActionRequest(request_id='gm',text='Мастер, обсудим стиль игры?')))['gm']['id']==mid
        # Повтор с изменённым режимом не должен неожиданно выполнять игровой ход.
        assert (await game.route(campaign,ActionRequest(request_id='gm',text='Мастер, обсудим стиль игры?',mode='dialogue',target='Ирина')))['gm']['id']==mid
        assert [x async for x in game.gm_answer(mid)][-1]['type']=='done'
        assert [x async for x in game.gm_answer(mid)][-1]['replayed']
        assert len(llm.metrics)==1 and store.campaign(campaign)==before
    asyncio.run(run())

def test_compiler_whitelists_secrets_numbers_and_history(store,campaign):
    c=store.campaign(campaign);c['state']['locations'][0]['secrets']=['Тайная корона Орлина']
    c['master']=PERSONAL.model_dump()
    previous=apply(store,campaign,'previous')
    store.narrative(previous['id'],'Герой открыл дверь и получил тайную корону.','complete')
    e=apply(store,campaign,'current')
    compiler=SceneCompiler(store)
    system,packet,_=compiler.narrator(c,e)
    data=json.loads(packet)
    assert 'Тайная корона' not in packet and 'получил тайную' not in packet
    assert data['npc'][0]['confirmed_knowledge'].startswith('Брат ушёл')
    assert data['hero']=='Мирон' and 'hp' not in packet and 'abilities' not in packet
    assert any('Осматриваю дверь' in r['content'] for r in data['memory'])
    _,before,_=compiler.narrator(c,dict(e,seq=previous['seq']))
    assert 'past_player_words' not in json.loads(before)
    assert 'скрыт' not in json.dumps(compiler.interpreter(c),ensure_ascii=False)
    _,gm=compiler.gm(c,'Правило?')
    assert 'Тайная корона' not in gm

@pytest.mark.parametrize('size',[1,2,3,7,32,500])
def test_json_stream_chunk_boundaries_and_no_proposal_leak(size):
    data=structure();data['description']='Свет «фонаря» 🌲\nКлюч: "dialogue".'
    data['dialogue'][0]['text']='Ирина: "Кого?" — спросила бы я. \\ 🌙'
    raw=json.dumps(data,ensure_ascii=True)
    stream=StreamPresentation(['Ирина']);parts=[]
    for offset in range(0,len(raw),size):parts.append(stream.feed(raw[offset:offset+size]))
    assert stream.finish().model_dump()==data
    assert ''.join(parts)==data['description']+'\n\nИрина: «'+data['dialogue'][0]['text']+'»'
    assert data['proposals'][0]['text'] not in ''.join(parts)

def test_json_partial_and_wrong_speaker_never_confirm():
    stream=StreamPresentation(['Ирина'])
    stream.feed('{"description":"Свет дрожит')
    assert stream.shown=='Свет дрожит'
    with pytest.raises(ValueError):stream.finish()
    wrong=structure();wrong['dialogue'][0]['speaker']='Мирон'
    stream=StreamPresentation(['Ирина']);stream.feed(json.dumps(wrong))
    with pytest.raises(ValueError,match='говорящий'):stream.finish()
    assert schema([])['properties']['dialogue']['maxItems']==0
    assert schema(['Ирина'],require_reply=True)['properties']['dialogue']['minItems']==1

def test_malformed_utf16_never_enters_visible_utf8_stream():
    stream=StreamPresentation([])
    with pytest.raises(ValueError,match='Unicode'):
        stream.feed('{"description":"Свет \\udc00')
    assert stream.shown==''

def test_structured_stream_closes_runtime_iterator_and_requires_npc_reply():
    class Structured(CloudLLM):
        closed=False
        async def stream(self,*args,**kwargs):
            assert kwargs['grammar']['properties']['dialogue']['minItems']==1
            self.metrics.append({})
            try:
                yield '{"description":"Тёплый свет'
                await asyncio.sleep(100)
            finally:self.closed=True
    async def run():
        llm=Structured();stream=llm.narrate_scene('Система',json.dumps({'npc':[{'name':'Ирина'}],'player_words':[{'words':'Здравствуй'}]}))
        assert await anext(stream)=='Тёплый свет'
        await stream.aclose()
        assert llm.closed and llm.last_narrative is None and llm.metrics[-1]['visible_ttft_ms']>=0
    asyncio.run(run())

def test_edit_and_proposals_never_repeat_mechanics_undo_restore(store,campaign):
    e=apply(store,campaign);before=store.campaign(campaign)
    store.narrative(e['id'],'Первое описание','complete',structure=structure())
    assert store.campaign(campaign)==before
    store.edit_narrative(e['id'],'Исправленные слова')
    assert store.campaign(campaign)==before
    with store.connection() as db:assert db.execute('SELECT old_text FROM narrative_versions').fetchone()[0]=='Первое описание'
    proposal=store.history(campaign)[0]['narrative_data']['proposals'][0]
    cp=store.save(campaign,'Без нового факта')
    confirmed=store.decide_proposal(e['id'],proposal['id'],True)
    assert store.decide_proposal(e['id'],proposal['id'],True)['id']==confirmed['id']
    updated=store.campaign(campaign)['state']
    assert updated['hero']==before['state']['hero'] and updated['turn']==2
    assert updated['facts']==[proposal['text']]
    store.undo(campaign)
    assert store.campaign(campaign)['state']==before['state']
    assert store.history(campaign)[0]['narrative_data']['proposals'][0]['status']=='pending'
    confirmed2=store.decide_proposal(e['id'],proposal['id'],True)
    assert confirmed2['id']!=confirmed['id']
    store.restore(campaign,cp)
    assert store.campaign(campaign)['state']==before['state']

def test_rejected_and_stale_proposals(store,campaign):
    e=apply(store,campaign);store.narrative(e['id'],'Описание','complete',structure=structure())
    proposal=store.history(campaign)[0]['narrative_data']['proposals'][0];before=store.campaign(campaign)
    store.decide_proposal(e['id'],proposal['id'],False)
    assert store.campaign(campaign)==before
    with pytest.raises(ValueError):store.decide_proposal(e['id'],proposal['id'],True)
    store.narrative(e['id'],'','streaming')
    with pytest.raises(ValueError):store.decide_proposal(e['id'],proposal['id'],True)
    store.narrative(e['id'],'Другая версия','complete',structure=structure())
    with pytest.raises(KeyError):store.decide_proposal(e['id'],proposal['id'],True)

@pytest.mark.parametrize('mode',['narrator','gm'])
def test_disconnect_closes_inner_stream_saves_partial_releases_lock(store,campaign,mode):
    class Slow(MockLLM):
        closed=False
        async def stream(self,*args,**kwargs):
            self.metrics.append({'mode':kwargs.get('mode','narrator')})
            try:
                yield 'Начало'
                await asyncio.sleep(100)
            finally:self.closed=True
    async def run():
        llm=Slow();game=Game(store,llm)
        if mode=='narrator':
            e=apply(store,campaign);stream=game.narrate(e['id'])
        else:
            r=await game.route(campaign,ActionRequest(request_id='gm',text='Мастер, обсудим стиль игры?'))
            stream=game.gm_answer(r['gm']['id'])
        before=store.campaign(campaign)
        assert (await anext(stream))['text']=='Начало'
        await stream.aclose()
        assert llm.closed and not game.operation.locked() and store.campaign(campaign)==before
        saved=store.event(e['id']) if mode=='narrator' else store.gm_message(r['gm']['id'])
        assert saved['narrative_status' if mode=='narrator' else 'status']=='error'
        assert saved['narrative' if mode=='narrator' else 'answer']=='Начало'
    asyncio.run(run())

def test_gm_restart_and_latest_history(store,campaign):
    for i in range(55):store.gm_stage(campaign,str(i),'Вопрос '+str(i))
    history=store.gm_history(campaign)
    assert len(history)==50 and history[0]['question']=='Вопрос 5'
    store.gm_finish(history[-1]['id'],'Начало','streaming',[])
    assert Store(store.path).gm_message(history[-1]['id'])['status']=='error'

def test_gm_supported_rule_uses_engine_not_generated_arithmetic():
    wrong=GmAnswer(topic='advantage',answer='Владение даёт +2 преимущества, помеха −2.')
    text=gm_render(wrong,Hero(name='Мирон'))
    assert 'два d20' in text and 'один d20' in text and wrong.answer not in text
    assert 'не даёт преимущество' in text
    assert '+3' in gm_render(GmAnswer(topic='proficiency',answer=''),Hero(name='Мирон',level=5))
    assert 'Мудрость 12 → +1' in gm_render(GmAnswer(topic='modifier',answer=''),Hero(name='Мирон'))

@pytest.mark.parametrize('configured',[None,{'cloud':True,'verified':False},{'cloud':True,'verified':True}])
def test_cloud_startup_never_issues_paid_generation(configured):
    class Warm(CloudLLM):
        calls=0
        async def available(self):return configured
        async def stream(self,*args,**kwargs):
            self.calls+=1
            raise AssertionError('Startup cannot issue an inference call')
            yield ''
    async def run():
        llm=Warm()
        assert (await llm.warmup())['warmup']=='no_paid_startup_request'
        assert llm.calls==0
        await llm.close()
    asyncio.run(run())

def test_api_correction_profile_and_gm_budget(tmp_path):
    llm=MockLLM();app=create_app(tmp_path/'api.sqlite3',llm)
    with TestClient(app) as client:
        c=client.post('/api/campaigns',json={'name':'API','hero':{'name':'Мирон'}}).json();cid=c['id']
        preset=client.get('/api/master/profiles').json()[0]
        assert client.put(f'/api/campaigns/{cid}/master',json=preset['master']).status_code==200
        response=client.post(f'/api/campaigns/{cid}/actions',json={'request_id':'gm','text':'Мастер, обсудим стиль игры?'})
        message=response.json()['gm']['id']
        assert 'done' in client.post(f'/api/gm/{message}/stream').text
        assert len(llm.metrics)==1 and client.get(f'/api/campaigns/{cid}').json()['state']['turn']==0
        action=client.post(f'/api/campaigns/{cid}/actions',json={'request_id':'wait','text':'жду'}).json()
        eid=action['event']['id']
        assert client.put(f'/api/events/{eid}/narrative',json={'text':'Исправлено вручную'}).status_code==200
        assert len(llm.metrics)==1 and client.get(f'/api/campaigns/{cid}').json()['state']['turn']==1

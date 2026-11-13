import asyncio
import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from solo.contracts import Hero, Plan, Step, Master
from solo.storage import Store
from solo.rules import modifier,proficiency,check
from solo.llm import MockLLM
from solo.game import Game
from solo.app import create_app,ActionRequest

class FixedDice:
    def __init__(self,*values):
        self.values=iter(values)
        self.calls=0
    def randint(self,a,b):
        self.calls+=1
        return next(self.values)

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'campaign.sqlite3')

@pytest.fixture
def campaign(store):
    return store.create('Эталонная кампания',Hero(name='Мирон'))['id']

def stage(store,campaign,plan,request='one'):
    return store.stage(campaign,request,'Тестовое действие',plan,store.campaign(campaign)['version'],{})['id']

@pytest.mark.parametrize('score,expected',[(1,-5),(9,-1),(10,0),(14,2),(19,4),(30,10)])
def test_modifier(score,expected):
    assert modifier(score)==expected

@pytest.mark.parametrize('level,expected',[(1,2),(4,2),(5,3),(9,4),(13,5),(20,6)])
def test_proficiency(level,expected):
    assert proficiency(level)==expected

@pytest.mark.parametrize('adv,dis,values,selected',[(False,False,[10],10),(True,False,[4,18],18),(False,True,[4,18],4),(True,True,[9],9)])
def test_advantage_cancellation(adv,dis,values,selected):
    d=FixedDice(*values)
    r=check(Hero(name='Мирон'),Step(kind='check',text='Осмотр',ability='wisdom',dc=12,advantage=adv,disadvantage=dis),d)
    assert r['selected']==selected
    assert d.calls==len(values)
    assert r['success']==(selected+1>=12)

def test_no_automatic_crit_on_checks():
    hero=Hero(name='Мирон',abilities=dict(strength=30,dexterity=10,constitution=10,intelligence=10,wisdom=1,charisma=10))
    assert check(hero,Step(kind='check',text='Поднять',ability='strength',dc=10),FixedDice(1))['success']
    assert not check(hero,Step(kind='check',text='Увидеть',ability='wisdom',dc=20),FixedDice(20))['success']

def test_duplicate_confirmation_is_one_roll(store,campaign):
    action=stage(store,campaign,Plan(summary='Проверка',steps=[Step(kind='check',text='Следы',ability='wisdom',dc=12)]))
    d=FixedDice(16)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:store.apply(action,d),range(2)))
    assert results[0]['id']==results[1]['id']
    assert d.calls==1
    assert store.campaign(campaign)['state']['turn']==1

def test_cancel_and_stale_never_execute(store,campaign):
    p=Plan(summary='Осмотр',steps=[Step(kind='observe',text='Оглядываюсь')])
    a,b=stage(store,campaign,p,'a'),stage(store,campaign,p,'b')
    store.apply(a)
    with pytest.raises(ValueError,match='устарел'):store.apply(b)
    store.cancel(b)
    with pytest.raises(ValueError):store.apply(b)
    assert store.campaign(campaign)['state']['turn']==1

def test_unsupported_rolls_back_whole_plan(store,campaign):
    p=Plan(summary='Осмотр и атака',steps=[Step(kind='check',text='Осмотр',ability='wisdom',dc=12),Step(kind='attack',text='Атакую стража')])
    d=FixedDice(20)
    action=stage(store,campaign,p)
    with pytest.raises(ValueError,match='не поддерживается'):store.apply(action,d)
    assert d.calls==0
    assert store.campaign(campaign)['state']['turn']==0

def test_unknown_destination_is_atomic(store,campaign):
    a=stage(store,campaign,Plan(summary='Иду',steps=[Step(kind='move',text='Иду в неизвестное место',target='Несуществующая локация')]))
    before=store.campaign(campaign)['state']
    with pytest.raises(ValueError):store.apply(a)
    assert store.campaign(campaign)['state']==before

def test_undo_and_restore_after_branch(store,campaign):
    p=Plan(summary='Осмотр',steps=[Step(kind='observe',text='Осмотр')])
    a=stage(store,campaign,p,'a');e=store.apply(a)
    cp=store.save(campaign,'До развилки')
    store.undo(campaign)
    b=stage(store,campaign,p,'b');store.apply(b)
    store.restore(campaign,cp)
    history=store.history(campaign)
    assert {h['id'] for h in history if not h['undone']}=={e['id']}
    store.undo(campaign)
    assert store.campaign(campaign)['state']['turn']==0
    with store.connection() as c:
        audit=c.execute('SELECT * FROM state_operations WHERE campaign=?',(campaign,)).fetchall()
        assert len(audit)==3 and len({r['id'] for r in audit})==3

def test_25_actions_save_restart(store,campaign):
    rng=random.Random(81)
    for i in range(25):
        if i%3==0:
            step=Step(kind='check',text='Осмотр',ability='wisdom',dc=12,proficient=True)
        elif i%3==1:
            step=Step(kind='speak',text='Спрашиваю Ирину о гонце',target='Ирина')
        else:
            step=Step(kind='observe',text='Смотрю на письмо')
        action=stage(store,campaign,Plan(summary=step.text,steps=[step]),str(i))
        store.apply(action,rng)
    before=store.campaign(campaign)
    cp=store.save(campaign,'25 ходов')
    reopened=Store(store.path)
    assert reopened.campaign(campaign)==before
    assert len(reopened.history(campaign))==25
    assert sum(r['type']=='check' for h in reopened.history(campaign) for r in h['results'])==9
    assert reopened.saves(campaign)[0]['id']==cp
    with reopened.connection() as c:
        assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_narrator_failure_and_regeneration_do_not_roll(store,campaign):
    async def run():
        action=stage(store,campaign,Plan(summary='Осмотр',steps=[Step(kind='check',text='Осмотр',ability='wisdom',dc=12)]))
        d=FixedDice(15)
        event=store.apply(action,d)
        game=Game(store,MockLLM(failure=True))
        failed=[e async for e in game.narrate(event['id'])]
        assert failed[-1]['type']=='error'
        assert store.event(event['id'])['narrative_status']=='error'
        game.llm.failure=False
        good=[e async for e in game.narrate(event['id'],True)]
        assert good[-1]['type']=='done'
        assert store.campaign(campaign)['state']['turn']==1 and d.calls==1
        replay=[e async for e in game.narrate(event['id'])]
        assert replay[-1]['replayed']
        assert len(game.llm.metrics)==2
    asyncio.run(run())

def test_modes_budgets_and_confirmation(store,campaign):
    async def run():
        p=Plan(summary='План',steps=[Step(kind='observe',text='Осмотр'),Step(kind='attack',text='Атакую если нападёт',disposition='conditional',condition='страж нападает')],needs_confirmation=False)
        llm=MockLLM(p);game=Game(store,llm)
        routed=await game.route(campaign,ActionRequest(request_id='complex',text='Сложный план'))
        assert routed['action']['status']=='pending' and not llm.metrics[1:]
        assert store.campaign(campaign)['state']['turn']==0
        event=store.apply(routed['action']['id'])
        [e async for e in game.narrate(event['id'])]
        assert len(llm.metrics)==2
        duplicate=await game.route(campaign,ActionRequest(request_id='complex',text='Сложный план'))
        assert duplicate['event']['id']==event['id'] and len(llm.metrics)==2
        dialogue=await game.route(campaign,ActionRequest(request_id='dialogue',text='Здравствуй, Ирина',mode='dialogue',target='Ирина'))
        [e async for e in game.narrate(dialogue['event']['id'])]
        assert len(llm.metrics)==3
        direct=await game.route(campaign,ActionRequest(request_id='move',text='Иду к башне',step=Step(kind='move',text='Иду',target='Старая башня')))
        assert 'event' in direct and len(llm.metrics)==3
    asyncio.run(run())

CORPUS=json.loads((Path(__file__).parent/'russian_actions.json').read_text(encoding='utf-8'))
@pytest.mark.parametrize('case',CORPUS,ids=lambda c:f"ru-{c['id']:02d}")
def test_40_contracts_preserve_deferred_actions(case,store,campaign):
    # Эталоны проверяют программную границу исполнения, а качество LLM — probe_model.py.
    deferred=[Step(kind=kind,text=case['text'],disposition=disp,condition='Условие из реплики' if disp=='conditional' else '')
              for kind,disp in zip(case['kinds'],case['dispositions']) if disp!='now']
    if not deferred:
        deferred=[Step(kind='observe',text=case['text'])]
    plan=Plan(summary=case['text'],steps=deferred)
    event=store.apply(stage(store,campaign,plan),FixedDice())
    assert all(r['type'] in ('deferred','denied','halted') for r in event['results']) if any(d!='now' for d in case['dispositions']) else event['results'][0]['type']=='observe'

def test_scene_cache_and_no_future_context(store,campaign):
    game=Game(store,MockLLM())
    initial=store.campaign(campaign)
    a=game.compiler.scene(initial)
    assert a is game.compiler.scene(initial)
    action=stage(store,campaign,Plan(summary='Иду',steps=[Step(kind='move',text='Иду',target='Старая башня')]))
    event=store.apply(action)
    assert game.compiler.scene(store.campaign(campaign))['location']['name']=='Старая башня'
    assert game.compiler.scene(store.campaign(campaign)) is not a
    store.narrative(event['id'],'Будущая реплика','complete')
    earlier=dict(event,seq=event['seq']-1)
    _,packet,_=game.compiler.narrator(initial,earlier)
    assert 'Будущая реплика' not in packet

def test_profile_version_and_literal_text(store,campaign):
    text='<script>alert(1)</script> Не менять мой стиль.'
    m=Master(narrative_prompt=text)
    store.master(campaign,m)
    assert store.campaign(campaign)['master']['narrative_prompt']==text
    assert len(store.master_history(campaign))==1
    store.master(campaign,Master.model_validate(store.master_history(campaign)[0]['master']))
    assert store.campaign(campaign)['master']['narrative_prompt']!=text

def test_api_validation_and_origins(tmp_path):
    app=create_app(tmp_path/'api.sqlite3',MockLLM())
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.post('/api/campaigns',json={'name':'Имя','hero':{'name':'Мирон','hp':20,'max_hp':12}}).status_code==422
        assert client.post('/api/campaigns',json={'name':'Имя','hero':{'name':'Мирон'}},headers={'Origin':'https://evil.example'}).status_code==403
        # r210: без сцены Nat20 кампании нет — ручной лист отключён.
        assert client.post('/api/campaigns',json={'name':'Имя','hero':{'name':'Мирон'}}).json()['detail']['code']=='nat20_scene_required'
        r=client.post('/api/campaigns',json={'name':'Имя','scene':'lantern-post'})
        cid=r.json()['id']
        assert client.get('/api/campaigns/'+cid).status_code==200
        assert client.get('/api/campaigns/missing').status_code==404
        assert client.get('/',headers={'Host':'evil.example'}).status_code==400

def test_invalid_hero_and_plan():
    with pytest.raises(ValueError):Hero(name='А',abilities={'strength':14})
    with pytest.raises(ValueError):Step(kind='attack',text='Условно',disposition='conditional')

def test_malformed_interpretation_never_applies(store,campaign):
    class Broken(MockLLM):
        async def interpret(self,text,scene):
            self.metrics.append({'mode':'interpreter'})
            raise ValueError('Некорректный JSON')
    async def run():
        llm=Broken();game=Game(store,llm)
        with pytest.raises(ValueError,match='JSON'):
            await game.route(campaign,ActionRequest(request_id='bad',text='Непонятное действие'))
        assert store.campaign(campaign)['state']['turn']==0
        assert len(llm.metrics)==1 and store.pending(campaign)==[]
    asyncio.run(run())

def test_interrupted_narrative_recovered(store,campaign):
    action=stage(store,campaign,Plan(summary='Осмотр',steps=[Step(kind='observe',text='Осмотр')]))
    event=store.apply(action)
    store.narrative(event['id'],'Начало…','streaming')
    reopened=Store(store.path)
    assert reopened.event(event['id'])['narrative_status']=='error'
    assert reopened.campaign(campaign)['state']['turn']==1

from solo.version import SCHEMA_VERSION
import asyncio,copy,json,sqlite3,time
import pytest
from solo.storage import Store
from solo.contracts import Hero,Plan,Step
from solo.game import Game,SceneCompiler
from solo.llm import CloudLLM,MockLLM
from solo.narration import PlainPresentation

@pytest.fixture
def story(tmp_path):
    store=Store(tmp_path/'memory.sqlite3');cid=store.create('Длинная история',Hero(name='Мирон'))['id']
    def setup(state):
        state['locations'] += [
            {'name':'Дом Лады','description':'Комната у дороги','npc':[{'id':'lada','name':'Лада','aliases':['Ладушка'],'known':'Дом принадлежит Ладе.','character':'Спокойная проводница.'}],'objects':['стол'],'facts':[]},
            {'name':'Подземелье','description':'Каменный коридор','npc':[],'objects':['дверь'],'facts':[]},
            {'name':'Рынок','description':'Площадь','npc':[{'id':'oleg','name':'Олег','known':'Знает рынок.'}],'objects':[],'facts':[]},
            {'name':'Мастерская','description':'Пыльный стол','npc':[],'objects':['молоток'],'facts':[]}]
        return [{'type':'scripted'}]
    store.record_scenario(cid,'setup','Начальные авторские данные',setup)
    return store,cid

def act(store,cid,text,kind='observe',target='',request=None):
    p=Plan(summary=text,steps=[Step(kind=kind,text=text,target=target)])
    a=store.stage(cid,request or str(time.time_ns()),text,p,store.campaign(cid)['version'],{})
    return store.apply(a['id'])

def find(store,cid,who,query='',**kw):
    state=store.campaign(cid)['state'];ids=store.memory.identities(cid,state)
    return store.memory.retrieve(cid,audience=[ids['hero'] if who=='hero' else ids['npc'][who]],query=query,
                                 location=ids['location'],limit=8,budget=4000,**kw)['records']

def authored(store,cid,key,text,known_by,truth='fact',scope='private',provenance='scenario',mutate=None,kind='event'):
    def transition(state):
        if mutate:mutate(state)
        return [{'type':'scripted','memory_evidence':[dict(text=text,known_by=known_by,truth=truth,scope=scope,provenance=provenance,kind=kind,importance=9)]}]
    return store.record_scenario(cid,key,'Подтверждённый эпизод сценария',transition)

def test_long_history_promises_restart_aliases_and_not_other_npc(story):
    store,cid=story
    promised=act(store,cid,'Обещаю Ирине вернуться с письмом из катакомб.','speak','Ирина','promise')
    for i in range(100):act(store,cid,'Осматриваю следы на стене номер '+str(i),request='later:'+str(i))
    retrieved=find(store,cid,'Ирина','Ирине, помнишь обещание о подземелье?')
    assert any(r['event_id']==promised['id'] and r['kind']=='promise' and r['truth']=='statement' for r in retrieved)
    assert not any(r['event_id']==promised['id'] for r in find(store,cid,'Лада','Обещание Ирине'))
    reopened=Store(store.path)
    assert any(r['event_id']==promised['id'] for r in find(reopened,cid,'Ирина','Обещание о письме'))
    ids=store.memory.identities(cid,store.campaign(cid)['state'])
    assert ids['npc']['Лада']!=ids['npc']['Ирина']
    event=authored(store,cid,'lada-memory','Лада помогла выбраться из подземелья.',['Лада','hero'])
    r=store.memory.retrieve(cid,audience=[ids['hero']],query='Ладушка, помнишь катакомбы?',limit=8,budget=4000)
    assert any(x['event_id']==event['id'] for x in r['records'])
    assert len(store.history(cid,limit=1000))>=103

def test_absent_npc_does_not_learn_secret_and_packet_never_sends_it(story):
    store,cid=story
    secret=authored(store,cid,'secret','Король Арвен скрывается под чужим именем.',['Лада'],scope='secret')
    hero_only=authored(store,cid,'theft','Герой тайно забрал документ в одиночестве.',['hero'])
    ids=store.memory.identities(cid,store.campaign(cid)['state'])
    with store.connection() as db:rid=db.execute('select id from memory_records where event_id=?',(secret['id'],)).fetchone()[0]
    assert store.memory.knows(cid,ids['npc']['Лада'],rid)
    assert not store.memory.knows(cid,ids['npc']['Ирина'],rid)
    assert not store.memory.knows(cid,ids['hero'],rid)
    assert not find(store,cid,'Ирина','документ Арвен')
    assert not any(r['event_id']==hero_only['id'] for r in find(store,cid,'Ирина','тайный документ'))
    e=act(store,cid,'Ирина, что известно?','speak','Ирина')
    _,packet,_=SceneCompiler(store).narrator(store.campaign(cid),e)
    assert 'Арвен' not in packet and 'тайно забрал' not in packet

def test_rumour_and_belief_are_not_world_truth(story):
    store,cid=story;before=copy.deepcopy(store.campaign(cid)['state']['facts'])
    event=authored(store,cid,'rumour','За башней будто бы живёт дракон.',['Ирина','hero'],truth='rumour',provenance='npc_statement')
    rows=find(store,cid,'Ирина','дракон башня')
    assert any(r['event_id']==event['id'] and r['truth']=='rumour' for r in rows)
    assert store.campaign(cid)['state']['facts']==before
    with pytest.raises(ValueError,match='statement'):
        authored(store,cid,'bad-rumour','Дракон действительно живёт здесь.',['hero'],truth='fact',provenance='npc_statement')
    assert store.request(cid,'bad-rumour') is None

def test_relation_change_has_source_survives_restart_and_undo(story):
    store,cid=story
    def change(state):state['locations'][0]['npc'][0]['relationships']={'hero':{'trust':'осторожное доверие','reason':'Герой помог сохранить письмо.'}}
    event=authored(store,cid,'help','Герой помог Ирине сохранить письмо.',['hero','Ирина'],mutate=change)
    assert any(r['kind']=='relationship' and r['event_id']==event['id'] for r in find(store,cid,'Ирина','доверие письмо'))
    reopened=Store(store.path)
    assert reopened.campaign(cid)['state']['locations'][0]['npc'][0]['relationships']['hero']['trust']=='осторожное доверие'
    store.undo(cid)
    assert 'relationships' not in store.campaign(cid)['state']['locations'][0]['npc'][0]
    assert not any(r['event_id']==event['id'] for r in find(store,cid,'Ирина','доверие'))

def test_transfer_is_single_canonical_scenario_effect_not_new_inventory_engine(story):
    store,cid=story;calls=[]
    def transfer(state):
        calls.append(1);state['locations'][0]['objects'].remove('письмо')
        state['object_owners']={'письмо':'Ирина'}
    e=authored(store,cid,'transfer','Письмо передано Ирине.',['hero','Ирина'],mutate=transfer)
    duplicate=authored(store,cid,'transfer','Письмо передано Ирине.',['hero','Ирина'],mutate=transfer)
    assert duplicate['id']==e['id'] and len(calls)==1
    assert 'письмо' not in store.campaign(cid)['state']['locations'][0]['objects']
    for i in range(51):act(store,cid,'Наблюдаю улицу '+str(i))
    assert 'письмо' not in Store(store.path).campaign(cid)['state']['locations'][0]['objects']
    assert any(r['event_id']==e['id'] for r in find(store,cid,'Ирина','переданное письмо'))

def test_rollback_branches_have_no_future_knowledge(story):
    store,cid=story;root=store.save(cid,'Развилка')
    a=authored(store,cid,'branch-a','В первой ветке Ирина получила обещание помочь гонцу.',['Ирина','hero'],kind='promise')
    save_a=store.save(cid,'Ветка A')
    store.restore(cid,root)
    b=authored(store,cid,'branch-b','Во второй ветке Ирина поссорилась с героем.',['Ирина','hero'],kind='conflict')
    rows=find(store,cid,'Ирина','обещание конфликт')
    assert all(r['event_id']!=a['id'] for r in rows) and any(r['event_id']==b['id'] for r in rows)
    store.restore(cid,save_a)
    rows=find(Store(store.path),cid,'Ирина','обещание конфликт')
    assert any(r['event_id']==a['id'] for r in rows) and all(r['event_id']!=b['id'] for r in rows)
    assert not store.memory.audit()['dangling_events']

def test_cross_campaign_knowledge_and_keys_are_isolated(story):
    store,cid=story;other=store.create('Другая история',Hero(name='Мирон'))['id']
    event=authored(store,cid,'local','У Ирины появилась тайная записка.',['Ирина','hero'])
    assert not find(store,other,'Ирина','записка')
    ids=store.memory.identities(cid,store.campaign(cid)['state']);oid=store.memory.identities(other,store.campaign(other)['state'])
    assert ids['npc']['Ирина']!=oid['npc']['Ирина']
    with store.connection(True) as db:
        with pytest.raises(ValueError,match='same campaign'):
            store.memory.put(db,key='foreign',campaign=other,event=event['id'],kind='event',provenance='scenario',truth='fact',scope='public',location='',text='Ошибка',importance=3,minutes=0,recipients=[ids['npc']['Ирина']])

def test_attempt_and_deferred_intention_are_not_success(story):
    store,cid=story
    with pytest.raises(ValueError,match='не поддерживается'):
        act(store,cid,'Пытаюсь украсть письмо','use','письмо')
    p=Plan(summary='Попытка',steps=[Step(kind='use',text='Пытаюсь украсть письмо',target='письмо')])
    a=store.stage(cid,'natural-attempt','Пытаюсь украсть письмо',p,store.campaign(cid)['version'],{'natural':True})
    e=store.apply(a['id'])
    assert any(r['truth']=='attempt' and 'Результат не установлен' in r['content'] for r in find(store,cid,'hero','письмо'))
    state=store.campaign(cid)['state'];assert 'письмо' in state['locations'][0]['objects']
    p=Plan(summary='Намерение',steps=[Step(kind='move',text='Пойду к башне',target='Старая башня',disposition='conditional',condition='Ирина уйдёт')])
    a=store.stage(cid,'conditional','Позже пойду',p,store.campaign(cid)['version'],{})
    event=store.apply(a['id'])
    assert store.campaign(cid)['state']['location']==state['location']
    assert any(r['truth']=='intent' and r['event_id']==event['id'] for r in find(store,cid,'hero','Пойду к башне'))
    assert not any(r['event_id']==event['id'] for r in find(store,cid,'Ирина','башня'))

def test_prose_and_regeneration_never_create_memory_or_relationships(story):
    store,cid=story;e=act(store,cid,'Осматриваю очаг')
    with store.connection() as db:before=db.execute('select count(*) from memory_records').fetchone()[0]
    state=store.campaign(cid)['state']
    store.narrative(e['id'],'Ирина стала преданной союзницей. Герой получил золотой меч.','complete')
    store.edit_narrative(e['id'],'Секрет: герой — король.')
    assert store.campaign(cid)['state']==state
    with store.connection() as db:assert db.execute('select count(*) from memory_records').fetchone()[0]==before
    assert not any('золотой' in r['content'] for r in find(store,cid,'hero','золотой меч'))

def test_transaction_rolls_back_on_invalid_memory_evidence(story):
    store,cid=story;before=store.campaign(cid)
    def bad(state):
        state['facts'].append('Не должно сохраниться')
        return [{'type':'scripted','memory_evidence':[dict(text='Выдумка',known_by=['hero'],provenance='narrator',truth='fact')]}]
    with pytest.raises(ValueError,match='Untrusted'):store.record_scenario(cid,'bad','Ошибка',bad)
    assert store.campaign(cid)==before and store.request(cid,'bad') is None

def test_migration_backfills_journal_without_changing_original_rows(story):
    store,cid=story;e=act(store,cid,'Обещаю Ирине вернуться.','speak','Ирина')
    with store.connection(True) as db:
        before=tuple(db.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone())
        db.execute('DROP TABLE memory_access');db.execute('DROP TABLE memory_subjects');db.execute('DROP TABLE memory_fts')
        db.execute('DROP TABLE memory_records');db.execute('DROP TABLE memory_entities');db.execute('PRAGMA user_version=5')
    reopened=Store(store.path)
    with reopened.connection() as db:
        assert tuple(db.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone())==before
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
    assert any(r['event_id']==e['id'] for r in find(reopened,cid,'Ирина','обещание'))

def test_historical_packet_omits_future_and_nested_secrets(story):
    store,cid=story;early=act(store,cid,'Смотрю на окно')
    authored(store,cid,'future','Ирина встретила нового посланника.',['hero','Ирина'])
    snapshot=store.campaign(cid);snapshot['state']=early['after_state'];snapshot['version']='old'
    snapshot['state']['locations'][0]['npc'][0]['goals']=[{'text':'Поговорить','scope':'public'},{'text':'Найти короля Арвена','scope':'secret'}]
    _,packet,_=SceneCompiler(store).narrator(snapshot,early)
    assert 'посланника' not in packet and 'Арвена' not in packet
    assert 'Поговорить' in packet and 'packet_version' in packet

@pytest.mark.parametrize('size',[1,2,3,7,32,1000])
def test_plain_stream_handles_split_reasoning_without_json(size):
    raw='<think>Скрытые рассуждения</think>\n\nОгонь трещит.\n— Я помню обещание, — сказала Ирина.'
    p=PlainPresentation();shown=''.join(p.feed(raw[i:i+size]) for i in range(0,len(raw),size));p.finish()
    assert 'Скрытые' not in shown and '<think>' not in shown and 'Я помню обещание' in shown

def test_plain_narrator_uses_no_grammar_and_cancellation_closes_stream():
    class Fake(CloudLLM):
        def __init__(self):super().__init__();self.closed=False;self.kw=None
        async def stream(self,system,user,**kw):
            self.kw=kw;self.metrics.append({})
            try:yield 'Огонь трещит.';yield ' Ирина ждёт.'
            finally:self.closed=True
    async def check():
        llm=Fake();g=llm.narrate_scene('Роль','{}',narrative_format='plain')
        assert await anext(g)=='Огонь трещит.'
        await g.aclose();assert llm.closed and 'grammar' not in llm.kw and llm.last_narrative is None
    asyncio.run(check())

def test_legacy_format_remains_explicit_option(story):
    store,cid=story;e=act(store,cid,'Ирина, ответь.','speak','Ирина');c=store.campaign(cid)
    free,packet,m=SceneCompiler(store).narrator(c,e);assert m.narrator_format=='plain' and 'Без JSON' in free
    c['master']['narrator_format']='structured'
    legacy,other,m=SceneCompiler(store).narrator(c,e)
    assert 'JSON: description' in legacy and json.loads(packet)==json.loads(other)

@pytest.mark.parametrize('size',[1,3,8,1000])
def test_reasoning_after_visible_prose_is_not_shown(size):
    text='Огонь трещит.<think>Секретные рассуждения модели</think> Ирина ждёт.'
    p=PlainPresentation();shown=''.join(p.feed(text[i:i+size]) for i in range(0,len(text),size));p.finish()
    assert shown=='Огонь трещит. Ирина ждёт.'

def test_long_memory_is_compacted_before_budget_filtering(story):
    store,cid=story
    e=authored(store,cid,'long-memory','Обещание о подземелье. '+'Важная подробность. '*70,['hero'])
    ids=store.memory.identities(cid,store.campaign(cid)['state'])
    rows=store.memory.retrieve(cid,audience=[ids['hero']],query='обещание',limit=1,budget=850)['records']
    assert len(rows)==1 and rows[0]['event_id']==e['id'] and len(rows[0]['content'])==480

def test_whisper_is_not_granted_to_other_present_npc(story):
    store,cid=story
    def arrive(state):state['locations'][0]['npc'].append({'id':'witness','name':'Дозорный','known':'Знает таверну.'});return [{'type':'scripted'}]
    store.record_scenario(cid,'new-npc','Дозорный вошёл',arrive)
    e=act(store,cid,'Шепчу Ирине о тайном письме.','speak','Ирина')
    assert any(r['event_id']==e['id'] for r in find(store,cid,'Ирина','тайное письмо'))
    assert all(r['event_id']!=e['id'] for r in find(store,cid,'Дозорный','тайное письмо'))

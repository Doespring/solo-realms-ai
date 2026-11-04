import asyncio,json
from decimal import Decimal
from pathlib import Path
import httpx,pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.cloud import CloudLLM,CloudUnavailable
from solo.cloud_settings import CloudSettings
from solo.cloud_cost import CostLedger,BudgetExceeded
from solo.cloud_control import enable,EnableCloud,status
from solo.ai_roles import routerai_preset,prompt
from solo.rule_knowledge import RuleKnowledge,normalize,topics
from solo.narrator_language import allowed_foreign,validate,NarrativeLanguageError
from test_cloud import TestVault,sse

def setup(tmp_path,respond=None):
    settings=CloudSettings(tmp_path/'cloud-profiles.json',TestVault());p=routerai_preset()
    for role in ('interpreter','narrator','secondary_narrator','pdf_analyst'):
        cfg=getattr(p,role);cfg.endpoint='explicit-route';cfg.input_per_million=cfg.output_per_million=Decimal('1')
    settings.save(p);settings.vault.set(p.id,'one-synthetic-key')
    llm=CloudLLM(settings,CostLedger(tmp_path/'cloud-usage.sqlite3'),client=httpx.AsyncClient(transport=httpx.MockTransport(respond or (lambda r:sse([{'choices':[{'delta':{'content':'Да'}}]}])))))
    return llm,p

def test_enable_requires_confirmation_preserves_single_key_and_never_calls_provider(tmp_path):
    requests=[];llm,p=setup(tmp_path,lambda r:requests.append(r))
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        before=client.get('/api/ai/profiles/'+p.id+'/status').json();assert not before['enabled'] and before['key_present']
        assert {r['code'] for r in before['issues']} >= {'profile_disabled','zero_monthly_budget','zero_preparation_budget'}
        raw=p.model_dump(mode='json');raw['enabled']=True
        assert client.post('/api/ai/profiles',json=raw).status_code==409
        request=dict(amount='2',max_request='1',currency='RUB',confirm=False)
        assert client.post('/api/ai/profiles/'+p.id+'/enable',json=request).status_code==409
        request['confirm']=True;response=client.post('/api/ai/profiles/'+p.id+'/enable',json=request);response.raise_for_status()
        assert response.json()['non_recurring'] and llm.settings.profile().total_budget==Decimal(2)
        assert len(llm.settings.vault.keys)==1 and not requests
        # Saving ordinary form settings cannot enlarge the cumulative authorization.
        raw=llm.settings.profile().model_dump(mode='json');raw['total_budget']='99'
        assert client.post('/api/ai/profiles',json=raw).status_code==409

def test_cumulative_budget_cannot_reset_by_new_session_or_one_shot_approval(tmp_path):
    llm,p=setup(tmp_path);enable(llm.settings,llm.ledger,p.id,EnableCloud(confirm=True,amount='0.01',max_request='0.01',currency='RUB'),llm.session)
    p=llm.settings.profile();p.total_budget=Decimal('0.000001')
    async def run():
        for session in ('first','restarted'):
            llm.session=session
            with pytest.raises(CloudUnavailable,match='Общий подтверждённый лимит'):''.join([x async for x in llm.stream('Роль','Реплика',max_tokens=16)])
        await llm.close()
    asyncio.run(run());assert not llm.ledger.pending()

def test_four_roles_reuse_one_key_and_keep_explicit_routes(tmp_path):
    requests=[]
    def respond(r):requests.append(r);return sse([{'choices':[{'delta':{'content':'Да'}}]}])
    llm,p=setup(tmp_path,respond);p.pdf_analysis_enabled=True;enable(llm.settings,llm.ledger,p.id,EnableCloud(confirm=True,amount=5,max_request=1,currency='RUB'),llm.session)
    async def run():
        for mode in ('interpreter','narrator','pdf_analyst'):
            ''.join([x async for x in llm.stream('','Проверка',mode=mode,max_tokens=16)])
        llm.settings.profile().narrator_selection='secondary'
        ''.join([x async for x in llm.stream('','Проверка',max_tokens=16)]);await llm.close()
    asyncio.run(run());assert len(requests)==4
    assert all(r.headers['Authorization']=='Bearer one-synthetic-key' for r in requests)
    assert len({json.loads(r.content)['model'] for r in requests})==4
    # explicit-route — это предпочтение узла, а не обещание: при 502/503/504
    # тот же модэль может ответить на другом зеркале (solo/cloud.py:189-195),
    # поэтому allow_fallbacks=True. Проверяем главное: маршрут один и тот же
    # и он явно задан для всех четырёх ролей.
    assert all(json.loads(r.content)['provider']['only']==['explicit-route'] for r in requests)
    assert all(json.loads(r.content)['provider']['allow_fallbacks'] is True for r in requests)

@pytest.mark.parametrize('code',[429,503])
@pytest.mark.parametrize('in_stream',[False,True])
def test_provider_failures_are_specific_and_never_retried(tmp_path,code,in_stream):
    requests=[]
    def respond(r):requests.append(r);return sse([{'error':{'code':code,'message':'untrusted secret detail'}}]) if in_stream else httpx.Response(code,json={'error':{'message':'untrusted secret detail'}})
    llm,p=setup(tmp_path,respond);p.enabled=True;p.max_request_cost=p.session_budget=p.daily_budget=p.monthly_budget=Decimal(5)
    async def run():
        with pytest.raises(CloudUnavailable,match=str(code)) as exc:''.join([x async for x in llm.stream('','Проверка',max_tokens=16)])
        assert 'untrusted secret' not in str(exc.value);await llm.close()
    asyncio.run(run());assert len(requests)==1 and llm.metrics[-1]['status']=='error'

def seed(knowledge,edition,kind,text,public=True):
    sha=(edition+kind).encode().hex().ljust(64,'a')[:64]
    with knowledge.db() as db:
        db.execute('INSERT OR IGNORE INTO books(sha,title,path,edition,kind,license,partial,pages,readable,complete) VALUES(?,?,?,?,?,?,?,?,?,?)',(sha,'Тест '+edition+kind,'private.pdf',edition,kind,'user_private',int(kind=='dmg'),1,1,1))
        text+=' Это синтетический проверенный фрагмент правила для локального теста. Состояние игры не изменяется.'
        row=db.execute('INSERT INTO sections(book,page,heading,text,public) VALUES(?,?,?,?,?)',(sha,1,'Преимущество',text,int(public)))
        db.execute('INSERT INTO rule_fts(rowid,search) VALUES(?,?)',(row.lastrowid,normalize(text)+' '+' '.join('rule'+t.replace('_','') for t in topics(text))))

def test_rule_search_separates_editions_private_dmg_and_unknown_named_rules(tmp_path):
    kb=RuleKnowledge(tmp_path/'rules.sqlite3');seed(kb,'dnd2014','phb','Преимущество. Редакция 2014.')
    seed(kb,'dnd2024','phb','Преимущество. Редакция 2024. Проверяемая цитата.')
    seed(kb,'dnd2024','dmg','Преимущество. GM_SECRET_442',False)
    value=kb.search('Как работает преимущество?','dnd2024');assert value['found'] and 'GM_SECRET' not in json.dumps(value)
    assert all(r['edition']=='dnd2024' and r['page']==1 for r in value['excerpts'])
    assert not kb.search('Как работает правило «Лунная монархия»?','dnd2024')['found']
    before=kb._search.cache_info().hits;kb.search('Как работает преимущество?','dnd2024');assert kb._search.cache_info().hits>before

def test_rule_question_is_answered_locally_without_model_or_game_mutation(tmp_path):
    from solo.llm import MockLLM
    from solo.contracts import Hero
    from solo.app import ActionRequest
    app=create_app(tmp_path/'solo.sqlite3',MockLLM());store=app.state.store;campaign=store.create('Локальные правила',Hero(name='Лира'))
    seed(app.state.game.compiler.rule_knowledge,'dnd2024','phb','Преимущество: бросьте два d20 и выберите больший.')
    async def run():
        before=store.campaign(campaign['id'])
        for i,question in enumerate(['Мастер, как работает преимущество?','Мастер, правило Лунной монархии?']):
            result=await app.state.game.route(campaign['id'],ActionRequest(request_id=str(i),text=question));parts=[x async for x in app.state.game.gm_answer(result['gm']['id'])]
            assert parts[-1]['local_rules'];assert 'Источник:' in parts[0]['text'] if i==0 else 'не найдено' in parts[0]['text']
        assert store.campaign(campaign['id'])==before and not app.state.game.llm.metrics
    asyncio.run(run());app.state.adventure_jobs.close()

def test_shared_contract_is_once_and_language_guard_preserves_canonical_names():
    for role in ('deepseek_narrator_v1','venice_narrator_v1'):assert prompt(role).count('Ты — Мастер D&D.')==1
    allowed=allowed_foreign(dict(hero='Marek',npc=[dict(name='Arden')]))
    validate('Marek молчит. Arden ждёт.',allowed)
    validate('Ma',allowed,partial=True)
    with pytest.raises(NarrativeLanguageError):validate('Марек: «وجہ proteína»',allowed)


@pytest.mark.parametrize('format',['plain','structured'])
def test_unknown_cause_has_bounded_russian_reply_without_narrator_or_new_canon(tmp_path,format):
    requests=[];llm,p=setup(tmp_path,lambda r:requests.append(r))
    packet=dict(hero='Арден',npc=[dict(name='Марек',mood='Напуган.')],player_words=[dict(to='Марек',words='Спрашиваю: «Что случилось?»')])
    async def run():
        result=''.join([x async for x in llm.narrate_scene('',json.dumps(packet,ensure_ascii=False),narrative_format=format)])
        assert result=='Марек: «Не знаю.»' and llm.last_narrative_policy=='engine_unknown_knowledge'
        assert llm.last_narrative['proposals']==[] and not requests and not llm.metrics
        await llm.close()
    asyncio.run(run())

def test_unknown_answer_does_not_override_recorded_npc_facts():
    from solo.npc_answers import unknown_reply
    packet=dict(npc=[dict(name='Марек',confirmed_knowledge='Мост смыло паводком.')],player_words=[dict(to='Марек',words='Что случилось?')])
    assert unknown_reply(packet) is None
    packet['npc'][0]['confirmed_knowledge']='';packet['player_words'][0]['words']='Привет, Марек!'
    assert unknown_reply(packet) is None


@pytest.mark.parametrize('key',['outcomes','attempts','deferred','denied'])
def test_unknown_answer_does_not_hide_other_engine_results(key):
    from solo.npc_answers import unknown_reply
    packet=dict(npc=[dict(name='Марек')],player_words=[dict(to='Марек',words='Что случилось?')])
    packet[key]=[dict(action='Открыть дверь',outcome='Дверь открыта')]
    assert unknown_reply(packet) is None


@pytest.mark.parametrize('defect',['key','model','route','price'])
def test_enable_reports_concrete_configuration_defect_before_spending(tmp_path,defect):
    llm,p=setup(tmp_path)
    if defect=='key':llm.settings.vault.keys.clear()
    elif defect=='model':p.interpreter.model=''
    elif defect=='route':p.interpreter.endpoint=''
    else:p.interpreter.input_per_million=None
    with pytest.raises(ValueError):enable(llm.settings,llm.ledger,p.id,EnableCloud(confirm=True,amount=1,max_request=1,currency='RUB'),llm.session)
    assert not llm.settings.profile().enabled and not llm.ledger.summary()['calls']
    asyncio.run(llm.close())

def test_query_term_aliases_do_not_change_the_persisted_fts_index_protocol():
    from solo.rule_knowledge import query_topics
    assert query_topics('Что означает Истощённый?')==['exhaustion']
    assert query_topics('Как вычисляется Класс Защиты?')==['armor']


def test_custom_ruleset_uses_its_engine_without_importing_another_edition(tmp_path):
    from solo.storage import Store
    from solo.contracts import Hero
    from solo.game import SceneCompiler
    from solo.gm import local_rule_answer
    from solo.rulesets import BUILTINS
    store=Store(tmp_path/'solo.sqlite3');campaign=store.create('Custom',Hero(name='Лира'),BUILTINS['custom'])
    compiler=SceneCompiler(store)
    assert 'rule_references' not in compiler.interpreter(campaign,'Иду скрытно')
    answer=local_rule_answer('Как работает преимущество?',campaign['state'],compiler.rule_knowledge)
    assert 'два d20' in answer and 'Пользовательский пресет' in answer

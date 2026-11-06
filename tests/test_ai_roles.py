import asyncio,json
from decimal import Decimal
import httpx,pytest
from solo.action_validator import ActionValidator
from solo.ai_roles import routerai_preset,select_role,reasoning_parameters,VENICE,DEEPSEEK
from solo.cloud_settings import CloudSettings,ModelConfig
from solo.cloud_cost import CostLedger,BudgetExceeded
from solo.cloud import CloudLLM,CloudUnavailable
from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.adventure_analysis import AdventureAnalysis,AnalysisRequest
from test_cloud import TestVault,sse

SCENE={'location':{'name':'Таверна','npc':[{'name':'Марек'}],'objects':['дверь']},'destinations':['Площадь']}

def action(**changes):
    return dict(id='a',category='ACTION_ATTEMPT',kind='move',text='Иду на площадь',target='Площадь',item='',disposition='now',condition='',condition_state='not_applicable',after=[],**changes)

def packet(actions):return dict(summary='Намерение',actions=actions,unclear=[],branches=[])

def test_ai_cannot_add_mechanical_fields_unknown_targets_or_missing_intent():
    for a in [{**action(),'hp':0},{**action(),'target':'Несуществующая цель'},{**action(),'text':'Убиваю всех'}]:
        with pytest.raises(ValueError):ActionValidator.validate(packet([a]),'Иду на площадь',SCENE)

def test_order_is_not_dependency_on_success_and_conditional_remains_unknown():
    a=action();b={**action(),'id':'b','category':'CONDITIONAL_ACTION','text':'осматриваюсь','kind':'observe','target':'','disposition':'conditional','condition':'если дверь открыта','condition_state':'unknown','after':['a']}
    plan=ActionValidator.validate(packet([a,b]),'Иду на площадь, затем, если дверь открыта, осматриваюсь.',SCENE)
    assert plan.steps[0].disposition=='now' and plan.steps[1].condition=='если дверь открыта'
    assert plan.steps[1].disposition=='conditional'
    with pytest.raises(ValueError):ActionValidator.validate(packet([{**a,'after':['b']},b]),'Иду на площадь, затем, если дверь открыта, осматриваюсь.',SCENE)

def test_state_claim_outcome_and_npc_command_never_execute_as_success():
    for category in ('STATE_CLAIM','DESIRED_OUTCOME'):
        plan=ActionValidator.validate(packet([{**action(),'category':category}]),'Иду на площадь',SCENE)
        assert plan.steps[0].disposition=='intended' and plan.unclear
    command={**action(),'category':'NPC_COMMAND','kind':'speak','target':'Марек','text':'Марек, открой дверь'}
    assert ActionValidator.validate(packet([command]),command['text'],SCENE).steps[0].kind=='speak'

def test_negation_preserved_and_model_cannot_certify_condition():
    a={**action(),'text':'Не иду на площадь','disposition':'negated'}
    assert ActionValidator.validate(packet([a]),a['text'],SCENE).steps[0].disposition=='negated'
    with pytest.raises(ValueError):ActionValidator.validate(packet([{**a,'condition_state':'confirmed'}]),a['text'],SCENE)

def setup(tmp_path,respond):
    p=routerai_preset();p.enabled=True;p.monthly_budget=Decimal('10');p.preparation_budget=Decimal('10');p.max_request_cost=Decimal('10');p.session_budget=Decimal('10');p.daily_budget=Decimal('10');p.streaming=True
    for cfg in (p.interpreter,p.narrator,p.secondary_narrator,p.pdf_analyst):cfg.input_per_million=Decimal('1');cfg.output_per_million=Decimal('2')
    settings=CloudSettings(tmp_path/'profiles.json',TestVault());settings.save(p);settings.vault.set(p.id,'test-secret')
    return CloudLLM(settings,CostLedger(tmp_path/'usage.sqlite3'),client=httpx.AsyncClient(transport=httpx.MockTransport(respond))),p

def test_narrators_have_same_packet_and_exactly_one_request_each(tmp_path):
    requests=[]
    def respond(req):requests.append(json.loads(req.content));return sse([{'provider':'synthetic-route','choices':[{'delta':{'content':'Ответ Марека'}}]}])
    llm,p=setup(tmp_path,respond)
    async def execute():
        for choice in ('primary','secondary'):
            p.narrator_selection=choice
            assert ''.join([c async for c in llm.stream('Общий канон','Тот же пакет')])=='Ответ Марека'
        await llm.close()
    asyncio.run(execute());assert [r['model'] for r in requests]==[DEEPSEEK,VENICE]
    assert requests[0]['messages'][1]==requests[1]['messages'][1]
    assert all('Общий канон' in r['messages'][0]['content'] for r in requests)
    assert llm.ledger.summary()['calls'][0]['actual_endpoint']=='synthetic-route'

def test_refusal_never_triggers_secondary_narrator(tmp_path):
    requests=[]
    llm,p=setup(tmp_path,lambda r:requests.append(r) or httpx.Response(403,json={'error':'refused'}))
    async def execute():
        with pytest.raises(CloudUnavailable):''.join([c async for c in llm.stream('Роль','Пакет')])
        await llm.close()
    asyncio.run(execute());assert len(requests)==1 and p.narrator_selection=='primary'

def test_thinking_off_is_not_inferred_from_generic_reasoning_capability():
    p=routerai_preset();p.interpreter.parameters+=['reasoning'];body,status=reasoning_parameters(p,p.interpreter)
    assert body=={} and status=='requested_but_unverified'
    p.interpreter.reasoning_control='routerai_enabled';body,status=reasoning_parameters(p,p.interpreter)
    assert body=={'reasoning':{'enabled':False}}
    assert reasoning_parameters(p,p.secondary_narrator)==({},'provider_default')

def test_monthly_budget_precedes_network_and_startup_does_not_generate(tmp_path):
    requests=[];llm,p=setup(tmp_path,lambda r:requests.append(r) or sse([]));p.monthly_budget=0
    async def execute():
        await llm.warmup()
        with pytest.raises(BudgetExceeded):''.join([c async for c in llm.stream('Роль','Пакет')])
        await llm.close()
    asyncio.run(execute());assert requests==[]

def test_preparation_limit_is_per_bundle_and_has_no_gameplay_calls(tmp_path):
    requests=[];llm,p=setup(tmp_path,lambda r:requests.append(r) or sse([{'choices':[{'delta':{'content':'Готов'}}]}]));p.preparation_budget=0
    async def execute():
        with llm.scope('preparation:bundle','source-job'):
            with pytest.raises(BudgetExceeded):''.join([c async for c in llm.stream('Роль','Пакет',mode='preparation_qwen')])
        assert ''.join([c async for c in llm.stream('Роль','Пакет')])=='Готов'
        await llm.close()
    asyncio.run(execute());assert len(requests)==1

def test_selective_analysis_opt_in_cache_and_gemini_after_qwen_only(tmp_path):
    library=AdventureLibrary(tmp_path);bundle=library.create('Synthetic source');path=tmp_path/'source.txt';path.write_text('При открытии двери прозвучит колокол.',encoding='utf8');job=library.enqueue(bundle['id'],path,'source.txt','book');run(tmp_path,job['id']);doc=library.review(bundle['id'])['documents'][0]['id']
    answer={'suggestions':[{'page':1,'kind':'procedure','quote':'При открытии двери','interpretation':'Кандидат условного события','review':'requires_review'}],'problems':['Сверить условие']};requests=[]
    llm,p=setup(tmp_path,lambda r:requests.append(json.loads(r.content)) or sse([{'choices':[{'delta':{'content':json.dumps(answer,ensure_ascii=False)}}]}]));p.pdf_analysis_enabled=True
    service=AdventureAnalysis(library,llm);body=AnalysisRequest(document=doc,pages=[1],request_id='source')
    async def execute():
        with pytest.raises(ValueError):await service.analyze(bundle['id'],body)
        with pytest.raises(ValueError):await service.analyze(bundle['id'],body.model_copy(update={'role':'gemini','problem':'Условие','consent_to_send':True}))
        body.consent_to_send=True;first=await service.analyze(bundle['id'],body);second=await service.analyze(bundle['id'],body)
        assert first['status']=='requires_review' and not first['cached'] and second['cached'] and len(requests)==1
        await service.analyze(bundle['id'],body.model_copy(update={'role':'gemini','problem':'Условие'}));assert len(requests)==2
        with pytest.raises(ValueError):library.definition(bundle['id'])
        await llm.close()
    asyncio.run(execute())

import asyncio,json,os,uuid
from decimal import Decimal
from pathlib import Path
import httpx,pytest
from fastapi.testclient import TestClient
from solo.cloud import CloudLLM,CloudUnavailable
from solo.cloud_cost import CostLedger,BudgetExceeded
from solo.cloud_settings import CloudProfile,ModelConfig,CloudSettings,WindowsKeyVault
from solo.app import create_app,PlayRequest
from solo.storage import Store
from solo.contracts import Hero,Plan,Step
from solo.game import Game

class TestVault:
    __test__=False
    def __init__(self):self.keys={}
    def exists(self,p):return p in self.keys
    def set(self,p,key):self.keys[p]=key
    def get(self,p):return self.keys.get(p)
    def delete(self,p):self.keys.pop(p,None)

def configured(tmp_path,respond,*,streaming=True,budget='10',usage_currency=None):
    role=ModelConfig(model='test-model',max_tokens=1000,parameters=['max_tokens','temperature','response_format','seed'],structured='json_schema',input_per_million=Decimal('2'),output_per_million=Decimal('4'))
    p=CloudProfile(enabled=True,base_url='https://provider.example/v1',interpreter=role,narrator=role,streaming=streaming,
                   max_request_cost=Decimal(budget),session_budget=Decimal(budget),daily_budget=Decimal(budget),usage_cost_currency=usage_currency)
    settings=CloudSettings(tmp_path/'profiles.json',TestVault());settings.save(p);settings.vault.set(p.id,'synthetic-secret-only-for-tests')
    ledger=CostLedger(tmp_path/'usage.sqlite3')
    client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return CloudLLM(settings,ledger,client=client),p

def sse(frames,done=True):
    text=''.join('data: '+json.dumps(frame,ensure_ascii=False)+'\n\n' for frame in frames)
    if done:text+='data: [DONE]\n\n'
    return httpx.Response(200,headers={'content-type':'text/event-stream'},content=text.encode())

def collect(llm,**kw):
    async def run():
        try:return ''.join([c async for c in llm.stream('Роль','Реплика',**kw)])
        finally:await llm.close()
    return asyncio.run(run())

def test_cloud_wire_usage_and_cost_priority_no_key_in_body_or_metrics(tmp_path):
    requests=[]
    def respond(request):
        requests.append(request)
        return sse([{'choices':[{'delta':{'reasoning_content':'не показывать'},'finish_reason':None}]},
                    {'choices':[{'delta':{'content':'Привет'},'finish_reason':None}]},
                    {'choices':[],'usage':{'prompt_tokens':12,'completion_tokens':8,'completion_tokens_details':{'reasoning_tokens':3},'cost':.25,'currency':'RUB'}}])
    llm,p=configured(tmp_path,respond)
    assert collect(llm)=='Привет'
    body=json.loads(requests[0].content)
    assert str(requests[0].url)=='https://provider.example/v1/chat/completions'
    assert body['messages']==[{'role':'system','content':'Роль'},{'role':'user','content':'Реплика'}]
    assert requests[0].headers['authorization'].startswith('Bearer ')
    assert 'secret' not in requests[0].content.decode() and 'secret' not in json.dumps(llm.metrics)
    row=llm.ledger.summary()['calls'][0]
    assert row['cost']=='0.25' and row['cost_source']=='provider' and row['reasoning_tokens']==3
    assert llm.metrics[-1]['input_tokens_estimated'] is False

def test_unsupported_sampling_and_schema_parameters_not_sent(tmp_path):
    captured=[]
    def respond(req):captured.append(json.loads(req.content));return sse([{'choices':[{'delta':{'content':'Ответ'}}]}])
    llm,p=configured(tmp_path,respond)
    p.narrator.parameters=['max_tokens'];p.narrator.structured='none';p.narrator.top_p=.8
    collect(llm,temperature=.3,seed=5,grammar={'type':'object'})
    assert set(captured[0])=={'model','messages','max_tokens','stream'}

def test_non_streaming_and_unknown_currency_are_labelled_calculated(tmp_path):
    llm,p=configured(tmp_path,lambda r:httpx.Response(200,json={'choices':[{'message':{'content':'Готов'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':100,'completion_tokens':20,'cost':999,'currency':'USD'}}),streaming=False)
    assert collect(llm)=='Готов'
    row=llm.ledger.summary()['calls'][0]
    assert row['cost']=='0.00028' and row['cost_source']=='calculated_from_usage'

@pytest.mark.parametrize('status',[401,403,429,500])
def test_errors_never_retry_or_echo_provider_body(tmp_path,status):
    requests=[]
    def respond(request):requests.append(request);return httpx.Response(status,text='echo synthetic-secret-only-for-tests')
    llm,p=configured(tmp_path,respond)
    with pytest.raises(CloudUnavailable) as e:collect(llm)
    assert 'synthetic-secret' not in str(e.value) and len(requests)==1
    row=llm.ledger.summary()['calls'][0]
    assert row['status']=='error' and row['error_code']=='http_'+str(status)

@pytest.mark.parametrize('exception',[httpx.ConnectError,httpx.ReadTimeout])
def test_network_and_timeout_keep_honest_cost_state(tmp_path,exception):
    def respond(request):raise exception('synthetic-secret should not be echoed',request=request)
    llm,p=configured(tmp_path,respond)
    with pytest.raises(CloudUnavailable) as e:collect(llm)
    assert 'synthetic-secret' not in str(e.value)
    row=llm.ledger.summary()['calls'][0]
    assert row['cost_source']==('not_sent' if exception==httpx.ConnectError else 'reserved_estimate')

def test_missing_usage_never_claims_actual_billing(tmp_path):
    llm,p=configured(tmp_path,lambda r:sse([{'choices':[{'delta':{'content':'Текст'}}]}]))
    collect(llm)
    row=llm.ledger.summary()['calls'][0]
    assert row['usage_source']=='estimated' and row['cost_source']=='reserved_estimate'

def test_broken_stream_without_done_is_error(tmp_path):
    llm,p=configured(tmp_path,lambda r:sse([{'choices':[{'delta':{'content':'Часть'}}]}],done=False))
    with pytest.raises(CloudUnavailable,match='прерван'):collect(llm)
    assert llm.ledger.summary()['calls'][0]['status']=='error'

def test_budget_blocks_before_http_and_approval_is_single_use(tmp_path):
    requests=[]
    def respond(request):requests.append(request);return sse([{'choices':[{'delta':{'content':'Да'}}]}])
    llm,p=configured(tmp_path,respond,budget='0')
    async def run():
        with pytest.raises(BudgetExceeded) as blocked:
            ''.join([c async for c in llm.stream('Роль','Реплика')])
        assert not requests
        approval=blocked.value.approval;llm.ledger.approve(approval)
        assert ''.join([c async for c in llm.stream('Роль','Реплика')])=='Да'
        with pytest.raises(BudgetExceeded):''.join([c async for c in llm.stream('Роль','Реплика')])
        assert len(requests)==1
        with pytest.raises(ValueError):llm.ledger.approve(approval)
        await llm.close()
    asyncio.run(run())

def test_approval_not_reused_for_different_campaign_or_turn(tmp_path):
    requests=[]
    llm,p=configured(tmp_path,lambda r:requests.append(r) or sse([{'choices':[{'delta':{'content':'Да'}}]}]),budget='0')
    async def run():
        with llm.scope('campaign-a','turn-a'):
            with pytest.raises(BudgetExceeded) as blocked:''.join([c async for c in llm.stream('Роль','Реплика')])
        llm.ledger.approve(blocked.value.approval)
        with llm.scope('campaign-b','turn-a'):
            with pytest.raises(BudgetExceeded):''.join([c async for c in llm.stream('Роль','Реплика')])
        assert not requests
        await llm.close()
    asyncio.run(run())

def test_session_and_daily_budget_reservations_are_enforced(tmp_path):
    llm,p=configured(tmp_path,lambda r:sse([{'choices':[{'delta':{'content':'Да'}}]}]))
    p.max_request_cost=Decimal('1');p.session_budget=Decimal('0.005');p.daily_budget=Decimal('0.005')
    async def run():
        ''.join([c async for c in llm.stream('Роль','Реплика')])
        with pytest.raises(BudgetExceeded):''.join([c async for c in llm.stream('Роль','Реплика')])
        llm.session=str(uuid.uuid4())
        with pytest.raises(BudgetExceeded):''.join([c async for c in llm.stream('Роль','Реплика')])
        await llm.close()
    asyncio.run(run())

def test_cancellation_closes_http_stream_and_next_call_works(tmp_path):
    class Chunks(httpx.AsyncByteStream):
        closed=False
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            await asyncio.sleep(100)
        async def aclose(self):self.closed=True
    stream=Chunks();n=[]
    def respond(r):
        n.append(1)
        return httpx.Response(200,stream=stream) if len(n)==1 else sse([{'choices':[{'delta':{'content':'next'}}]}])
    llm,p=configured(tmp_path,respond)
    async def run():
        g=llm.stream('Роль','Реплика');assert await anext(g)=='first';await g.aclose()
        assert stream.closed and not llm.lock.locked()
        assert ''.join([c async for c in llm.stream('Роль','Реплика')])=='next'
        await llm.close()
    asyncio.run(run())
    assert any(r['status']=='cancelled' for r in llm.ledger.summary()['calls'])

def test_interpreter_invalid_json_is_recorded_and_never_applied(tmp_path):
    llm,p=configured(tmp_path,lambda r:sse([{'choices':[{'delta':{'content':'{"summary":"bad"}'}}]}]))
    store=Store(tmp_path/'game.sqlite3');cid=store.create('Игра',Hero(name='Мирон'))['id'];before=store.campaign(cid)
    game=Game(store,llm)
    async def run():
        response=await game.play(cid,PlayRequest(request_id='complex',text='Сначала притворяюсь спящим, затем ищу ключ.'))
        assert response['kind']=='reply';await llm.close()
    asyncio.run(run())
    assert store.campaign(cid)==before
    assert llm.ledger.summary()['calls'][0]['status']=='invalid_contract'

@pytest.mark.skipif(os.name!='nt',reason='Windows CurrentUser DPAPI')
def test_windows_key_encrypted_and_profile_export_has_no_secret(tmp_path):
    settings=CloudSettings(tmp_path/'profiles.json');p=CloudProfile(base_url='https://provider.example/v1')
    settings.save(p);key='completely-synthetic-api-token';settings.vault.set(p.id,key)
    blob=settings.vault.file(p.id).read_bytes()
    assert key.encode() not in blob and settings.vault.get(p.id)==key
    assert key not in json.dumps(settings.public()) and key not in settings.path.read_text()
    settings.vault.delete(p.id);assert not settings.vault.exists(p.id)

def test_api_key_input_never_echoed_in_validation_or_read_endpoints(tmp_path):
    app=create_app(tmp_path/'api.sqlite3')
    with TestClient(app) as client:
        p=CloudProfile(base_url='https://provider.example/v1');assert client.post('/api/ai/profiles',json=p.model_dump(mode='json')).status_code==200
        key='completely-synthetic-api-token'
        if os.name=='nt':
            r=client.put('/api/ai/profiles/'+p.id+'/key',json={'api_key':key});assert r.status_code==200 and key not in r.text
        invalid=client.put('/api/ai/profiles/'+p.id+'/key',json={'api_key':key*400})
        assert invalid.status_code==422 and key not in invalid.text
        assert key not in client.get('/api/ai/profiles').text and key not in client.get('/api/ai/costs').text

def test_offline_app_has_no_local_fallback_and_old_saves_accessible(tmp_path):
    app=create_app(tmp_path/'offline.sqlite3')
    with TestClient(app) as client:
        cid=client.post('/api/campaigns',json={'name':'Локальные данные','hero':{'name':'Мирон'}}).json()['id']
        assert client.get('/api/health').json()['cloud_only']
        assert client.get('/api/health').json()['llm'] is None
        assert client.get('/api/campaigns/'+cid).status_code==200
        assert client.post('/api/campaigns/'+cid+'/saves',json={'name':'Без интернета'}).status_code==200
        assert client.post('/api/campaigns/'+cid+'/play',json={'request_id':'read','text':'Сначала притворяюсь спящим, затем ищу ключ.'}).json()['kind']=='reply'
        assert not app.state.game.llm.metrics and not app.state.costs.summary()['calls']

@pytest.mark.parametrize('url',['http://cloud.example/v1','https://user:key@cloud.example/v1','https://cloud.example/v1?api_key=secret','https://127.0.0.1:5001'])
def test_unsafe_or_local_provider_urls_rejected(url):
    with pytest.raises(ValueError):CloudProfile(base_url=url)

def test_catalog_is_not_a_generation_test_and_profiles_restore(tmp_path):
    requests=[]
    def respond(req):
        requests.append(req)
        return httpx.Response(200,json={'data':[{'id':'catalog-model','name':'Каталог','supported_parameters':['max_tokens','response_format'],'pricing':{'prompt':.0001},'pricing_units':{'prompt':'token'}}]})
    llm,p=configured(tmp_path,respond)
    async def run():
        result=await llm.models();assert result['verified']=='catalog_only' and result['generation_tested'] is False
        assert result['models'][0]['id']=='catalog-model';await llm.close()
    asyncio.run(run())
    assert len(requests)==1 and requests[0].method=='GET' and not llm.metrics
    loaded=CloudSettings(llm.settings.path,llm.settings.vault)
    assert loaded.profile().id==p.id and loaded.catalog[p.id][0]['id']=='catalog-model'
    assert not llm.ledger.summary()['calls']

@pytest.mark.parametrize('payload',[[],{'data':'bad'}])
def test_corrupted_catalog_rejected_without_leaking_secret(tmp_path,payload):
    llm,p=configured(tmp_path,lambda r:httpx.Response(200,json=payload))
    async def run():
        with pytest.raises(CloudUnavailable):await llm.models()
        await llm.close()
    asyncio.run(run())

def test_narrator_error_and_retry_do_not_reroll_or_duplicate_memory(tmp_path):
    requests=[]
    def respond(req):
        requests.append(req)
        return httpx.Response(500) if len(requests)==1 else sse([{'choices':[{'delta':{'content':'Попытка удалась. Огонь трещит.'}}],
                  'usage':{'prompt_tokens':100,'completion_tokens':10}}])
    llm,p=configured(tmp_path,respond)
    store=Store(tmp_path/'game.sqlite3');cid=store.create('Проверка',Hero(name='Мирон'))['id']
    class Dice:
        calls=0
        def randint(self,a,b):self.calls+=1;return 16
    dice=Dice();plan=Plan(summary='Осмотр',steps=[Step(kind='check',text='Осматриваю следы',ability='wisdom',dc=12)])
    action=store.stage(cid,'check','Осматриваю следы',plan,0,{})
    e=store.apply(action['id'],dice);state=store.campaign(cid)['state'];results=e['results']
    with store.connection() as db:count=db.execute('select count(*) from memory_records').fetchone()[0]
    game=Game(store,llm)
    async def run():
        first=[r async for r in game.narrate(e['id'])];assert any(r['type']=='error' for r in first)
        second=[r async for r in game.narrate(e['id'],True)];assert second[-1]['type']=='done'
        replay=[r async for r in game.narrate(e['id'])];assert replay[-1]['replayed']
        await llm.close()
    asyncio.run(run())
    assert dice.calls==1 and store.event(e['id'])['results']==results and store.campaign(cid)['state']==state
    assert len(requests)==2
    with store.connection() as db:assert db.execute('select count(*) from memory_records').fetchone()[0]==count
    costs=llm.ledger.summary(cid,llm.session)
    assert len(costs['turns'])==1 and costs['turns'][0]['calls']==2

def test_unknown_prices_disabled_profile_and_context_block_before_send(tmp_path):
    requests=[];llm,p=configured(tmp_path,lambda r:requests.append(r) or sse([]))
    async def run():
        p.enabled=False
        with pytest.raises(CloudUnavailable):''.join([c async for c in llm.stream('Роль','Реплика')])
        p.enabled=True;p.narrator.input_per_million=None
        with pytest.raises(CloudUnavailable,match='стоимость'):''.join([c async for c in llm.stream('Роль','Реплика')])
        p.narrator.input_per_million=Decimal('2');p.context_tokens=2048
        with pytest.raises(ValueError,match='контекста'):''.join([c async for c in llm.stream('Ж'*3000,'Реплика')])
        await llm.close()
    asyncio.run(run());assert not requests

def test_deleting_active_profile_never_silently_switches_provider(tmp_path):
    settings=CloudSettings(tmp_path/'profiles.json',TestVault())
    a=CloudProfile(base_url='https://a.example/v1');b=CloudProfile(base_url='https://b.example/v1')
    settings.save(a);settings.save(b);settings.activate(a.id);settings.delete(a.id)
    assert settings.active is None
    with pytest.raises(ValueError):settings.profile()

def test_budget_near_limit_warning_uses_persistent_usage(tmp_path):
    llm,p=configured(tmp_path,lambda r:sse([{'choices':[{'delta':{'content':'Да'}}],'usage':{'prompt_tokens':1,'completion_tokens':1,'cost':8,'currency':'RUB'}}]))
    collect(llm)
    limits=llm.ledger.quotas(p,llm.session)
    assert all(r['state']=='warning' for r in limits) and all(r['spent']=='8' for r in limits)

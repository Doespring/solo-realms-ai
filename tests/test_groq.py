import asyncio,json,copy
from decimal import Decimal
import httpx,pytest
from solo.groq import preset,seconds,CANDIDATES
from solo.cloud import CloudUnavailable,GroqThrottle
from test_cloud import configured,sse,collect

def groq(tmp_path,respond,*,retry=0,confirmed=True,catalog=True):
    llm,p=configured(tmp_path,respond);p.provider='Groq';p.base_url='https://api.groq.com/openai/v1';p.free_only=True;p.free_account_confirmed=confirmed;p.retry_429=retry;p.tokens_per_minute=8000;p.currency='USD'
    for cfg in (p.interpreter,p.narrator):cfg.model=CANDIDATES[0];cfg.input_per_million=Decimal(0);cfg.output_per_million=Decimal(0);cfg.reasoning_effort='none';cfg.parameters.append('reasoning_effort')
    llm.settings.save(p)
    if catalog:llm.settings.catalog[p.id]=[{'id':CANDIDATES[0]}]
    return llm,p

def test_preset_never_selects_model_enables_connection_or_reuses_key():
    a,b=preset(),preset();assert a.id!=b.id and not a.enabled and not a.free_account_confirmed and not a.narrator.model and a.retry_429==0
    assert a.free_only and a.base_url=='https://api.groq.com/openai/v1'

@pytest.mark.parametrize('confirmed,catalog',[(False,True),(True,False)])
def test_groq_blocks_generation_before_network_when_free_or_catalog_unverified(tmp_path,confirmed,catalog):
    calls=[];llm,p=groq(tmp_path,lambda req:calls.append(req),confirmed=confirmed,catalog=catalog)
    with pytest.raises(CloudUnavailable):collect(llm)
    assert calls==[]

def test_qwen_none_payload_stream_usage_quota_no_secret_and_no_inference_fallback(tmp_path):
    requests=[]
    def respond(req):
        requests.append(json.loads(req.content));return httpx.Response(200,headers={'content-type':'text/event-stream','x-ratelimit-remaining-tokens':'7600','x-ratelimit-limit-tokens':'8000','x-ratelimit-limit-requests':'1000','x-ratelimit-reset-tokens':'3.5s','x-ratelimit-reset-requests':'synthetic-secret-only-for-tests'},content=sse([{'choices':[{'delta':{'reasoning_content':'PRIVATE THINKING','content':'Добрый вечер.'}}]},{'usage':{'prompt_tokens':100,'completion_tokens':4}}]).content)
    llm,p=groq(tmp_path,respond);text=collect(llm)
    assert text=='Добрый вечер.' and requests[0]['reasoning_effort']=='none' and requests[0]['reasoning_format']=='hidden' and requests[0]['model']==CANDIDATES[0]
    quota=llm.settings.quota[p.id][CANDIDATES[0]];assert quota['headers']['x-ratelimit-limit-requests']=='1000' and quota['headers']['x-ratelimit-reset-tokens']=='3.5'
    assert 'PRIVATE THINKING' not in text and 'synthetic-secret-only-for-tests' not in json.dumps(quota)
    assert llm.metrics[-1]['input_tokens_estimated'] is False and llm.metrics[-1]['output_tokens']==4

def test_429_retries_once_respects_cooldown_and_records_both_attempts(tmp_path):
    requests=[]
    def respond(req):
        requests.append(req)
        return httpx.Response(429,headers={'retry-after':'0.1'},json={'error':{'message':'rate limit'}}) if len(requests)==1 else sse([{'choices':[{'delta':{'content':'Готово.'}}]}])
    llm,p=groq(tmp_path,respond,retry=1);assert collect(llm)=='Готово.' and len(requests)==2
    assert llm.metrics[0]['error_code']=='http_429' and llm.metrics[1]['status']=='ok'
    with llm.ledger.db() as c:assert c.execute('SELECT count(*) FROM calls').fetchone()[0]==2

def test_long_429_never_retries_early_and_remains_blocked_after_restart(tmp_path):
    requests=[]
    def respond(req):requests.append(req);return httpx.Response(429,headers={'retry-after':'60'},json={'error':{}})
    llm,p=groq(tmp_path,respond,retry=1)
    with pytest.raises(GroqThrottle):collect(llm)
    assert len(requests)==1
    from solo.cloud_settings import CloudSettings
    restored=CloudSettings(llm.settings.path,llm.settings.vault);llm.settings=restored
    with pytest.raises(CloudUnavailable):collect(llm)
    assert len(requests)==1

def test_gpt_oss_none_rejected_before_request_then_low_is_explicit(tmp_path):
    calls=[]
    def respond(req):calls.append(json.loads(req.content));return sse([{'choices':[{'delta':{'content':'Ответ.'}}]}])
    llm,p=groq(tmp_path,respond);p.narrator.model=CANDIDATES[1];llm.settings.catalog[p.id].append({'id':CANDIDATES[1]})
    with pytest.raises(CloudUnavailable,match='low/medium/high'):collect(llm)
    assert calls==[]
    llm.client=httpx.AsyncClient(transport=httpx.MockTransport(respond));p.narrator.reasoning_effort='low';assert collect(llm)=='Ответ.'
    assert calls[0]['reasoning_effort']=='low' and calls[0]['include_reasoning'] is False and 'reasoning_format' not in calls[0]

def test_cancel_during_429_backoff_never_retries(tmp_path):
    calls=[]
    def respond(req):calls.append(req);return httpx.Response(429,headers={'retry-after':'5'},json={'error':{}})
    llm,p=groq(tmp_path,respond,retry=1)
    async def run():
        async def consume():return [part async for part in llm.stream('Роль','Реплика')]
        task=asyncio.create_task(consume())
        while not llm.metrics or llm.metrics[-1]['status']=='running':await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await llm.close()
    asyncio.run(run());assert len(calls)==1 and len(llm.metrics)==1

def test_nonzero_reported_cost_disables_free_profile_and_quota_cap_blocks_next(tmp_path):
    calls=[]
    def respond(req):calls.append(req);return sse([{'choices':[{'delta':{'content':'Ответ.'}}]},{'usage':{'prompt_tokens':10,'completion_tokens':3,'cost':.01,'currency':'USD'}}])
    llm,p=groq(tmp_path,respond);collect(llm);assert not p.enabled and not p.free_account_confirmed
    with pytest.raises(CloudUnavailable):collect(llm)
    assert len(calls)==1

@pytest.mark.parametrize('cap',["requests","tokens"])
def test_local_quota_blocks_before_second_network_request(tmp_path,cap):
    calls=[]
    def respond(req):calls.append(req);return sse([{'choices':[{'delta':{'content':'Ответ.'}}]},{'usage':{'prompt_tokens':100,'completion_tokens':4}}])
    llm,p=groq(tmp_path,respond)
    if cap=='requests':p.requests_per_minute=1
    else:p.tokens_per_minute=1200  # Fits one request with framing/headroom; not the second.
    assert collect(llm)=='Ответ.'
    with pytest.raises(CloudUnavailable,match='лимит'):collect(llm)
    assert len(calls)==1

@pytest.mark.parametrize('value,result',[('2',2),('2m59.56s',179.56),('7.66s',7.66),('50ms',.05),('nan',None),('credential-value',None)])
def test_retry_after_duration_parser(value,result):assert seconds(value)==result

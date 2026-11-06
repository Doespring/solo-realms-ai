"""Stage 1 money/context/concurrency tests; only own fresh SQLite and mock HTTP."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from solo.cloud import CloudUnavailable
from solo.cloud_context import estimate_context,text_estimate
from solo.cloud_cost import CostLedger
from solo.cloud_settings import CloudSettings,ModelConfig
from test_cloud import configured,sse


def test_common_character_estimate_has_explicit_margin_and_no_utf8_ru_multiplier():
    assert text_estimate('a'*320)==100
    assert text_estimate('я'*240)==100
    assert text_estimate('😀')==4  # Unknown scripts retain byte headroom.
    text='Следопыт осматривает следы у старого моста. '*200
    value=estimate_context([dict(role='user',content=text)])
    assert value['exact'] is False and value['safety_margin_tokens']>0
    assert len(text)/4 < value['input_tokens_estimate'] < len(text)
    assert value['text_utf8_bytes']>value['input_tokens_estimate']*2
    packet=json.dumps(dict(герой='Мирон',сцена=text,tools=[dict(name='check',parameters=dict(check_id='следы'))]),ensure_ascii=False)
    estimate=estimate_context([dict(role='user',content=packet)])
    assert estimate['input_tokens_estimate']<len(packet) and estimate['image_tokens_estimate']==0


@pytest.mark.parametrize('rate,expected',[(None,'0.00136'),('0.5','0.0016'),('0','0.0012')])
def test_explicit_hit_default_and_new_rate_alias_are_separate_from_reserve(tmp_path,rate,expected):
    usage=dict(prompt_tokens=1000,completion_tokens=200,prompt_tokens_details=dict(cached_tokens=800))
    llm,profile=configured(tmp_path,lambda r:sse([dict(choices=[dict(delta=dict(content='Ответ.'))]),dict(choices=[],usage=usage)]))
    profile.narrator.cache_read_per_million=Decimal(rate) if rate is not None else None
    async def run():
        try:
            assert ''.join([s async for s in llm.stream('Роль','Контекст')])=='Ответ.'
        finally:await llm.close()
    asyncio.run(run())
    metric=llm.metrics[-1];row=llm.ledger.summary()['calls'][0]
    assert row['cost']==expected and row['cache_price_applied']
    assert row['cached_tokens']==row['cached_input_tokens']==metric['cached_tokens']==800
    assert row['cost_source']=='calculated_from_usage' and Decimal(row['reserved'])>=Decimal('.004')


def test_cache_alias_validation_persistence_and_idempotent_synthetic_migration(tmp_path):
    assert 'cache_read_per_million' not in ModelConfig().model_dump()  # Old configs/fingerprints stay stable when unset.
    for bad in (-1,'NaN','Infinity',10000001):
        with pytest.raises(ValueError):ModelConfig(cache_read_per_million=bad)
    with pytest.raises(ValueError):ModelConfig(cache_read_per_million='0.1',cached_input_per_million='0.2')
    usage=dict(prompt_tokens=1000,completion_tokens=200,prompt_cache_hit_tokens=800)
    llm,profile=configured(tmp_path,lambda r:sse([dict(choices=[dict(delta=dict(content='Ответ'))]),dict(choices=[],usage=usage)]))
    profile.narrator.cache_read_per_million=Decimal('.5');llm.settings.save(profile)
    assert CloudSettings(llm.settings.path,llm.settings.vault).profile().narrator.cache_read_per_million==Decimal('.5')
    async def run():
        try:
            async for _ in llm.stream('Роль','Контекст'):pass
        finally:await llm.close()
    asyncio.run(run())
    original=llm.ledger.summary()['calls'][0]
    with llm.ledger.db() as db:db.execute('ALTER TABLE calls DROP COLUMN cached_tokens')
    for _ in range(2):
        migrated=CostLedger(llm.ledger.path).summary()['calls'][0]
        assert migrated['cached_tokens']==800 and migrated['cost']==original['cost']


def test_two_campaigns_overlap_without_crossing_narrative_metrics_or_contract_errors(tmp_path):
    async def run():
        entered=[];both=asyncio.Event();finished=asyncio.Event();done=[]
        async def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][-1]['content'])
            name=packet['atmosphere'];entered.append(name)
            if len(entered)==2:both.set()
            await asyncio.wait_for(both.wait(),3)
            narrative=dict(description=name,dialogue=[],proposals=[])
            return sse([dict(choices=[dict(delta=dict(content=json.dumps(narrative,ensure_ascii=False)))]),dict(choices=[],usage=dict(prompt_tokens=20,completion_tokens=10))])
        llm,_=configured(tmp_path,respond)
        async def narrate(campaign,name):
            with llm.scope(campaign,'turn'):
                text=''.join([c async for c in llm.narrate_scene('Роль',json.dumps(dict(packet_version=2,atmosphere=name,confirmed=[],npc=[]),ensure_ascii=False))])
                done.append(campaign)
                if len(done)==2:finished.set()
                await asyncio.wait_for(finished.wait(),3)
                metric=llm.metrics[-1]
                assert llm.last_narrative['description']==name and text==name
                with llm.ledger.db() as db:
                    assert db.execute('SELECT campaign FROM calls WHERE id=?',(metric['id'],)).fetchone()[0]==campaign
                if campaign=='A':llm.contract_error('synthetic_A_only')
                return metric['id']
        try:
            ids=await asyncio.gather(narrate('A','Альфа ждёт.'),narrate('B','Бета ждёт.'))
            rows={r['id']:r for r in llm.ledger.summary()['calls']}
            assert rows[ids[0]]['status']=='invalid_contract' and rows[ids[1]]['status']=='ok'
            assert len(entered)==2 and not llm.lock.locked()
        finally:await llm.close()
    asyncio.run(run())


@pytest.mark.parametrize('campaign',[None,'same'])
def test_same_campaign_and_unscoped_fallback_remain_serial(tmp_path,campaign):
    async def run():
        entered=[];first=asyncio.Event();release=asyncio.Event();active=0;maximum=0
        async def respond(request):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active);entered.append(request)
            if len(entered)==1:
                first.set();await release.wait()
            active-=1
            return sse([dict(choices=[dict(delta=dict(content='Готово'))])])
        llm,_=configured(tmp_path,respond)
        async def call(turn):
            with llm.scope(campaign,turn):return ''.join([s async for s in llm.stream('Роль','Контекст')])
        try:
            a=asyncio.create_task(call('1'));await asyncio.wait_for(first.wait(),3)
            b=asyncio.create_task(call('2'));await asyncio.sleep(0)
            assert len(entered)==1
            release.set();assert await asyncio.gather(a,b)==['Готово','Готово']
            assert maximum==1 and not llm.campaign_lock(campaign).locked()
        finally:await llm.close()
    asyncio.run(run())


def test_cancellation_keeps_reserve_releases_own_lock_and_other_campaign_can_finish(tmp_path):
    class Paused(httpx.AsyncByteStream):
        closed=False
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            await asyncio.Event().wait()
        async def aclose(self):self.closed=True
    async def run():
        ready=asyncio.Event();body=Paused()
        def respond(request):
            user=json.loads(request.content)['messages'][-1]['content']
            return httpx.Response(200,stream=body) if user=='pause' else sse([dict(choices=[dict(delta=dict(content='Готово'))])])
        llm,_=configured(tmp_path,respond)
        async def call(campaign,text):
            with llm.scope(campaign,text):
                output=[]
                async for part in llm.stream('Роль',text):
                    output.append(part)
                    if text=='pause':ready.set()
                return ''.join(output)
        task=asyncio.create_task(call('A','pause'))
        try:
            await asyncio.wait_for(ready.wait(),3)
            assert await asyncio.wait_for(call('B','other'),3)=='Готово'
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert body.closed and not llm.campaign_lock('A').locked()
            assert await call('A','retry')=='Готово'
            row=next(r for r in llm.ledger.summary()['calls'] if r['status']=='cancelled')
            assert row['cost']==row['reserved'] and row['cost_source']=='reserved_estimate'
        finally:
            if not task.done():task.cancel()
            await llm.close()
    asyncio.run(run())


def test_parallel_campaign_reservations_cannot_overdraw_shared_total_budget(tmp_path):
    async def run():
        entered=asyncio.Event();release=asyncio.Event();sent=[]
        async def respond(request):
            sent.append(request);entered.set();await release.wait()
            return sse([dict(choices=[dict(delta=dict(content='Готово'))])])
        llm,profile=configured(tmp_path,respond);profile.total_budget=Decimal('.005')
        async def call(campaign):
            with llm.scope(campaign,'turn'):return ''.join([c async for c in llm.stream('Роль','Контекст')])
        first=asyncio.create_task(call('A'))
        try:
            await asyncio.wait_for(entered.wait(),3)
            with pytest.raises(CloudUnavailable,match='Общий подтверждённый лимит'):await call('B')
            assert len(sent)==1 and len(llm.ledger.summary()['calls'])==1
            release.set();assert await first=='Готово'
            assert Decimal(llm.ledger.summary()['total'][0]['cost'])<=profile.total_budget
        finally:
            release.set()
            if not first.done():await first
            await llm.close()
    asyncio.run(run())

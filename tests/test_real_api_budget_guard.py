import asyncio,json
from decimal import Decimal
import httpx,pytest
import budget_guard_fixture as runner

def test_aggregate_cap_survives_restart_and_blocks_before_transport(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'QA',tmp_path)
    (tmp_path/'grant.json').write_text(json.dumps(dict(authorized_rub='50',calls=[dict(model='previous',bound_rub='49',provider_cost_rub='49')])),encoding='utf8')
    sent=[];grant=runner.Grant({'test-model':(Decimal('.001'),Decimal('.001'))})
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:sent.append(request)),event_hooks={'request':[grant.request]}) as client:
            with pytest.raises(ValueError,match='50 RUB'):await client.post('https://routerai.ru/api/v1/chat/completions',json=dict(model='test-model',messages=[],max_tokens=100))
    asyncio.run(execute())
    assert not sent and runner.Grant(grant.rates).held()==Decimal('49')

def test_unknown_cancelled_cost_keeps_reservation_and_rub_receipt_releases_it(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'QA',tmp_path);grant=runner.Grant({'test-model':(Decimal('.00001'),Decimal('.00001'))})
    request=httpx.Request('POST','https://routerai.ru/api/v1/chat/completions',json=dict(model='test-model',messages=[],max_tokens=100))
    asyncio.run(grant.request(request));reserved=grant.held()
    grant.finish(dict(status='cancelled',cost_source='reserved_estimate',cost='0',currency='RUB'));assert grant.held()==reserved
    grant.finish(dict(status='cancelled',cost_source='provider',cost='0.02',currency='USD'));assert grant.held()==reserved
    grant.finish(dict(status='cancelled',cost_source='provider',cost='0.02',currency='RUB'));assert grant.held()==Decimal('.02')
    assert runner.Grant(grant.rates).held()==Decimal('.02')

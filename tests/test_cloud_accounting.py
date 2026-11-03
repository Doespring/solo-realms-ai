"""Synthetic transport, profiles and SQLite only. Never sends provider traffic."""
import asyncio
import json
import sqlite3
from decimal import Decimal

import httpx
import pytest

from solo.cloud import CloudUnavailable
from solo.cloud_context import estimate_context, move_turn_limits, IMAGE_ALLOWANCE
from solo.cloud_cost import CostLedger, BudgetExceeded
from solo.cloud_settings import ModelConfig, CloudSettings
from solo.cloud_setup import configure_route
from test_cloud import configured, collect, sse


def usage_response(usage):
    return sse([dict(choices=[dict(delta=dict(content='Готово'))]), dict(usage=usage, choices=[])])


def bill(tmp_path, usage, *, cached_rate='0.5', streaming=True):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        if streaming:
            return usage_response(usage)
        return httpx.Response(200, json=dict(usage=usage, choices=[dict(message=dict(content='Готово'))]))
    llm, profile = configured(tmp_path, respond, streaming=streaming)
    profile.narrator.cached_input_per_million = Decimal(cached_rate) if cached_rate is not None else None
    assert collect(llm) == 'Готово'
    return llm.ledger.summary(), llm.metrics[-1], requests


@pytest.mark.parametrize('content', [
    'Hello, adventurer!', 'Привет, путешественник! Ёж идёт к реке.',
    '{"герой":"Мирон","hp":12,"цели":["следы","river"],"ok":true}',
    'D&D: брось d20 → 17; цель foe-1, клетка [3, 4, 0]. 😀\nJSON {"x":1}',
])
def test_estimate_is_explicit_character_heuristic_plus_margin_and_overhead(content):
    messages = [dict(role='system', content='Роль'), dict(role='user', content=content)]
    estimate = estimate_context(messages)
    baseline = estimate['text_tokens_estimate']
    assert estimate['exact'] is False and estimate['method'] == 'unicode-char-3.2-2.4-plus25pct-v2'
    assert estimate['text_utf8_bytes'] == len(('Роль' + content).encode('utf8'))
    assert 0 < baseline < estimate['text_utf8_bytes']
    assert estimate['safety_margin_tokens'] == (baseline + 3) // 4
    assert estimate['input_tokens_estimate'] == baseline + (baseline + 3) // 4 + estimate['request_overhead_tokens']
    assert estimate['request_overhead_tokens'] >= 128 and estimate['image_tokens_estimate'] == 0


def test_native_schema_is_counted_once_and_images_separately():
    from PIL import Image
    from solo.analysis_images import png_part, image_count
    small = png_part(Image.new('RGB', (64, 64), 'white'))
    varied = png_part(Image.frombytes('RGB', (64, 64), bytes(i % 256 for i in range(64 * 64 * 3))))
    estimates = []
    schema = dict(type='json_schema', json_schema=dict(schema=dict(type='object', properties=dict(имя=dict(type='string')))))
    for image in (small, varied):
        user = [dict(type='text', text='Только эта страница: карта.'), image]
        messages = [dict(role='system', content='Анализ'), dict(role='user', content=user)]
        estimates.append(estimate_context(messages, response_format=schema, image_count=image_count(user)))
    assert len(json.dumps(small)) != len(json.dumps(varied))
    assert estimates[0] == estimates[1]  # base64 is transport, not text tokens
    estimate = estimates[0]
    assert estimate['image_tokens_estimate'] == IMAGE_ALLOWANCE and estimate['image_count'] == 1
    assert estimate['schema_utf8_bytes'] == len(json.dumps(schema, ensure_ascii=False, separators=(',', ':')).encode('utf8'))
    assert 0 < estimate['schema_tokens_estimate'] < estimate['schema_utf8_bytes']


def test_actual_wire_system_prefix_stays_identical_across_response_lengths(tmp_path):
    requests = []
    llm, _ = configured(tmp_path, lambda r: requests.append(json.loads(r.content)) or usage_response({}))
    async def run():
        try:
            for output_limit in (180, 600):
                # Same legacy compiler instructions, different word limit.
                system = f'Статическая роль.\nВсего до {(output_limit - 60) // 5} слов: описание, 0–2 реплики. Заверши JSON до лимита.'
                async for _ in llm.stream(system, '{"сцена":"двор"}', max_tokens=output_limit):
                    pass
        finally:
            await llm.close()
    asyncio.run(run())
    assert requests[0]['messages'][0] == requests[1]['messages'][0]
    assert 'Всего до' not in requests[0]['messages'][0]['content']
    assert '0–2 реплики' in requests[0]['messages'][0]['content']
    assert 'Заверши JSON до лимита.' in requests[0]['messages'][0]['content']
    for request, words in zip(requests, (24, 108)):
        packet = json.loads(request['messages'][-1]['content'])
        assert packet['сцена'] == 'двор'
        assert list(packet)[-1] == '_solo_turn_response_limits'
        assert f'Всего до {words} слов.' in packet['_solo_turn_response_limits']


def test_turn_limit_tail_handles_plain_and_multimodal_content_without_mutation():
    system = 'Всего до 100 слов: описание.'
    parts = [dict(type='text', text='Данные')]
    normalized, new_parts = move_turn_limits(system, parts, 180)
    assert len(parts) == 1 and len(new_parts) == 2 and new_parts[-1]['type'] == 'text'
    assert 'Всего до 24 слов.' in new_parts[-1]['text']
    assert normalized == move_turn_limits(system, 'Данные', 600)[0]
    assert move_turn_limits('Постоянная роль', 'Текст', 180) == ('Постоянная роль', 'Текст')


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('cache_fields', [
    dict(prompt_tokens_details=dict(cached_tokens=800)),
    dict(input_tokens_details=dict(cached_tokens=800)),
    dict(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200),
    dict(prompt_tokens_details=dict(cached_tokens=800), prompt_cache_hit_tokens=800),
])
def test_cached_tokens_are_a_subset_not_double_charged(tmp_path, streaming, cache_fields):
    usage = dict(prompt_tokens=1000, completion_tokens=200, completion_tokens_details=dict(reasoning_tokens=50), **cache_fields)
    summary, metric, _ = bill(tmp_path, usage, streaming=streaming)
    row = summary['calls'][0]
    # 200 uncached*2 + 800 cached*0.5 + 200 output*4 = 1600 micros.
    assert row['cost'] == '0.0016' and row['cost_source'] == 'calculated_from_usage'
    assert row['input_tokens'] == 1000 and row['cached_input_tokens'] == 800
    assert row['reasoning_tokens'] == 50  # already included in output, not charged twice
    assert row['cache_usage_status'] == 'provider' and row['cache_price_applied'] == 1
    assert metric['cache_price_applied'] is True
    assert summary['total'][0]['input_tokens'] == 1000 and summary['total'][0]['cached_input_tokens'] == 800


@pytest.mark.parametrize('cached_rate,cache_fields', [
    (None, dict(prompt_tokens_details=dict(cached_tokens=800))),
    ('0.5', {}),
    ('0.5', dict(prompt_tokens_details=dict(cached_tokens=0))),
    ('0.5', dict(unrecognized_cache_hit=800)),
])
def test_default_cache_tariff_requires_reported_hit_and_never_predicts_one(tmp_path, cached_rate, cache_fields):
    summary, _, _ = bill(tmp_path, dict(input_tokens=1000, output_tokens=200, **cache_fields), cached_rate=cached_rate)
    applied=cached_rate is None and bool(cache_fields.get('prompt_tokens_details',{}).get('cached_tokens'))
    assert summary['calls'][0]['cost'] == ('0.00136' if applied else '0.0028')
    assert bool(summary['calls'][0]['cache_price_applied']) is applied


@pytest.mark.parametrize('fields', [
    dict(prompt_tokens_details=dict(cached_tokens=-1)),
    dict(prompt_tokens_details=dict(cached_tokens=True)),
    dict(prompt_tokens_details=dict(cached_tokens='800')),
    dict(prompt_tokens_details=dict(cached_tokens=1.5)),
    dict(prompt_tokens_details=dict(cached_tokens=1001)),
    dict(prompt_tokens_details=dict(cached_tokens=1000000001)),
    dict(prompt_tokens_details='malformed'),
    dict(prompt_tokens_details=dict(cached_tokens=None)),
    dict(prompt_tokens_details=dict(cached_tokens=800), input_tokens_details=dict(cached_tokens=700)),
    dict(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=300),
    dict(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=False),
])
def test_invalid_or_conflicting_cache_fields_are_visible_and_full_price(tmp_path, fields):
    summary, _, _ = bill(tmp_path, dict(prompt_tokens=1000, completion_tokens=200, **fields))
    row = summary['calls'][0]
    assert row['cost'] == '0.0028' and row['cached_input_tokens'] is None
    assert row['cache_usage_status'] == 'invalid' and not row['cache_price_applied']


@pytest.mark.parametrize('usage', [
    {}, [], dict(prompt_tokens=-1, completion_tokens=20),
    dict(prompt_tokens=True, completion_tokens=20), dict(prompt_tokens='100', completion_tokens=20),
    dict(prompt_tokens=100, completion_tokens=1.5), dict(prompt_tokens=1000000001, completion_tokens=20),
    dict(prompt_tokens=100, input_tokens=200, completion_tokens=20),
    dict(prompt_tokens=100, completion_tokens=20, output_tokens=30),
    dict(input_tokens=100, output_tokens=200, cache_read_input_tokens=800),
    dict(input_tokens=100, output_tokens=200, cache_creation_input_tokens=800),
])
def test_missing_or_invalid_total_usage_keeps_reservation(tmp_path, usage):
    summary, _, _ = bill(tmp_path, usage)
    row = summary['calls'][0]
    assert row['cost_source'] == 'reserved_estimate' and row['cost'] == row['reserved']
    assert not row['cache_price_applied']


@pytest.mark.parametrize('cost', ['0', '0.0032'])
def test_actual_provider_bill_takes_priority_over_cached_calculation(tmp_path, cost):
    summary, metric, _ = bill(tmp_path, dict(prompt_tokens=1000, completion_tokens=200,
                                            prompt_tokens_details=dict(cached_tokens=800), cost=cost, currency='RUB'))
    row = summary['calls'][0]
    assert Decimal(row['cost']) == Decimal(cost) and row['cost_source'] == 'provider'
    assert row['cached_input_tokens'] == 800 and not metric['cache_price_applied']


@pytest.mark.parametrize('bad_cost', [-1, 'NaN', 'Infinity', 'bad', True, 1000001])
def test_invalid_provider_cost_falls_back_to_usage(tmp_path, bad_cost):
    summary, _, _ = bill(tmp_path, dict(prompt_tokens=1000, completion_tokens=200, cost=bad_cost, currency='RUB'))
    assert summary['calls'][0]['cost'] == '0.0028'
    assert summary['calls'][0]['cost_source'] == 'calculated_from_usage'


@pytest.mark.parametrize('cached_rate', ['-1', 'NaN', 'Infinity', '10000001', True])
def test_invalid_cached_tariffs_rejected(cached_rate):
    with pytest.raises(ValueError):
        ModelConfig(cached_input_per_million=cached_rate)


def test_cache_does_not_lower_reservation_and_cannot_bypass_money_limits(tmp_path):
    reserves = []
    for name, rate in [('none', None), ('free_cache', Decimal(0))]:
        requests = []
        llm, profile = configured(tmp_path / name, lambda r: requests.append(r) or usage_response({}), budget='0')
        profile.narrator.cached_input_per_million = rate
        async def run():
            try:
                with pytest.raises(BudgetExceeded) as blocked:
                    async for _ in llm.stream('Роль', 'Реплика'):
                        pass
                reserves.append(blocked.value.quote)
            finally:
                await llm.close()
        asyncio.run(run())
        assert not requests and not llm.ledger.summary()['calls']
    assert reserves[0] == reserves[1]


def test_reported_overrun_is_kept_and_blocks_next_request_by_total_budget(tmp_path):
    requests = []
    llm, profile = configured(tmp_path, lambda r: requests.append(r) or usage_response(dict(cost='0.007', currency='RUB')))
    profile.total_budget = Decimal('0.005')
    profile.narrator.cached_input_per_million = Decimal(0)
    async def run():
        try:
            async for _ in llm.stream('Роль', 'Реплика'):
                pass
            with pytest.raises(CloudUnavailable, match='Общий подтверждённый лимит'):
                async for _ in llm.stream('Роль', 'Реплика'):
                    pass
        finally:
            await llm.close()
    asyncio.run(run())
    assert len(requests) == 1 and llm.ledger.summary()['total'][0]['cost'] == '0.007'


def test_more_expensive_cache_rate_is_covered_and_rates_are_snapshotted(tmp_path):
    llm, profile = configured(tmp_path, lambda r: usage_response({}))
    cfg = profile.narrator
    cfg.cached_input_per_million = Decimal(8)
    cfg.price_checked_at = 'reservation-time'
    body = dict(model=cfg.model, max_tokens=1000)
    call, quote = llm.ledger.reserve(profile, cfg, body, campaign=None, session='test', turn_ref='test', purpose='narrator', input_estimate=1000)
    assert quote == 12000  # 1000*8 + 1000*4, no assumed cache discount
    cfg.cached_input_per_million = Decimal(0)
    cfg.price_checked_at = 'changed-after-send'
    cfg.input_per_million = cfg.output_per_million = Decimal(0)
    result = llm.ledger.finish(call, profile, cfg, dict(prompt_tokens=1000, completion_tokens=200,
                                                     prompt_tokens_details=dict(cached_tokens=800)), dict(status='ok'))
    assert result['cost'] == '0.0076'  # reserved rates: 200*2 + 800*8 + 200*4
    assert llm.ledger.summary()['calls'][0]['price_checked_at'] == 'reservation-time'
    asyncio.run(llm.close())


def test_legacy_database_and_profiles_migrate_without_inventing_cache_discounts(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE calls(id TEXT PRIMARY KEY,campaign TEXT,session TEXT NOT NULL,turn_ref TEXT NOT NULL,
            provider TEXT NOT NULL,model TEXT NOT NULL,purpose TEXT NOT NULL,currency TEXT NOT NULL,day TEXT NOT NULL,created REAL NOT NULL,
            reserved INTEGER NOT NULL,cost INTEGER NOT NULL DEFAULT 0,cost_source TEXT NOT NULL DEFAULT 'reserved_estimate',status TEXT NOT NULL DEFAULT 'reserved',
            input_tokens INTEGER,output_tokens INTEGER,reasoning_tokens INTEGER,usage_source TEXT NOT NULL DEFAULT 'estimated',
            ttft_ms REAL,duration_ms REAL,error_code TEXT NOT NULL DEFAULT '',retry_of TEXT)''')
        db.execute("INSERT INTO calls(id,session,turn_ref,provider,model,purpose,currency,day,created,reserved,cost,cost_source,status,input_tokens,output_tokens) VALUES('old','s','t','test','model','narrator','RUB','2026-10-17',0,8000,7000,'provider','ok',100,20)")
    ledger = CostLedger(path)
    row = ledger.summary()['calls'][0]
    assert row['cost'] == '0.007' and row['cost_source'] == 'provider'
    assert row['cached_input_tokens'] is None and not row['cache_price_applied']
    assert json.loads(row['input_estimate_details']) == {} and row['cached_input_rate'] == ''
    assert CostLedger(path).summary()['calls'] == ledger.summary()['calls']
    llm, profile = configured(tmp_path / 'profile', lambda r: usage_response({}))
    data = json.loads(llm.settings.path.read_text(encoding='utf8'))
    for role in ('narrator', 'interpreter'):
        data['profiles'][0][role].pop('cached_input_per_million')
    llm.settings.path.write_text(json.dumps(data), encoding='utf8')
    restored = CloudSettings(llm.settings.path, llm.settings.vault)
    assert restored.profile().narrator.cached_input_per_million is None
    restored.profile().narrator.cached_input_per_million = Decimal('0.5')
    restored.save(restored.profile())
    assert CloudSettings(restored.path, restored.vault).profile().narrator.cached_input_per_million == Decimal('0.5')
    asyncio.run(llm.close())


def test_route_refresh_clears_unverified_cache_tariff():
    cfg = ModelConfig(cached_input_per_million=Decimal('0.1'),cache_read_per_million=Decimal('0.1'))
    row = dict(tag='new-route', supported_parameters=['max_tokens'], context_length=8192,
               pricing=dict(prompt='0.000002', completion='0.000004'), pricing_units=dict(prompt='token', completion='token'))
    configured_cfg, _ = configure_route(cfg, row)
    assert configured_cfg.cached_input_per_million is None
    assert configured_cfg.cache_read_per_million is None


def test_legacy_inflight_cost_stays_reserved_and_new_cached_calls_work(tmp_path):
    # Start with a database without any new accounting columns.
    path = tmp_path / 'usage.sqlite3'
    ledger = CostLedger(path)
    with ledger.db() as db:
        for column in ('cached_input_tokens', 'cache_usage_status', 'cache_price_applied', 'input_estimate_details',
                       'input_rate', 'output_rate', 'cached_input_rate'):
            db.execute('ALTER TABLE calls DROP COLUMN ' + column)
        db.execute("INSERT INTO calls(id,session,turn_ref,provider,model,purpose,currency,day,created,reserved) VALUES('old','s','t','test','model','narrator','RUB','2026-10-17',0,8000)")
    ledger = CostLedger(path)
    old = ledger.summary()['calls'][0]
    assert old['status'] == 'interrupted' and old['cost'] == old['reserved'] == '0.008'
    assert old['cached_input_tokens'] is None and not old['cache_price_applied']
    llm, profile = configured(tmp_path / 'profile', lambda r: usage_response({}))
    profile.narrator.cached_input_per_million = Decimal('0.5')
    call, _ = ledger.reserve(profile, profile.narrator, dict(model='test-model', max_tokens=1000),
                             campaign=None, session='s', turn_ref='new', purpose='narrator', input_estimate=1000)
    result = ledger.finish(call, profile, profile.narrator, dict(prompt_tokens=1000, completion_tokens=200,
                                                               prompt_tokens_details=dict(cached_tokens=800)), dict(status='ok'))
    assert result['cost'] == '0.0016' and ledger.summary()['total'][0]['cost'] == '0.0096'
    asyncio.run(llm.close())


@pytest.mark.parametrize('cached_rate,expected_cost', [('0.5', '0.0038'), ('8', '0.0088')])
def test_multimodal_usage_does_not_assume_text_cache_price_applies_to_vision(tmp_path, cached_rate, expected_cost):
    from PIL import Image
    from solo.analysis_images import png_part
    llm, profile = configured(tmp_path, lambda r: usage_response(dict(prompt_tokens=1000, completion_tokens=200,
                                                                    prompt_tokens_details=dict(cached_tokens=800))))
    profile.pdf_analyst = profile.narrator.model_copy(deep=True)
    profile.pdf_analysis_enabled = True
    profile.pdf_analyst.image_per_million = Decimal(3)
    profile.pdf_analyst.cached_input_per_million = Decimal(cached_rate)
    async def run():
        try:
            async for _ in llm.stream('Анализ', [dict(type='text', text='Карта'), png_part(Image.new('RGB', (16, 16)))], mode='pdf_analyst'):
                pass
        finally:
            await llm.close()
    asyncio.run(run())
    row = llm.ledger.summary()['calls'][0]
    assert row['cached_input_tokens'] == 800 and not row['cache_price_applied']
    assert row['cost'] == expected_cost  # Worst configured input rate, no unverified vision discount.
    assert json.loads(row['input_estimate_details'])['image_tokens_estimate'] == IMAGE_ALLOWANCE


def test_context_estimate_and_native_schema_persist_for_audit(tmp_path):
    llm, _ = configured(tmp_path, lambda r: usage_response({}))
    collect(llm, grammar=dict(type='object', properties=dict(имя=dict(type='string'))))
    row = llm.ledger.summary()['calls'][0]
    estimate = json.loads(row['input_estimate_details'])
    assert estimate == llm.metrics[-1]['context_estimate']
    assert estimate['schema_tokens_estimate'] > 0 and estimate['image_tokens_estimate'] == 0
    assert estimate['input_tokens_estimate'] == row['input_tokens']


def test_fully_cached_input_and_free_cached_tariff_do_not_make_output_free(tmp_path):
    summary, _, _ = bill(tmp_path, dict(input_tokens=1000, output_tokens=200,
                                       input_tokens_details=dict(cached_tokens=1000)), cached_rate='0')
    assert summary['calls'][0]['cost'] == '0.0008'  # Only the reported cached INPUT is free.
    assert summary['total'][0]['input_tokens'] == summary['total'][0]['cached_input_tokens'] == 1000

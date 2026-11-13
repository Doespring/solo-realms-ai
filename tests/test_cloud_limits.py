"""r212: spends are aggregated in SQL, and a model stream has local caps.

The ledger tests seed rows directly (the reserved/finish paths are covered by
test_cloud_accounting.py) and compare the SQL aggregation against the previous
Python aggregation on exactly the same data.
"""
import asyncio
import json
import sqlite3
import tracemalloc
from decimal import Decimal

import httpx
import pytest

import solo.cloud as cloud
from solo.cloud import CloudUnavailable, stream_budget
from solo.cloud_cost import CostLedger
from test_cloud import configured, collect, sse

PURPOSES = ('narrator', 'interpreter', 'preparation_world', 'character_creation')
MODELS = (('routerai', 'deepseek-v4.1-flash'), ('routerai', 'qwen3.5-35b-a3b'), ('google', 'gemini-3.8-flash'))
STATUSES = ('ok', 'ok', 'error', 'reserved', 'cancelled')


def seed(path, rows=300):
    """Deterministic history: two currencies, several campaigns/turns/models."""
    ledger = CostLedger(path)
    base = 1793000000.0
    values = []
    for i in range(rows):
        provider, model = MODELS[i % len(MODELS)]
        status = STATUSES[i % len(STATUSES)]
        reserved = 100 + (i * 37) % 900
        values.append((
            'call-%05d' % i, None if i % 11 == 0 else 'c%d' % (i % 4), 's%d' % (i % 3), 'turn-%d' % (i % 40),
            provider, model, PURPOSES[i % len(PURPOSES)], 'RUB' if i % 5 else 'USD',
            '2026-11-0%d' % (1 + i % 9), base + i * 300, reserved,
            0 if status == 'reserved' else reserved + (i * 13) % 7000,
            'provider' if i % 4 else 'calculated_from_usage', status,
            (i * 91) % 4000, (i * 29) % 900, (i * 17) % 300, 'provider' if i % 4 else 'estimated',
            json.dumps({'input_tokens_estimate': (i * 47) % 5000}),
        ))
    con = sqlite3.connect(path)
    con.executemany(
        'INSERT INTO calls(id,campaign,session,turn_ref,provider,model,purpose,currency,day,created,reserved,cost,'
        'cost_source,status,input_tokens,output_tokens,cached_input_tokens,usage_source,input_estimate_details)'
        ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', values)
    con.commit()
    con.close()
    return ledger


def legacy_summary(ledger, campaign=None, session=None, turn_ref=None):
    """The implementation this card replaced, kept only as a reference oracle."""
    with ledger.db() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM calls ORDER BY created DESC')]

    def groups(values, keys):
        result = {}
        for r in values:
            key = tuple(r[k] for k in keys) + (r['currency'],)
            target = result.setdefault(key, dict(**{k: r[k] for k in keys}, currency=r['currency'], cost_micros=0, calls=0,
                                                 input_tokens=0, output_tokens=0, cached_input_tokens=0, estimated_calls=0))
            target['cost_micros'] += r['reserved'] if r['status'] == 'reserved' else r['cost']
            target['calls'] += 1
            target['input_tokens'] += r['input_tokens'] or 0
            target['output_tokens'] += r['output_tokens'] or 0
            target['cached_input_tokens'] += r['cached_input_tokens'] or 0
            target['estimated_calls'] += int(r['cost_source'] != 'provider')
        for v in result.values():
            v['cost'] = str(Decimal(v.pop('cost_micros')) / 1000000)
        return list(result.values())

    turns = groups([r for r in rows if r['campaign'] == campaign], ['turn_ref']) if campaign else []
    campaign_rows = [r for r in rows if r['campaign'] == campaign] if campaign else []
    current_ref = turn_ref or (campaign_rows[0]['turn_ref'] if campaign_rows else None)
    averages = []
    for currency in {r['currency'] for r in campaign_rows}:
        matching = [r for r in campaign_rows if r['currency'] == currency]
        turn_count = len({r['turn_ref'] for r in matching})
        total = sum(r['reserved'] if r['status'] == 'reserved' else r['cost'] for r in matching)
        averages.append(dict(currency=currency, turns=turn_count, average=str(Decimal(total) / 1000000 / turn_count)))

    def public_call(row):
        try:
            planned = json.loads(row['input_estimate_details']).get('input_tokens_estimate')
        except (ValueError, TypeError, AttributeError):
            planned = None
        from solo.cloud_cost import amount, token_count
        return dict(row, cost=amount(row['cost']), reserved=amount(row['reserved']), planned_input_tokens=token_count(planned),
                    actual_input_tokens=row['input_tokens'] if row['usage_source'] == 'provider' else None,
                    actual_output_tokens=row['output_tokens'] if row['usage_source'] == 'provider' else None)

    return dict(total=groups(rows, []), campaign=groups([r for r in rows if r['campaign'] == campaign], []) if campaign else [],
                session=groups([r for r in rows if r['session'] == session], []) if session else [],
                models=groups(rows, ['provider', 'model']), turns=turns,
                current_turn=groups([r for r in campaign_rows if r['turn_ref'] == current_ref], []),
                average_per_turn=averages, calls=[public_call(r) for r in rows[:100]])


def normalise(summary):
    def fixed(values, keys):
        return sorted(values, key=lambda r: tuple([str(r.get(k)) for k in keys] + [r.get('currency', '')]))

    return dict(total=fixed(summary['total'], []), campaign=fixed(summary['campaign'], []),
                session=fixed(summary['session'], []), models=fixed(summary['models'], ['provider', 'model']),
                turns=fixed(summary['turns'], ['turn_ref']), current_turn=fixed(summary['current_turn'], []),
                average_per_turn=fixed(summary['average_per_turn'], []),
                calls=sorted(summary['calls'], key=lambda r: r['id']))


@pytest.mark.parametrize('filters', [(), ('c1',), ('c1', 's1'), ('c1', 's1', 'turn-21'), ('c3', 's2', 'turn-99')])
def test_sql_summary_equals_previous_python_summary(tmp_path, filters):
    path = tmp_path / 'ledger.sqlite3'
    ledger = seed(path)
    legacy = legacy_summary(ledger, *filters)
    current = ledger.summary(*filters)
    assert normalise(current) == normalise(legacy)
    assert current['pending'] == []


def test_summary_keeps_legacy_order_of_groups(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    ledger = seed(path)
    summary = ledger.summary('c1')
    con = sqlite3.connect(path)
    latest = con.execute("SELECT turn_ref FROM calls WHERE campaign='c1' ORDER BY created DESC LIMIT 1").fetchone()[0]
    con.close()
    # Latest call first, exactly like the removed `ORDER BY created DESC` scan.
    assert summary['turns'][0]['turn_ref'] == latest


def test_summary_does_not_materialise_history(tmp_path):
    ledger = seed(tmp_path / 'ledger.sqlite3', rows=5000)
    ledger.summary('c1', 's1')  # warm schema and page cache, not measured
    tracemalloc.start()
    result = ledger.summary('c1', 's1')
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result['total'] and len(result['calls']) == 100
    # 5000 row dicts would be megabytes; the aggregate keeps ~100 public rows.
    assert peak < 1536 * 1024, peak


def test_open_creates_created_and_model_indexes(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    CostLedger(path)
    con = sqlite3.connect(path)
    con.execute('DROP INDEX IF EXISTS calls_created')
    con.execute('DROP INDEX IF EXISTS calls_model')
    con.commit()
    con.close()
    CostLedger(path)  # reopening is the migration
    con = sqlite3.connect(path)
    names = {row[1] for row in con.execute('PRAGMA index_list(calls)')}
    con.close()
    assert {'calls_created', 'calls_model'} <= names


def test_stream_budget_comes_from_role_max_tokens():
    profile = type('P', (), {'timeout_seconds': 90})()
    small, large = stream_budget(profile, 500), stream_budget(profile, 4000)
    assert small['bytes'] < large['bytes'] and small['seconds'] <= large['seconds']
    assert large['bytes'] == 4000 * cloud.STREAM_BYTES_PER_TOKEN + cloud.STREAM_BYTE_SLACK
    assert stream_budget(type('P', (), {'timeout_seconds': 240})(), 500)['seconds'] == 240


def test_stream_aborts_on_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, 'stream_budget', lambda p, t: dict(bytes=200, lines=10 ** 6, events=10 ** 6, seconds=60))
    frames = [{'choices': [{'delta': {'content': 'я' * 80}}]} for _ in range(5)]
    llm, _ = configured(tmp_path, lambda request: sse(frames))
    with pytest.raises(CloudUnavailable, match='потолок байтов'):
        collect(llm)
    metric = llm.metrics[-1]
    assert metric['status'] == 'error' and metric['error_code'] == 'stream_byte_limit'
    assert metric['stream_bytes'] > 200
    row = llm.ledger.summary()['calls'][0]
    assert row['status'] == 'error' and row['error_code'] == 'stream_byte_limit'


def test_stream_aborts_on_total_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, 'stream_budget', lambda p, t: dict(bytes=10 ** 6, lines=10 ** 6, events=10 ** 6, seconds=.05))

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"\xd0\xbf"}}]}\n\n'
            await asyncio.sleep(.25)
            yield b'data: [DONE]\n\n'

    llm, _ = configured(tmp_path, lambda request: httpx.Response(200, stream=Slow()))
    with pytest.raises(CloudUnavailable, match='дедлайн'):
        collect(llm)
    metric = llm.metrics[-1]
    assert metric['error_code'] == 'stream_deadline' and metric['stream_seconds'] >= .05


def test_stream_aborts_on_event_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, 'stream_budget', lambda p, t: dict(bytes=10 ** 6, lines=10 ** 6, events=1, seconds=60))
    frames = [{'choices': [{'delta': {'content': str(i)}}]} for i in range(4)]
    llm, _ = configured(tmp_path, lambda request: sse(frames))
    with pytest.raises(CloudUnavailable, match='лимит числа событий'):
        collect(llm)
    assert llm.metrics[-1]['error_code'] == 'stream_event_limit'


def test_crlf_sse_is_parsed_and_metrics_record_stream_size(tmp_path):
    body = ('data: {"choices":[{"delta":{"content":"Да"}}]}\r\n\r\n'
            'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}\r\n\r\n'
            'data: [DONE]\r\n\r\n').encode()
    llm, _ = configured(tmp_path, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body))
    assert collect(llm) == 'Да'
    metric = llm.metrics[-1]
    assert metric['status'] == 'ok' and metric['stream_bytes'] == len(body) and metric['stream_events'] == 3
    assert metric['stream_limits']['bytes'] >= len(body)


def test_non_streaming_body_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, 'stream_budget', lambda p, t: dict(bytes=100, lines=10 ** 6, events=10 ** 6, seconds=60))
    huge = {'choices': [{'message': {'content': 'я' * 500}}]}
    llm, _ = configured(tmp_path, lambda request: httpx.Response(200, json=huge), streaming=False)
    with pytest.raises(CloudUnavailable, match='потолок байтов'):
        collect(llm)
    assert llm.metrics[-1]['error_code'] == 'response_byte_limit'

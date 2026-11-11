"""Actual role/scene packets through mock HTTP; no live provider or user data."""
import asyncio
import json

import httpx
import pytest

from scripts.playable_ai_readiness import measure_packets, measure_native_packets, mock_cloud, saved_usage_summary
from solo.cloud import CloudUnavailable
from solo.cloud_cost import BudgetExceeded
from scripts.command_smoke import FixedDice, setup
from test_cloud import sse


@pytest.fixture(scope='module')
def measured(tmp_path_factory):
    return asyncio.run(measure_packets(tmp_path_factory.mktemp('wire')))


def test_real_packets_roles_schemas_context_and_estimates(measured):
    measurements = measured['wire_measurements']
    # Three resolvable command steps (a check and two rests that need no combat),
    # plus the legacy interpreter and narrator, plus the two limits packets.
    assert len(measurements) == 8 and measured['live_calls'] == 0
    commands = measurements[:3]
    for value in commands:
        estimate = value['planning']
        assert value['message_roles'] == ['system', 'user']
        assert estimate['text_utf8_bytes'] == value['system_utf8_bytes'] + value['user_utf8_bytes']
        assert value['schema_in_system'] and estimate['exact'] is False
        assert estimate['input_tokens_estimate'] + value['output_limit'] <= value['context_limit'] == 65536
        assert value['usage_kind'].startswith('synthetic')
        if value['role'] == 'interpreter':
            assert value['user_fields'] == ['context', 'player_intent', 'tools']
            assert value['context_fields'] == ['engine_commands', 'game_snapshot', 'memory_context', 'scene', 'sheet']
            assert set(value['tool_names']) == {'check','start_combat','move','attack','end_turn',
                                               'dash','short_rest','long_rest','end_combat','use_feature','native_action','level_up'}
            assert len(value['tool_names']) == 12
            assert value['prompt_version'] == 'gm_commands_v1'
            assert value['tool_schema_utf8_bytes'] > 2000
        else:
            assert value['user_fields'] == ['engine_commands', 'game_snapshot', 'memory_context', 'npc', 'outcomes', 'packet_version', 'scene', 'sheet']
            assert value['prompt_version'] == 'deepseek_narrator_v1' and not value['tool_names']
    assert {v['system_utf8_bytes'] for v in commands if v['role'] == 'interpreter'} == {commands[0]['system_utf8_bytes']}
    assert measurements[-2]['user_fields'] == ['реплика', 'сцена']
    assert '_solo_turn_response_limits' in measurements[-1]['user_fields']


def test_native_wizard_packets_include_bounded_capabilities_and_engine_outcomes(tmp_path):
    measurements = asyncio.run(measure_native_packets(tmp_path))
    assert len(measurements) == 6
    for value in measurements:
        assert value['planning']['exact'] is False
        assert value['planning']['input_tokens_estimate'] + value['output_limit'] <= value['context_limit']
        if value['role'] == 'interpreter':
            assert {'capabilities', 'encounters', 'scene', 'sheet', 'memory_context', 'game_snapshot'} <= set(value['context_fields'])
            assert 'native_action' in value['tool_names']
        else:
            assert {'outcomes', 'game_snapshot', 'capabilities'} <= set(value['user_fields'])
    assert measurements[-2]['label'] == 'native_native_action'


@pytest.mark.parametrize('spare,allowed', [(0, True), (-1, False)])
def test_real_command_packet_context_boundary_blocks_before_reserve_or_http(tmp_path, measured, spare, allowed):
    calls = []
    intent = dict(status='command', action=dict(kind='check', check_id='следы'), message='')
    def respond(request):
        calls.append(request)
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(intent, ensure_ascii=False)))])])
    llm, profile = mock_cloud(tmp_path, respond)
    store, cid, game = setup(tmp_path / 'game', llm=llm)
    initial = measured['wire_measurements'][0]
    profile.context_tokens = initial['planning']['input_tokens_estimate'] + initial['output_limit'] + spare
    async def run():
        try:
            if allowed:
                assert (await llm.interpret_command('Исследую следы', game.compiler.command_context(store.campaign(cid), query='Исследую следы'))).status == 'command'
            else:
                with pytest.raises(ValueError, match='контекста'):
                    await llm.interpret_command('Исследую следы', game.compiler.command_context(store.campaign(cid), query='Исследую следы'))
        finally:
            await llm.close()
    asyncio.run(run())
    assert len(calls) == int(allowed)
    assert len(llm.ledger.summary()['calls']) == int(allowed)


@pytest.mark.parametrize('case', ['usage', 'no_usage', 'empty', 'reasoning_only', 'http_error', 'broken_stream', 'not_sent'])
def test_reserve_actual_usage_empty_responses_and_provider_errors_are_distinct(tmp_path, case):
    calls = []
    usage = dict(prompt_tokens=500, completion_tokens=50, cost='0.002', currency='RUB')
    def respond(request):
        calls.append(request)
        if case == 'not_sent':
            raise httpx.ConnectError('synthetic', request=request)
        if case == 'http_error':
            return httpx.Response(502, json=dict(usage=usage))
        delta = dict(reasoning_content='Скрытое рассуждение') if case == 'reasoning_only' else dict(content='' if case == 'empty' else 'Русский ответ.')
        frames = [dict(choices=[dict(delta=delta)])]
        if case != 'no_usage':
            frames.append(dict(choices=[], usage=usage))
        return sse(frames, done=case != 'broken_stream')
    llm, _ = mock_cloud(tmp_path, respond)
    async def run():
        try:
            if case in ('usage', 'no_usage'):
                assert ''.join([c async for c in llm.stream('Роль', 'Короткий русский контекст')]) == 'Русский ответ.'
            else:
                with pytest.raises(CloudUnavailable):
                    ''.join([c async for c in llm.stream('Роль', 'Короткий русский контекст')])
        finally:
            await llm.close()
    asyncio.run(run())
    metric = llm.metrics[-1]
    row = llm.ledger.summary()['calls'][0]
    assert len(calls) == 1 and not llm.lock.locked()
    assert metric['planned_input_tokens'] == row['planned_input_tokens'] == metric['context_estimate']['input_tokens_estimate']
    assert metric['context_limit_tokens'] == 65536 and metric['output_limit_tokens'] == 600
    if case in ('no_usage', 'not_sent'):
        assert metric['actual_input_tokens'] is None and row['actual_input_tokens'] is None
        assert row['cost_source'] == ('not_sent' if case == 'not_sent' else 'reserved_estimate')
        assert row['cost'] == ('0' if case == 'not_sent' else row['reserved'])
    else:
        assert metric['actual_input_tokens'] == row['actual_input_tokens'] == 500
        assert metric['actual_output_tokens'] == row['actual_output_tokens'] == 50
        assert row['cost_source'] == 'provider' and row['cost'] == '0.002'
    assert metric['reserved_cost'] == row['reserved']


def test_cancel_preserves_reserve_releases_lock_and_next_call_works(tmp_path):
    class Paused(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True
    response = Paused()
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, stream=response) if len(calls) == 1 else sse([dict(choices=[dict(delta=dict(content='Готово.'))])])
    llm, _ = mock_cloud(tmp_path, respond)
    async def run():
        try:
            stream = llm.stream('Роль', 'Контекст')
            assert await anext(stream) == 'partial'
            await stream.aclose()
            assert response.closed and not llm.lock.locked()
            row = llm.ledger.summary()['calls'][0]
            assert row['status'] == 'cancelled' and row['cost'] == row['reserved']
            assert row['planned_input_tokens'] > 0 and row['actual_input_tokens'] is None
            assert ''.join([c async for c in llm.stream('Роль', 'Контекст')]) == 'Готово.'
        finally:
            await llm.close()
    asyncio.run(run())
    assert len(calls) == 2


def test_real_command_reservation_respects_zero_budget_without_http(tmp_path):
    calls = []
    llm, profile = mock_cloud(tmp_path, lambda r: calls.append(r) or sse([]))
    profile.max_request_cost = 0
    async def run():
        try:
            with pytest.raises(BudgetExceeded):
                await llm.interpret_command('Исследую следы', dict(memory_context={}, game_snapshot={}))
        finally:
            await llm.close()
    asyncio.run(run())
    assert not calls and not llm.ledger.summary()['calls']


def test_real_cloud_mock_cannot_turn_invented_prose_into_game_facts(tmp_path):
    calls = []
    def respond(request):
        body = json.loads(request.content)
        packet = json.loads(body['messages'][-1]['content'])
        calls.append(body)
        value = dict(status='command', action=dict(kind='check', check_id='следы'), message='') if 'player_intent' in packet else dict(
            description='Мирон нашёл мешок золота.', dialogue=[], proposals=[])
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(value, ensure_ascii=False)))]),
                    dict(choices=[], usage=dict(prompt_tokens=500, completion_tokens=50))])
    llm, _ = mock_cloud(tmp_path, respond)
    store, cid, game = setup(tmp_path / 'game', llm=llm)
    async def run():
        try:
            result = await game.play_command(cid, 'one', 'Исследую следы', 0, rng=FixedDice((13,)))
            assert result['status'] == 'resolved' and result['narrative'] == 'Действие удалось.'
            assert result['narrative_policy'] == 'engine_fallback'
            assert store.campaign(cid)['state']['facts'] == []
            replay = await game.play_command(cid, 'one', 'Исследую следы', 0, rng=FixedDice(()))
            assert replay['narrative'] == result['narrative'] and len(calls) == 2
            assert 'мешок золота' not in json.dumps(store.history(cid), ensure_ascii=False)
        finally:
            await llm.close()
    asyncio.run(run())


def test_saved_usage_reader_is_read_only_and_does_not_export_private_columns(tmp_path):
    llm, profile = mock_cloud(tmp_path, lambda r: sse([]))
    body = dict(model=profile.narrator.model, max_tokens=600)
    pending, _ = llm.ledger.reserve(profile, profile.narrator, body, campaign='private-campaign', session='s', turn_ref='private-turn', purpose='narrator', input_estimate=800)
    with llm.ledger.db() as db:
        db.execute('ALTER TABLE calls ADD COLUMN private_prompt TEXT')
        db.execute('UPDATE calls SET private_prompt=?', ('PRIVATE_PROMPT_AND_KEY_CANARY',))
    first = saved_usage_summary(llm.ledger.path)
    second = saved_usage_summary(llm.ledger.path)
    assert first == second and first['calls'] == 1 and first['provider_usage'] == 0
    assert first['paired_planning_and_actual'] == 0 and first['median_estimate_to_actual_ratio'] is None
    assert 'PRIVATE_PROMPT_AND_KEY_CANARY' not in json.dumps(first) and 'private-turn' not in json.dumps(first)
    assert llm.ledger.summary()['calls'][0]['status'] == 'reserved'  # A CostLedger reopen would alter it.
    asyncio.run(llm.close())

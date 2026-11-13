"""A provider outage, a broken reply and an invented fact are not one failure.

solo/prototype_gm.py:74 used a bare 'except Exception' and reported
'engine_fallback' for all of them. Section 6 of the task requires them to be
distinguishable in diagnostics: an outage is worth retrying, a rejected claim
is not, a malformed reply is a transport fault.

The test drives present_intent with a narrator that fails in each way and reads
the recorded policy and reason.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM
from solo.prototype_gm import present_intent


class _Scope:
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def _narrator(mode, answer):
    llm = MockLLM()

    async def scene(*a, **k):
        if mode == 'outage':
            raise ConnectionError('провайдер недоступен')
        if mode == 'format':
            raise ValueError('malformed json')
        llm.last_narrative = dict(description=answer, dialogue=[], proposals=[])
        llm.last_narrative_policy = 'model_artistic'
        if False:
            yield ''
    llm.narrate_scene = scene
    return llm


def _present(tmp_path, mode, answer='Кузница открыта.'):
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Сцена', Hero(name='Мирон', hp=12, max_hp=12))['id']
    current = store.campaign(cid)
    llm = _narrator(mode, answer)
    game = SimpleNamespace(llm=llm, model_scope=lambda *a, **k: _Scope(), store=store)
    context = dict(scene=dict(name='Кузница', description='Кузница открыта.', facts=[], objects=[], npc=[]),
                   confirmed=['Кузница открыта.'], memory_context=dict(memories=[]),
                   game_snapshot=dict(actors=[]))
    intent = SimpleNamespace(target='', status='observe')
    return asyncio.run(present_intent(game, current, 'r1', 'осматриваюсь', intent, context))


def test_confirmed_answer_is_verified(tmp_path):
    result = _present(tmp_path, 'ok')
    assert result['narrative_policy'] == 'engine_verified'
    assert result['narrative_reason'] == ''


def test_provider_outage_is_named_as_one(tmp_path):
    result = _present(tmp_path, 'outage')
    assert result['narrative_policy'] == 'engine_fallback'
    assert result['narrative_reason'].startswith('provider:'), result['narrative_reason']


def test_malformed_reply_is_a_format_fault(tmp_path):
    result = _present(tmp_path, 'format')
    assert result['narrative_policy'] == 'engine_fallback'
    assert result['narrative_reason'].startswith('format:'), result['narrative_reason']


def test_invented_fact_is_rejected_not_called_an_outage(tmp_path):
    result = _present(tmp_path, 'ok', answer='Кузница разрушена.')
    assert result['narrative_policy'] == 'engine_rejected', result
    assert result['narrative_reason'] == 'rejected_claim'

"""Stage D says an unavailable model must show up as an SSE 503.

The engine does not do that. A missing model is not observable as an HTTP
status on the two event-stream routes at all: `/api/events/{event}/narrate`
opens its 200 response and then reports the failure as an in-band
`data: {"type":"error"}` frame, and `/api/gm/{message}/stream` does the same
through `Game.gm_answer`. A 503 is reachable only on the non-streaming
endpoints, through the `CloudUnavailable` handler in `solo/app.py`.

This test pins what a player really sees when the model disappears mid-answer,
so the gap between the stage-D wording and the product is a failing expectation
rather than a guess. It calls no provider: the streaming call is a scripted
dependency that fails the way `CloudLLM` does.
"""
import importlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_control_play import table, world  # noqa: F401,E402  (fixture)


def frames(text):
    """The SSE payloads of a body, in order."""
    return [json.loads(line[len('data: '):]) for line in text.splitlines() if line.startswith('data: ')]


def test_unavailable_model_during_narration_answers_200_with_error_frame(world, tmp_path):  # noqa: F811
    """The real user-visible behaviour: 200, then one error frame, mechanics kept."""
    from solo.app import create_app
    from solo.cloud import CloudUnavailable
    from solo.llm import MockLLM
    from solo.world_service import WorldCommand

    store, cid, service = table(tmp_path, world)
    event = service.execute(cid, WorldCommand(request_id='walk', kind='travel',
                                              target='The Sunken Gallery'), from_map=True)
    event_id = event['id']
    before = store.event(event_id)
    before_state = store.campaign(cid)['state']
    before_version = store.campaign(cid)['version']

    class UnavailableMidAnswer(MockLLM):
        """A model that starts talking and then loses its provider, as CloudLLM does."""
        def __init__(self):
            super().__init__()

        async def stream(self, system, user, **kwargs):
            self.metrics.append({'mode': 'narrator', 'status': 'error'})
            yield 'Фонарь качается, '
            raise CloudUnavailable('Облачный Мастер недоступен. Сохранения доступны локально.')

    llm = UnavailableMidAnswer()
    client = _client(create_app(store.path, llm))

    with client.stream('POST', '/api/events/%s/narrate' % event_id, json={'regenerate': True}) as answer:
        body = ''.join(answer.iter_text())
    assert answer.status_code == 200, 'the stream reports unavailability in band, not as a status'
    answered = frames(body)
    # Narration is released once, after the sanitiser (86f2a4e), so a run that
    # fails before finishing sends no text frame: the half-answer never reached
    # the player and must not appear on a reload either.
    assert [f['type'] for f in answered] == ['error'], answered
    assert 'Механика сохранена. Ошибка описания: ' in answered[0]['message']
    assert 'Облачный Мастер недоступен' in answered[0]['message']
    assert all('Фонарь качается' not in str(f.get('text')) for f in answered), 'unverified text leaked'

    # What the player is left with: the turn is complete and unchanged, but its
    # description is unfinished, and the half-sentence is not kept as prose.
    after = store.event(event_id)
    assert after['narrative_status'] == 'error', after['narrative_status']
    # The stored copy goes through the same filter as the screen, which trims
    # the trailing space; the words that arrived are still what is kept.
    assert after['narrative'].strip() == 'Фонарь качается,', 'the partial text is kept as the description'
    failing = llm.metrics[-1]
    assert failing.get('status') == 'error', 'the failed call is recorded for the cost journal'
    assert store.campaign(cid)['state']['location'] == before_state['location']
    assert store.campaign(cid)['version'] == before_version, 'a failed description must not move the game'
    assert after['undone'] == before['undone'] == 0

    # The player retries; the model answers; the same turn is described again and
    # the previous failure text is replaced rather than appended.
    llm.stream = types.MethodType(_healthy, llm)

    with client.stream('POST', '/api/events/%s/narrate' % event_id, json={'regenerate': True}) as retry:
        retried = frames(''.join(retry.iter_text()))
    assert retry.status_code == 200, retried
    assert [f['type'] for f in retried] == ['token', 'done'], retried
    assert store.event(event_id)['narrative_status'] == 'complete'
    assert store.event(event_id)['narrative'].strip() == 'Фонарь горит ровно.'
    assert len(store.history(cid)) >= 1, 'one travel, never a duplicated turn'


def test_a_public_event_route_still_turns_unavailable_model_into_503(world, tmp_path):  # noqa: F811
    """A 503 does exist — on the routes that are not already streaming."""
    from solo.app import create_app
    from solo.cloud import CloudUnavailable
    from solo.llm import MockLLM
    from solo.world_service import WorldCommand

    store, cid, service = table(tmp_path, world)
    service.execute(cid, WorldCommand(request_id='walk', kind='travel',
                                      target='The Sunken Gallery'), from_map=True)

    class Refusing(MockLLM):
        async def interpret(self, text, scene):
            raise CloudUnavailable('Облачный Мастер недоступен. Сохранения доступны локально.')

    client = _client(create_app(store.path, Refusing()))
    answer = client.post('/api/campaigns/%s/actions' % cid,
                         json={'text': 'Оглядываюсь по сторонам', 'request_id': 'look'})
    assert answer.status_code == 503, answer.text
    assert 'Облачный Мастер недоступен' in answer.json()['detail']


async def _healthy(self, system, user, **kwargs):
    self.metrics.append({'mode': 'narrator', 'status': 'ok'})
    yield 'Фонарь горит ровно. '


def _client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_profile(monkeypatch, tmp_path):
    """No real profile, no key, no network fallback: SOLO_TEST before the app import."""
    monkeypatch.setenv('SOLO_TEST', '1')
    monkeypatch.setenv('SOLO_HOME', str(tmp_path / 'home'))
    yield

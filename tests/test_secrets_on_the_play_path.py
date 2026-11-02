"""Section 4 through a resolved turn: the narrator is really asked.

The first version of this test looked green while the narrator had never been
called at all: the scripted intent missed the key, the turn stopped at
clarification (solo/game.py:559-566) and no event was written, so the narrator
branch (solo/game.py:739-747) was unreachable. A filter that is never reached
cannot prove anything. Here the turn is resolved first, and only then is the
silence of the secrets worth reading.
"""
import os
os.environ['SOLO_TEST']='1'

import json
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine

SECRET = 'За стеной спрятан тайный ключ.'
NPC_SECRET = 'Ирина держит ключ в кармане.'


class Recorder(CommandMockLLM):
    def __init__(self, answer):
        super().__init__({'intent': answer})
        self.calls = []

    async def stream(self, system, user, **kwargs):
        # The narrator is not sent a mode; it is recognised by its contract.
        self.calls.append(dict(mode='narrator' if kwargs.get('narrative_contract') else kwargs.get('mode'),
                               system=system, user=user))
        async for chunk in super().stream(system, user, **kwargs):
            yield chunk


def _app(tmp_path):
    model = Recorder(dict(status='command', action=dict(kind='check', check_id='perception'), message=''))
    app = create_app(tmp_path / 'secrets.sqlite3', model)
    store = app.state.store
    app.state.game.llm = model
    # A real Nat20 campaign: the check the scripted intent asks for is registered
    # by the engine, so the turn can actually be resolved and the narrator reached.
    cid = Nat20GameEngine(store).create_campaign('Тайна', seed=5)['id']
    state = json.loads(json.dumps(store.campaign(cid)['state']))
    places = state['locations']
    home = places[0]['name']
    for place in places:
        if place['name'] == home:
            place['sealed_text'] = SECRET
            place['secrets'] = [SECRET]
            place['npc'] = [dict(name='Ирина', character='Смотрительница.',
                                 description='Смотрительница.', secrets=[NPC_SECRET],
                                 private_knowledge=[NPC_SECRET])]
    with store.connection(True) as c:
        c.execute('update campaigns set state=? where id=?', (json.dumps(state, ensure_ascii=False), cid))
    return app, store, cid, model


def _resolved_turn(client, store, cid):
    return client.post('/api/campaigns/%s/command-turn' % cid, json=dict(
        request_id='intent', text='intent', expected_version=store.campaign(cid)['version']))


def test_a_resolved_turn_really_asks_the_narrator(tmp_path, monkeypatch):
    monkeypatch.setattr('solo.nat20_api.secrets.randbits', lambda bits: 5)
    app, store, cid, model = _app(tmp_path)
    turn = store.campaign(cid)['state']['turn']
    with TestClient(app) as client:
        response = _resolved_turn(client, store, cid)
        assert response.status_code == 200, response.text
        body = response.json()
    assert body['status'] == 'resolved', body
    assert body['events'], 'a resolved turn must write an event'
    assert store.campaign(cid)['state']['turn'] == turn + 1
    modes = [call['mode'] for call in model.calls]
    assert 'interpreter' in modes, modes
    assert 'narrator' in modes, 'the narrator was never asked: ' + str(modes)


def test_neither_role_receives_a_secret_on_a_resolved_turn(tmp_path, monkeypatch):
    monkeypatch.setattr('solo.nat20_api.secrets.randbits', lambda bits: 5)
    app, store, cid, model = _app(tmp_path)
    with TestClient(app) as client:
        response = _resolved_turn(client, store, cid)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'resolved', response.json()
        seen = response.text
        screen = client.get('/api/campaigns/' + cid).text
    for call in model.calls:
        assert 'тайный ключ' not in call['user'], call['mode']
        assert 'ключ в кармане' not in call['user'], call['mode']
    for packet in model.packets:
        assert 'тайный ключ' not in json.dumps(packet, ensure_ascii=False), packet['mode']
        assert 'ключ в кармане' not in json.dumps(packet, ensure_ascii=False), packet['mode']
    assert 'тайный ключ' not in seen and 'ключ в кармане' not in seen
    assert 'тайный ключ' not in screen and 'ключ в кармане' not in screen


def test_the_scene_itself_still_reaches_the_roles_and_the_player(tmp_path, monkeypatch):
    monkeypatch.setattr('solo.nat20_api.secrets.randbits', lambda bits: 5)
    app, store, cid, model = _app(tmp_path)
    with TestClient(app) as client:
        response = _resolved_turn(client, store, cid)
        assert response.json().get('status') == 'resolved', response.json()
        screen = client.get('/api/campaigns/' + cid).text
    # The scene is the real prepared place, not a string this test invented:
    # it must reach both roles and the player, while its secret does not.
    state = app.state.store.campaign(cid)['state']
    home = state['location']
    description = next(p for p in state['locations'] if p['name'] == home)['description']
    scene_word = description.split()[0].strip('.,;:')
    assert scene_word and scene_word in screen, 'the screen lost the place description'
    assert any(scene_word in call['user'] for call in model.calls), 'the roles lost the scene'
    assert any(call['mode'] == 'narrator' for call in model.calls), 'no narrator call to check'
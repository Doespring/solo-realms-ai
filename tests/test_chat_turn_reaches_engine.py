"""r136: a chat line in a Nat20 campaign reaches the engine, and only there.

The chat screen (?ui=chat) has one route for a typed phrase. A Nat20 campaign
has exactly one interpreter, so its route is ``command-turn``; the legacy
``play`` handler refuses those campaigns with 409 ``command_backend_required``
by design and that refusal is asserted here as a guard, not worked around.

The last test reads the client source: the branch is chosen from the campaign's
own ``backend`` field (the same field the server projects in combat_view), and
the body carries ``request_id``/``text``/``expected_version`` — the strict
``CommandTurnRequest`` contract, no legacy ``selection``.
"""
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store

ROOT = Path(__file__).resolve().parents[1]

# The mock interpreter is keyed by the exact player utterance and performs no
# network call: the phrase below is what the browser check types as well.
TEXT = 'Осматриваю калитку'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect

    def local_only(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)  # Windows asyncio wakeup socket.
        raise AssertionError('Paid/external APIs are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local_only)


@pytest.fixture
def native(tmp_path):
    store = Store(tmp_path / 'chat-turn.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Чат · Мирон', seed=5)['id']
    model = CommandMockLLM({TEXT: dict(status='command', action=dict(kind='check', check_id='perception'),
                                      message='')})
    return store, cid, create_app(store.path, model)


def turn(client, cid, request_id, version, text=TEXT):
    return client.post(f'/api/campaigns/{cid}/command-turn',
                       json=dict(request_id=request_id, text=text, expected_version=version))


def test_chat_line_resolves_through_command_turn(native):
    store, cid, app = native
    with TestClient(app) as client:
        before = client.get(f'/api/campaigns/{cid}').json()
        # The client picks its route from this projection, so it is asserted here.
        assert before['backend'] == 'nat20'
        version = before['version']

        response = turn(client, cid, 'chat-turn-1', version)
        assert response.status_code == 200, response.text
        reply = response.json()
        assert reply['status'] == 'resolved', reply
        assert reply['state_version'] == version + 1
        assert reply['events'][0]['id'] == 'nat20:chat-turn-1'
        assert reply['narrative']

        after = client.get(f'/api/campaigns/{cid}').json()
        assert after['version'] == version + 1
        assert len(after['history']) == 1
        row = after['history'][0]
        # The feed renders the player's line from the journal's original text and
        # the master's line from the narrative: both must survive the engine turn.
        assert row['original'] == TEXT
        assert row['narrative']
        assert not row['undone']
    assert store.campaign(cid)['version'] == version + 1


def test_command_turn_body_is_the_strict_contract(native):
    _, cid, app = native
    with TestClient(app) as client:
        version = client.get(f'/api/campaigns/{cid}').json()['version']
        # The legacy extra field is refused, which is why the client must not send it.
        response = client.post(f'/api/campaigns/{cid}/command-turn',
                               json=dict(request_id='chat-turn-strict', text=TEXT,
                                         expected_version=version, selection=None))
        assert response.status_code == 422, response.text


def test_the_removed_play_route_is_gone_and_the_guard_still_refuses_it(native):
    """r210: маршрута ``/play`` больше нет — единственный вход в ход это Nat20.

    Маршрут удалён из приложения (его нет в ``app.routes``), поэтому ручного
    расчёта по HTTP не существует вовсе. Запрос на прежний адрес ловит страж
    одного калькулятора и получает тот же 409 ``command_backend_required``, что
    и любой другой неигровой путь Nat20-кампании: ни хода, ни записи в журнал.
    """
    store, cid, app = native
    assert '/api/campaigns/{campaign}/play' not in {getattr(r, 'path', '') for r in app.routes}
    version = store.campaign(cid)['version']
    with TestClient(app) as client:
        response = client.post(f'/api/campaigns/{cid}/play',
                               json=dict(request_id='chat-legacy-1', text=TEXT, selection=None))
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'command_backend_required'
    # A removed route commits nothing: the one backend is still the only writer.
    assert store.campaign(cid)['version'] == version
    assert store.history(cid) == []


def test_the_same_line_replayed_does_not_take_two_turns(native):
    store, cid, app = native
    with TestClient(app) as client:
        version = client.get(f'/api/campaigns/{cid}').json()['version']
        first = turn(client, cid, 'chat-turn-idem', version)
        assert first.status_code == 200 and first.json()['status'] == 'resolved', first.text
        # A lost reply makes the player press send again with the stored id and the
        # version it already saw: the engine answers from the journal, not twice.
        again = turn(client, cid, 'chat-turn-idem', version)
        assert again.status_code == 200, again.text
        assert again.json()['status'] == 'resolved', again.json()
        assert again.json()['journal_id'] == first.json()['journal_id']
    assert store.campaign(cid)['version'] == version + 1
    assert len(store.history(cid)) == 1


def test_client_picks_command_turn_from_the_campaign_backend():
    play = (ROOT / 'web' / 'play.js').read_text(encoding='utf-8')
    # r210: у кампании один интерпретатор — command-turn Nat20. Прежний
    # маршрут /play удалён и из сервера, и из клиента: ветки больше нет,
    # а состояние без движка клиент даже не пытается отправить.
    assert "api('/campaigns/'+campaignId+'/play'" not in play, 'the removed route is still called'
    assert play.count("api('/campaigns/'+campaignId+'/command-turn',") == 1
    assert "if(current.backend!=='nat20')throw new Error(" in play
    # Nat20 body = the strict CommandTurnRequest: no legacy selection, version from state.
    # Sliced out of the shipped file, not out of the literal asserted above.
    native_body = play.split("/command-turn',{", 1)[1].split('})', 1)[0]
    assert 'request_id:pendingSend.id' in native_body
    assert 'expected_version:current.version' in native_body
    assert 'selection' not in native_body
    # A refused turn is echoed only when neither the journal nor the conversation log
    # has a row for this request_id — a status name cannot tell (a stored scene turn
    # reports 'observation'). Matching the reply by text silenced the second
    # identical refusal, so that guard must be gone.
    assert 'row.request_id===requestId' in play and 'recordedInFeed(sentId)' in play
    assert 'textContent.includes(reply)' not in play


def test_a_narrative_turn_is_reported_as_observation(tmp_path):
    """The client's stored set names 'observation': a scene look is not 'resolved'.

    present_intent() answers an observe intent with status 'observation' and
    records the turn under its own journal id, so the feed renders it and the
    client must not echo it. The same id replayed after a lost reply returns the
    recorded answer instead of taking a second turn.
    """
    store = Store(tmp_path / 'chat-observe.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Чат · Мирон', seed=5)['id']
    model = CommandMockLLM({TEXT: dict(status='observe', target='')})
    with TestClient(create_app(store.path, model)) as client:
        version = client.get(f'/api/campaigns/{cid}').json()['version']
        first = turn(client, cid, 'chat-observe-1', version)
        assert first.status_code == 200, first.text
        assert first.json()['status'] == 'observation', first.json()['status']
        assert first.json()['narrative']
        after = client.get(f'/api/campaigns/{cid}').json()
        assert len(after['history']) == 1 and after['history'][0]['original'] == TEXT
        written = len(after['conversation'])
        again = turn(client, cid, 'chat-observe-1', version)   # stale version on purpose
        assert again.status_code == 200, again.text
        assert again.json()['status'] == 'observation', again.json()['status']
        repeated = client.get(f'/api/campaigns/{cid}').json()
        assert len(repeated['history']) == 1
        assert len(repeated['conversation']) == written

"""r158: a reviewed book binds to a campaign started on the Nat20 screen.

The preparation layer (``attach``/``continue``) writes one scripted journal
event and never resolves an attack, a check or a rest, so a Nat20 campaign
accepts it. Every mechanical route keeps going through the Nat20 screen alone:
the legacy ``play`` handler still refuses those campaigns with 409
``command_backend_required``.
"""
import socket

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine

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
    store_app = create_app(tmp_path / 'r158.sqlite3',
                           CommandMockLLM({TEXT: dict(status='command', action=dict(kind='check', check_id='perception'),
                                                      message='')}))
    cid = Nat20GameEngine(store_app.state.store).create_campaign('Nat20 · Мирон', seed=5)['id']
    return store_app, cid


def modules(library, tmp_path):
    """Two reviewed books: this one and the next one of the same series."""
    from test_adventure_continuity import book, definition
    bundle, document = book(library, tmp_path, 'Prepared one')
    library.save_definition(bundle['id'], definition('one', document), True)
    following, next_document = book(library, tmp_path, 'Prepared two')
    library.save_definition(following['id'], definition('two', next_document), True)
    return bundle['id'], following['id']


def test_a_reviewed_book_attaches_and_continues_on_a_nat20_campaign(native, tmp_path):
    app, cid = native
    store = app.state.store
    bundle, following = modules(app.state.adventures, tmp_path)
    url = '/api/adventure/review/campaigns/' + cid
    with TestClient(app) as client:
        # The preparation screen is closed: the answer is access, never a 409
        # about a second mechanics backend.
        closed = client.post(url + '/attach', json=dict(bundle=bundle, request_id='r158-closed', confirm=True))
        assert closed.status_code == 403, closed.text
        assert client.post('/api/adventure/review/session', json=dict(confirm_spoilers=True)).status_code == 200
        attached = client.post(url + '/attach', json=dict(bundle=bundle, request_id='r158-attach', confirm=True))
        assert attached.status_code == 200, attached.text
        state = store.campaign(cid)['state']
        assert state['adventure']['module'] == 'one'
        assert attached.json()['id']
        # The book is bound to the campaign that the Nat20 screen started: its
        # hero, sheet and engine state were not replaced by the attach.
        assert state['hero']['level'] == 1 and 'nat20' in state
        moved = client.post(url + '/continue', json=dict(bundle=following, module='two', request_id='r158-continue', confirm=True))
        assert moved.status_code == 200, moved.text
        assert store.campaign(cid)['state']['adventure']['module'] == 'two'
        # Mechanics still resolve through the Nat20 screen and nowhere else.
        version = store.campaign(cid)['version']
        turn = client.post('/api/campaigns/' + cid + '/command-turn',
                           json=dict(request_id='r158-turn', text=TEXT, expected_version=version))
        assert turn.status_code == 200, turn.text
        assert turn.json()['events'][0]['id'].startswith('nat20:')
        # r210: прежнего маршрута хода нет вовсе — единственный вход Nat20;
        # запрос на старый адрес ловит страж одного калькулятора.
        legacy = client.post('/api/campaigns/' + cid + '/play', json=dict(request_id='r158-play', text=TEXT))
        assert legacy.status_code == 409, legacy.text
        assert legacy.json()['detail']['code'] == 'command_backend_required'
        # The exception is exactly two routes, not every review route: a review
        # route that is not preparation is still refused with the one-backend 409.
        other = client.post(url + '/encounter-completion', json=dict(encounter='e1', request_id='r158-other'))
        assert other.status_code == 409, other.text
        assert other.json()['detail']['code'] == 'command_backend_required'


def test_a_scenario_that_opens_with_a_roll_waits_for_the_nat20_screen(native, tmp_path):
    """The preparation layer must not roll the legacy dice for a Nat20 campaign.

    The reviewed definition starts with a printed check that is due immediately.
    On a campaign started on the Nat20 screen the effect is recorded as pending
    (no dice, no flag, nothing spent); on a legacy campaign the same definition
    still resolves it, so the guard narrowed nothing for the older path.
    """
    from solo.contracts import Hero
    from solo.rulesets import BUILTINS
    from test_adventure_continuity import book, definition

    app, cid = native
    store = app.state.store
    bundle, document = book(app.state.adventures, tmp_path, 'Opening roll')
    event = dict(category='timed', id='gate', after_minutes=0, source=dict(document=document, page=1, review='confirmed'),
                 effects=[dict(kind='check', ability='wisdom', dc=12, target='gate_open', text='Замечен ли след у калитки')])
    app.state.adventures.save_definition(bundle['id'], definition('opening', document, events=[event]), True)
    plain = store.create('Обычная', Hero(name='Лира'), BUILTINS['dnd2024'])['id']
    with TestClient(app) as client:
        assert client.post('/api/adventure/review/session', json=dict(confirm_spoilers=True)).status_code == 200
        body = dict(bundle=bundle['id'], request_id='r158-opening', confirm=True)
        attached = client.post('/api/adventure/review/campaigns/%s/attach' % cid, json=body)
        assert attached.status_code == 200, attached.text
        native_state = store.campaign(cid)['state']['adventure']
        record = native_state['journal'][-1]
        assert record['id'] == 'gate' and record['rolls'] == [], record
        assert 'gate_open' not in native_state['flags'], native_state['flags']
        assert native_state['deferred'] == [dict(event='gate', kind='check', target='gate_open',
                                                 text='Замечен ли след у калитки', page=1)]
        assert 'ожидает игрового экрана Nat20' in attached.text, attached.text
        # r210: прежнего пути нет вовсе — книга не подключается к кампании
        # без движка Nat20, поэтому «как раньше» бросок не выполняется.
        older = client.post('/api/adventure/review/campaigns/%s/attach' % plain, json=body)
        assert older.status_code == 409, older.text
        assert older.json()['detail']['code'] == 'legacy_campaign_unsupported'
        assert 'adventure' not in store.campaign(plain)['state']

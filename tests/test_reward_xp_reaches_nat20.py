"""r167: experience printed in the book reaches the counter Nat20 owns.

r159 closed the boundary "the printed reward is booked, the hero does not grow".
This test proves the other half: a paid reward becomes one confirmed receipt in
the engine's own experience ledger, the hero sheet shows the new total in the
same turn, a repeated turn and a replayed queue pay nothing twice, a reward
without a price breaks nothing, and enough experience opens the existing
advancement path instead of the scenario writing a level itself.
"""
import copy
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


def app_for(tmp_path, name='r167.sqlite3', experimental=False):
    return create_app(tmp_path / name,
                      CommandMockLLM({TEXT: dict(status='command', action=dict(kind='check', check_id='perception'),
                                                 message='')}),
                      experimental=experimental, offline_mock=experimental)


@pytest.fixture
def native(tmp_path):
    app = app_for(tmp_path)
    cid = Nat20GameEngine(app.state.store).create_campaign('Nat20 · Р167', seed=11)['id']
    return app, cid


def reviewed_module(app, tmp_path, rule, definition_id='opening', event_id='purse', title='Reward xp'):
    """One reviewed module whose opening pays one printed reward."""
    from test_adventure_continuity import book, definition
    bundle, document = book(app.state.adventures, tmp_path, title)
    event = dict(category='timed', id=event_id, after_minutes=0,
                 source=dict(document=document, page=3, review='confirmed'),
                 effects=[dict(kind='reward', target=rule['id'], text='Награда за находку')])
    app.state.adventures.save_definition(
        bundle['id'], definition(definition_id, document, events=[event], tables=[],
                                 progression=dict(mode='experience', rewards=[rule])), True)
    return bundle['id']


def attach(app, cid, bundle):
    with TestClient(app) as client:
        assert client.post('/api/adventure/review/session', json=dict(confirm_spoilers=True)).status_code == 200
        attached = client.post('/api/adventure/review/campaigns/%s/attach' % cid,
                               json=dict(bundle=bundle, request_id='r167-attach', confirm=True))
    assert attached.status_code == 200, attached.text
    return attached


def turn(app, cid, request_id):
    version = app.state.store.campaign(cid)['version']
    with TestClient(app) as client:
        return client.post('/api/campaigns/%s/command-turn' % cid,
                           json=dict(request_id=request_id, text=TEXT, expected_version=version))


def notices(app, cid, request_id):
    store = app.state.store
    action = store.request(cid, request_id)
    results = store.event_for_action(action['id'])['results']
    return [item['text'] for item in results[-1].get('adventure_events') or []]


def test_a_printed_reward_pays_the_nat20_counter(native, tmp_path):
    app, cid = native
    store = app.state.store
    attach(app, cid, reviewed_module(app, tmp_path,
                                     dict(id='purse-xp', amount=50, page=3, reason='Награда напечатана в книге')))
    before = store.campaign(cid)['state']
    # Nothing is paid on the preparation route: the reward waits for the screen.
    assert before['nat20']['experience'] == 0 and before['nat20']['rewards'] == []
    assert [item['kind'] for item in before['adventure']['deferred']] == ['reward']

    rolled = turn(app, cid, 'r167-turn-1')
    assert rolled.status_code == 200, rolled.text

    after = store.campaign(cid)['state']
    # The engine's own counter moved, and it moved by a confirmed receipt of its
    # own ledger - not by a second counter written beside it.
    assert after['nat20']['experience'] == 50
    receipt = after['nat20']['rewards'][0]
    assert (receipt['id'], receipt['experience'], receipt['kind'], receipt['page']) == (
        'printed:opening:purse-xp', 50, 'printed', 3)
    assert receipt['encounter_id'] == '' and len(receipt['outcome_sha256']) == 64
    # The legacy hero is a projection of that counter, so the sheet agrees in
    # the very turn that paid.
    assert after['hero']['experience'] == 50
    words = ' '.join(notices(app, cid, 'r167-turn-1'))
    assert 'Опыт Nat20: 50 (+50, стр. 3)' in words, words
    # The state the next screen turn loads validates against the same contract
    # the worker uses; a receipt the ledger does not recognise would fail here.
    from solo.nat20_contracts import DurableState
    assert DurableState.model_validate(after['nat20']).experience == 50


def test_a_repeated_turn_and_a_replayed_queue_pay_once(native, tmp_path):
    app, cid = native
    store = app.state.store
    attach(app, cid, reviewed_module(app, tmp_path,
                                     dict(id='purse-xp', amount=50, page=3, reason='Награда напечатана в книге')))
    queued = copy.deepcopy(store.campaign(cid)['state']['adventure']['deferred'])
    assert len(queued) == 1
    assert turn(app, cid, 'r167-turn-1').status_code == 200

    again = turn(app, cid, 'r167-turn-2')
    assert again.status_code == 200, again.text
    repeated = store.campaign(cid)['state']
    assert repeated['nat20']['experience'] == 50
    assert [r['id'] for r in repeated['nat20']['rewards']] == ['printed:opening:purse-xp']
    assert repeated['adventure']['rewards']['purse-xp']['status'] == 'applied'

    # A stale copy of the queue, exactly what a repeated tick would hand over.
    from solo.adventure_engine import resolve_deferred
    replay = copy.deepcopy(repeated)
    replay['adventure']['deferred'] = copy.deepcopy(queued)
    assert resolve_deferred(replay) == []
    assert replay['nat20']['experience'] == 50
    assert [r['id'] for r in replay['nat20']['rewards']] == ['printed:opening:purse-xp']


def test_a_reward_without_a_price_breaks_nothing(native, tmp_path):
    app, cid = native
    store = app.state.store
    attach(app, cid, reviewed_module(app, tmp_path,
                                     dict(id='unpriced-xp', page=4, reason='Число не подтверждено источником')))
    assert turn(app, cid, 'r167-turn-1').status_code == 200
    state = store.campaign(cid)['state']
    # An unpriced reward stays honestly pending: no zero receipt, no counter.
    assert state['adventure']['rewards']['unpriced-xp']['status'] == 'pending_adaptation'
    assert state['nat20']['experience'] == 0 and state['nat20']['rewards'] == []
    assert state['adventure']['deferred'] == []
    assert turn(app, cid, 'r167-turn-2').status_code == 200
    assert store.campaign(cid)['state']['nat20']['experience'] == 0


def test_earned_experience_opens_the_existing_advancement_path(tmp_path):
    """The scenario pays experience; the level comes from the host's own path."""
    from nat20_builds import legal
    from solo.progression import Progression, RewardRule

    rule = dict(id='episode-xp', amount=300, page=42, reason='Опыт эпизода напечатан в книге')
    app = app_for(tmp_path, 'r167-level.sqlite3', experimental=True)
    store = app.state.store
    build = legal(class_slug='fighter', species_slug='human', background='soldier')
    cid = Nat20GameEngine(store).create_experimental_campaign(
        'Лира', build=build, encounters={}, checks=None, prepared_spells=(), seed=3,
        title='Reward xp', description='d',
        progression=Progression(mode='experience', rewards=[RewardRule.model_validate(rule)]))['id']
    with TestClient(app) as api:
        idle = api.get('/api/campaigns/' + cid + '/level-up').json()
    assert idle['available'] is False and idle['earned'] == 1

    attach(app, cid, reviewed_module(app, tmp_path, rule))
    assert turn(app, cid, 'r167-level-turn').status_code == 200
    paid = store.campaign(cid)['state']
    assert paid['nat20']['experience'] == 300
    # No level is written by the scenario: the hero is still first level.
    assert paid['nat20']['definition']['build']['level'] == 1

    with TestClient(app) as api:
        offer = api.get('/api/campaigns/' + cid + '/level-up').json()
        assert offer['available'] is True and offer['to_level'] == 2, offer
        done = api.post('/api/campaigns/' + cid + '/level-up', json=dict(
            request_id='r167-level-2', expected_version=offer['version'], draft=offer['draft']))
        assert done.status_code == 200, done.text
        assert done.json()['to_level'] == 2
    committed = store.campaign(cid)['state']
    assert committed['nat20']['definition']['build']['level'] == 2
    # Advancement spends no experience: the ledger still pays it once.
    assert committed['nat20']['experience'] == 300
    assert [r['id'] for r in committed['nat20']['rewards']] == ['printed:opening:episode-xp']


def test_two_modules_printing_the_same_rule_id_each_pay(native, tmp_path):
    """A printed rule id belongs to its module, so the next episode pays again.

    The scenario ledger is per module and is replaced when the campaign continues,
    while the Nat20 reward ledger outlives the module. Keying a receipt by the rule
    id alone would let the second episode's line hit the first episode's receipt:
    the scenario would book it as applied and the hero would get nothing.
    """
    from solo.adventure_engine import AdventureService
    app, cid = native
    store = app.state.store
    rule = dict(id='episode-xp', amount=50, page=3, reason='Опыт эпизода напечатан в книге')
    first_bundle = reviewed_module(app, tmp_path, rule, definition_id='episode-1', title='Episode one')
    second_bundle = reviewed_module(app, tmp_path, rule, definition_id='episode-2', title='Episode two')
    attach(app, cid, first_bundle)
    assert turn(app, cid, 'r167-m1-turn').status_code == 200
    assert store.campaign(cid)['state']['nat20']['experience'] == 50

    AdventureService(store, app.state.adventures).continue_module(
        cid, second_bundle, None, 'r167-continue', True)
    carried = store.campaign(cid)['state']
    # The preparation route pays nothing: the second episode waits for the screen.
    assert carried['nat20']['experience'] == 50
    assert [r['id'] for r in carried['nat20']['rewards']] == ['printed:episode-1:episode-xp']

    assert turn(app, cid, 'r167-m2-turn').status_code == 200
    paid = store.campaign(cid)['state']
    assert paid['nat20']['experience'] == 100
    assert [r['id'] for r in paid['nat20']['rewards']] == ['printed:episode-1:episode-xp',
                                                           'printed:episode-2:episode-xp']
    assert sum(r['experience'] for r in paid['nat20']['rewards']) == paid['nat20']['experience']

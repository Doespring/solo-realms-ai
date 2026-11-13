"""r159: scenario effects of a Nat20 campaign are executed, not left pending.

A preparation route (attach/continue) writes a printed check, table, resource or
reward as pending because the legacy ruleset must never roll for a campaign
that keeps its mechanics on the Nat20 screen. This test proves the other half:
the next screen turn drains that queue inside the same transaction, rolls the
check with Nat20's own resolver and prints its numbers, throws the table, books
the reward once and pays its experience into the engine's own counter, and does
not repeat itself on a second turn.
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


@pytest.fixture
def native(tmp_path):
    app = create_app(tmp_path / 'r159.sqlite3',
                     CommandMockLLM({TEXT: dict(status='command', action=dict(kind='check', check_id='perception'),
                                                message='')}))
    cid = Nat20GameEngine(app.state.store).create_campaign('Nat20 · Р159', seed=11)['id']
    return app, cid


def reviewed_book(app, tmp_path, title='Pending effects', events=None, tables=None, progression=None):
    """One reviewed module; by default its opening prints a check, a table and a counter."""
    from test_adventure_continuity import book, definition
    bundle, document = book(app.state.adventures, tmp_path, title)
    evidence = dict(document=document, page=1, review='confirmed')
    gate = dict(category='timed', id='gate', after_minutes=0, source=evidence,
                effects=[dict(kind='check', ability='wisdom', dc=12, target='gate_open',
                              text='Замечен ли след у калитки')])
    toll = dict(category='timed', id='toll', after_minutes=0,
                source=dict(document=document, page=2, review='confirmed'),
                effects=[dict(kind='resource', target='hit_dice', value=-1, text='Ночь без отдыха')])
    purse = dict(category='timed', id='purse', after_minutes=0,
                 source=dict(document=document, page=3, review='confirmed'),
                 effects=[dict(kind='table', target='purse-table', text='Что в кошеле')])
    table = dict(id='purse-table', formula='1d4', source=dict(document=document, page=3, review='confirmed'),
                 rows=[dict(low=1, high=4, label='Кошель', effects=[
                     dict(kind='reward', target='purse-gold', text='Награда за находку')])])
    rewards = dict(mode='experience', rewards=[dict(id='purse-gold', kind='experience', amount=50,
                                                    page=3, reason='Награда напечатана в книге')])
    # A provenance must name the very document of this bundle, so the opening
    # events are built from it when a caller brings its own.
    if callable(events):
        events = events(document)
    if callable(tables):
        tables = tables(document)
    app.state.adventures.save_definition(bundle['id'],
                                         definition('opening', document,
                                                    events=events if events is not None else [gate, toll, purse],
                                                    tables=tables if tables is not None else [table],
                                                    progression=progression if progression is not None else rewards),
                                         True)
    return bundle['id']


def attach(app, cid, bundle):
    """Open the review screen and bind the book to the Nat20 campaign."""
    with TestClient(app) as client:
        assert client.post('/api/adventure/review/session', json=dict(confirm_spoilers=True)).status_code == 200
        attached = client.post('/api/adventure/review/campaigns/%s/attach' % cid,
                               json=dict(bundle=bundle, request_id='r159-attach', confirm=True))
    assert attached.status_code == 200, attached.text
    return attached


def turn(app, cid, request_id):
    version = app.state.store.campaign(cid)['version']
    with TestClient(app) as client:
        return client.post('/api/campaigns/%s/command-turn' % cid,
                           json=dict(request_id=request_id, text=TEXT, expected_version=version))


def notices(app, cid, request_id):
    """What the table was told on that turn, from the recorded result row."""
    store = app.state.store
    action = store.request(cid, request_id)
    results = store.event_for_action(action['id'])['results']
    return [item['text'] for item in results[-1].get('adventure_events') or []]


def test_pending_effects_are_executed_on_the_next_nat20_screen_turn(native, tmp_path):
    app, cid = native
    store = app.state.store
    attached = attach(app, cid, reviewed_book(app, tmp_path))

    before = store.campaign(cid)['state']
    # The preparation route rolls nothing: every mechanical effect is queued in
    # the order the book prints it, with the page it came from.
    assert [item['kind'] for item in before['adventure']['deferred']] == ['check', 'resource', 'table']
    assert before['adventure']['flags'].get('gate_open') is None
    assert not before['adventure'].get('rewards')
    assert before['nat20']['experience'] == 0
    assert before['nat20']['hit_dice']['dice_remaining'] == 1
    assert 'ожидает игрового экрана Nat20' in attached.text

    rolled = turn(app, cid, 'r159-turn-1')
    assert rolled.status_code == 200, rolled.text

    after = store.campaign(cid)['state']
    adventure = after['adventure']
    # Nothing is left waiting: each entry was executed by the screen turn.
    assert adventure['deferred'] == []
    gate = next(row for row in adventure['journal'] if row['id'] == 'gate')
    check = gate['rolls'][0]
    assert check['engine'] == 'nat20'
    numbers = check['check']
    assert numbers['dc'] == 12 and 1 <= numbers['natural_roll'] <= 20
    assert numbers['roll_total'] == numbers['natural_roll'] + numbers['modifier']
    assert adventure['flags']['gate_open'] is numbers['success']
    words = ' '.join(notices(app, cid, 'r159-turn-1'))
    # A resolved check answers with its own numbers, not with "the action worked".
    assert 'против Сл 12' in words, words
    assert 'Действие удалось' not in words and 'Попытка не удалась' not in words, words
    # The table was thrown from the book's own row and its nested reward was
    # booked once, with its page, into the counter this campaign keeps.
    purse = next(row for row in adventure['journal'] if row['id'] == 'purse')
    thrown = purse['rolls'][0]
    assert thrown['formula'] == '1d4' and thrown['table'] == 'purse-table'
    assert thrown['row']['label'] == 'Кошель'
    assert adventure['rewards']['purse-gold'] == dict(status='applied', experience=50, page=3, basis='per_character')
    assert 'Награда начислена: 50 опыта (стр. 3)' in words, words
    # The printed reward now pays the counter Nat20 owns: one confirmed receipt
    # in the engine's own ledger, so the durable state still sums to it.
    assert after['nat20']['experience'] == 50
    assert [(r['id'], r['experience'], r.get('kind'), r.get('page')) for r in after['nat20']['rewards']] == [
        ('printed:opening:purse-gold', 50, 'printed', 3)]
    assert 'Опыт Nat20: 50 (+50, стр. 3)' in words, words
    # A printed resource moved the counter Nat20 owns, not a host-side copy.
    assert after['nat20']['hit_dice']['dice_remaining'] == 0
    assert 'Кости хитов: 0' in words, words

    again = turn(app, cid, 'r159-turn-2')
    assert again.status_code == 200, again.text
    repeated = store.campaign(cid)['state']
    assert repeated['adventure']['deferred'] == []
    assert repeated['adventure']['rewards']['purse-gold']['status'] == 'applied'
    assert repeated['nat20']['hit_dice']['dice_remaining'] == 0
    assert len([row for row in repeated['adventure']['journal'] if row['id'] == 'purse']) == 1
    assert len([row for row in repeated['adventure']['journal'] if row['id'] == 'gate']) == 1


def test_a_replayed_queue_cannot_book_the_same_reward_twice(native, tmp_path):
    """Re-ticking the same queue is a repeat, not a second payout."""
    from solo.adventure_engine import resolve_deferred

    app, cid = native
    store = app.state.store
    attach(app, cid, reviewed_book(app, tmp_path))
    queued = copy.deepcopy(store.campaign(cid)['state']['adventure']['deferred'])
    assert len(queued) == 3
    assert turn(app, cid, 'r159-turn-1').status_code == 200
    settled = store.campaign(cid)['state']

    replayed = copy.deepcopy(settled)
    # A stale copy of the queue, exactly what a repeated tick would hand over.
    replayed['adventure']['deferred'] = copy.deepcopy(queued)
    assert resolve_deferred(replayed) == []
    assert replayed['nat20']['experience'] == 50
    assert replayed['adventure']['deferred'] == []
    assert replayed['adventure']['rewards']['purse-gold']['status'] == 'applied'
    purse = next(row for row in replayed['adventure']['journal'] if row['id'] == 'purse')
    assert len(purse['rolls']) == 1


def test_two_checks_that_differ_only_in_dc_are_two_throws(native, tmp_path):
    """One event, two checks with the same target and text: no entry is lost."""
    app, cid = native
    store = app.state.store
    def events(document):
        source = dict(document=document, page=1, review='confirmed')
        return [dict(category='timed', id='gate', after_minutes=0, source=source,
                     effects=[dict(kind='check', ability='wisdom', dc=12, target='gate_open', text='Замечен ли след'),
                              dict(kind='check', ability='wisdom', dc=18, target='gate_open', text='Замечен ли след')])]
    attach(app, cid, reviewed_book(app, tmp_path, 'Two checks', events=events, tables=[]))
    queued = store.campaign(cid)['state']['adventure']['deferred']
    assert [item['kind'] for item in queued] == ['check', 'check']
    assert turn(app, cid, 'r159-turn-1').status_code == 200
    adventure = store.campaign(cid)['state']['adventure']
    assert adventure['deferred'] == []
    record = next(row for row in adventure['journal'] if row['id'] == 'gate')
    assert sorted(roll['check']['dc'] for roll in record['rolls']) == [12, 18]


def test_an_effect_that_cannot_be_finished_changes_nothing_and_rolls_nothing(native, tmp_path):
    """A table whose row pays an unregistered reward is not half-applied."""
    app, cid = native
    store = app.state.store
    def events(document):
        source = dict(document=document, page=3, review='confirmed')
        return [dict(category='timed', id='purse', after_minutes=0, source=source,
                     effects=[dict(kind='table', target='broken-table', text='Что в кошеле')])]

    def tables(document):
        source = dict(document=document, page=3, review='confirmed')
        return [dict(id='broken-table', formula='1d4', source=source,
                     rows=[dict(low=1, high=4, label='Кошель', effects=[
                         dict(kind='reward', target='ghost-gold', text='Неописанная награда')])])]
    attach(app, cid, reviewed_book(app, tmp_path, 'Broken table', events=events,
                                   tables=tables, progression=dict(mode='unset', rewards=[])))
    assert turn(app, cid, 'r159-turn-1').status_code == 200
    adventure = store.campaign(cid)['state']['adventure']
    # Still waiting, and the failed attempt left no roll and no payout behind.
    assert [item['kind'] for item in adventure['deferred']] == ['table']
    record = next(row for row in adventure['journal'] if row['id'] == 'purse')
    assert record['rolls'] == [] and not adventure.get('rewards')
    words = ' '.join(notices(app, cid, 'r159-turn-1'))
    assert 'ghost-gold' in words and 'ожидает игрового экрана Nat20' in words, words
    assert turn(app, cid, 'r159-turn-2').status_code == 200
    again = next(row for row in store.campaign(cid)['state']['adventure']['journal'] if row['id'] == 'purse')
    assert again['rolls'] == []

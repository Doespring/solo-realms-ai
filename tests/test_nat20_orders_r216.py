"""r216 acceptance: an order reaches the companion, and he answers for himself.

Owner's decision 03.11.2026 (SPEC-COMPANIONS item 2): the player may give an
order or a request, the character is not obliged to obey, and the party keeps a
system of relations. What is asserted here is the separation the card demands:

* the interpreter only names the request (form + category + whom), and that
  reading is in the journal;
* the host decides from the companion's traits and relation, which live in the
  campaign state - the model cannot talk him into obedience;
* a refusal carries a reason the player can act on and both relation numbers;
* the relation moves by the table's own step, and a companion below the
  threshold leaves the party.
"""
import json

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_orders import LEAVE_THRESHOLD, decide
from solo.storage import Store


def join(store, cid, name, traits, request_id):
    """The screen's own route: a companion joins with his character."""
    hero_build = store.campaign(cid)['state']['nat20']['definition']['build']
    with TestClient(create_app(store.path, CommandMockLLM({}), experimental=True)) as client:
        response = client.post(f'/api/campaigns/{cid}/party', json=dict(
            name=name, build=hero_build, prepared_spells=[], traits=traits, request_id=request_id))
        assert response.status_code == 200, response.text
        return response.json()['member']


def campaign(tmp_path, name):
    store = Store(tmp_path / 'orders.sqlite3')
    api = Nat20GameEngine(store)
    identity = api.create_campaign(name, seed=11)
    return store, api, identity['id']


def order(api, store, cid, member, text, category, form, request_id):
    current = store.campaign(cid)
    return api.execute(cid, GameCommand(request_id=request_id, expected_version=current['version'],
        action=dict(kind='order', target_id=member, text=text, form=form, category=category)))


def relation_of(api, store, cid, member):
    row = next(item for item in api.party(store.campaign(cid)) if item['id'] == member)
    return row['relation'], row


def test_a_request_within_character_is_obeyed_and_moves_the_relation(tmp_path):
    store, api, cid = campaign(tmp_path, 'Согласие')
    # Proud and loyal: a risky request from a hero he trusts at all is answered
    # with yes (20 + 20 - 40 = the bar is 0), so this is the "within character" case.
    member = join(store, cid, 'Мира', ['proud', 'loyal'], 'join-mira')
    result = order(api, store, cid, member, 'прикрой меня в подвале', 'risk', 'request', 'order-1')

    native = {event['type']: event for event in result['native_events']}
    assert native['order']['target_id'] == member
    assert native['order']['category'] == 'risk' and native['order']['form'] == 'request'
    assert 'order_obeyed' in native, result['native_events']
    assert native['order_obeyed']['reason'].startswith('Согласие')
    assert native['relation_shift']['before'] == 0 and native['relation_shift']['after'] == 5

    after, row = relation_of(api, store, cid, member)
    assert after == 5
    assert row['traits'] == ['proud', 'loyal']
    # The journal sentence the player and the narrator both read.
    outcome = result['event'].payload['outcome']
    assert 'Мира' in outcome and 'риск для жизни' in outcome and 'согласие' in outcome
    assert '0 → 5' in outcome, outcome


def test_a_request_against_character_is_refused_with_a_reason_and_a_minus(tmp_path):
    store, api, cid = campaign(tmp_path, 'Отказ')
    # Cautious and proud: the same risky request is against who he is, and the
    # refusal must say why instead of a bare "did not obey".
    member = join(store, cid, 'Брин', ['cautious', 'proud'], 'join-brin')
    result = order(api, store, cid, member, 'иди первым в тёмный подвал', 'risk', 'request', 'order-2')

    native = {event['type']: event for event in result['native_events']}
    assert 'order_refused' in native, result['native_events']
    assert 'осторожен' in native['order_refused']['reason']
    assert native['relation_shift']['before'] == 0 and native['relation_shift']['after'] == -2
    after, _ = relation_of(api, store, cid, member)
    assert after == -2

    outcome = result['event'].payload['outcome']
    # Not swallowed by a general phrase: the sentence names the companion, what
    # was asked, the reason with the bar, and both numbers.
    assert 'Брин' in outcome and 'тёмный подвал' in outcome
    assert 'доверие к герою 0' in outcome and '0 → -2' in outcome, outcome


def test_an_order_to_a_proud_companion_is_its_own_refusal(tmp_path):
    store, api, cid = campaign(tmp_path, 'Гордость')
    member = join(store, cid, 'Брин', ['proud', 'cautious'], 'join-brin')
    # A bare order asks him to go first *and* to take an order from someone he
    # does not trust yet: the reason names the pride, not just the danger.
    result = order(api, store, cid, member, 'иди первым, приказываю', 'normal', 'order', 'order-3')
    native = {event['type']: event for event in result['native_events']}
    assert native['order']['form'] == 'order'
    assert 'order_refused' in native
    assert 'приказ' in native['order_refused']['reason'], native['order_refused']


def test_the_order_is_in_the_journal_events_not_only_in_the_text(tmp_path):
    store, api, cid = campaign(tmp_path, 'Журнал')
    member = join(store, cid, 'Мира', ['proud', 'loyal'], 'join-mira')
    result = order(api, store, cid, member, 'прикрой меня', 'risk', 'request', 'order-4')
    assert result['event'].payload['command'] == 'order'
    # The durable journal row keeps the events themselves, so the decision
    # survives a browser restart and a replay - it is state, not a narrator's mood.
    with store.connection() as db:
        raw = db.execute('SELECT results FROM events ORDER BY seq DESC LIMIT 1').fetchone()[0]
    stored = json.loads(raw)
    types = [event['type'] for row in stored for event in row.get('native_events') or ()]
    assert types == ['order', 'order_obeyed', 'relation_shift'], types
    assert stored[0]['native_result']['order']['relation_after'] == 5



def test_a_companion_below_the_threshold_leaves_the_party(tmp_path):
    store, api, cid = campaign(tmp_path, 'Уход')
    member = join(store, cid, 'Мира', ['proud', 'loyal'], 'join-mira')
    # Three named betrayals: -25 each, so the third crosses the leave threshold.
    from solo.nat20_orders import betray
    for index in range(3):
        runtime = store.campaign(cid)['state']['nat20']
        events = betray(runtime, member, 'продал отряд')

        def transition(state, runtime=runtime):
            state['nat20'] = runtime
            return [dict(type='scripted', text='предательство')]

        store.record_scenario(cid, 'betray-' + str(index), 'предательство', transition)
    assert events[-1]['type'] == 'companion_left'
    assert events[-1]['relation'] <= LEAVE_THRESHOLD
    remaining = [row['id'] for row in api.party(store.campaign(cid))]
    assert member not in remaining and remaining == ['hero']


def test_the_decision_table_needs_no_model():
    """The table is the whole rule: same numbers in, same answer out."""
    assert decide(0, ['cautious', 'proud'], 'risk', 'request')['decision'] == 'refused'
    assert decide(70, ['cautious', 'proud'], 'risk', 'request')['decision'] == 'obeyed'
    assert decide(0, ['proud', 'loyal'], 'risk', 'request')['decision'] == 'obeyed'
    assert decide(0, ['proud', 'loyal'], 'risk', 'order')['decision'] == 'refused'
    assert decide(-30, [], 'normal', 'request')['decision'] == 'silent'
    assert decide(0, ['loyal', 'greedy'], 'profit', 'request')['decision'] == 'obeyed'
    against = decide(90, ['proud', 'loyal'], 'against_character', 'request')
    assert against['decision'] == 'refused' and against['delta'] < 0


def test_an_order_cannot_be_given_in_the_middle_of_a_fight(tmp_path):
    """r215 owns the companion's turn in combat; an order is not a second path."""
    store, api, cid = campaign(tmp_path, 'Бой')
    member = join(store, cid, 'Мира', ['proud', 'loyal'], 'join-mira')
    started = api.execute(cid, GameCommand(request_id='fight',
        expected_version=store.campaign(cid)['version'],
        action=dict(kind='start_combat', encounter_id='basic')))
    assert started
    with pytest.raises(ValueError) as refused:
        order(api, store, cid, member, 'прикрой меня', 'risk', 'request', 'order-5')
    assert 'В бою спутник действует сам' in str(refused.value)


def test_the_interpreter_is_told_the_party_and_can_name_the_order(tmp_path):
    """Without this the model cannot address a companion at all."""
    from solo.game import Game
    store, api, cid = campaign(tmp_path, 'Контекст')
    member = join(store, cid, 'Мира', ['proud', 'loyal'], 'join-mira')
    game = Game(store, CommandMockLLM({}))
    context = game.compiler.command_context(store.campaign(cid))
    names = {command['name'] for command in context['engine_commands']}
    assert 'order' in names, names
    companion = next(row for row in context['party'] if row['id'] == member)
    assert companion['traits'] == ['proud', 'loyal'] and companion['relation'] == 0

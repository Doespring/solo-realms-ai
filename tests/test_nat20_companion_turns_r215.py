"""r215 acceptance: the master plays the companions' turns in a fight.

Owner's decision 03.11.2026 (SPEC-COMPANIONS items 1-3): a companion is a full
participant — its own initiative, its own Action and Bonus Action, its own
resources — and the master, not the player, decides what it does.

Every number asserted here comes from Nat20's own output: the initiative the
engine rolled, the attack roll and damage it emitted, the resource counters it
keeps. The host policy only chooses the intent, and a refusal stays visible in
the journal with the engine's own reason.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_wizard import suggested_build
from solo.storage import Store


def build(class_slug, level=1):
    return suggested_build(class_slug=class_slug, species_slug='human',
                           background_slug='soldier', level=level)


def campaign(tmp_path, name='Партия', encounters=None):
    store = Store(tmp_path / 'companion.sqlite3')
    api = Nat20GameEngine(store)
    identity = api.create_experimental_campaign(name, build=build('fighter'),
                                                encounters=encounters or {}, title=name)
    return store, api, identity['id']


def join(store, cid, name, class_slug):
    """The screen's own route: a companion joins with a build the editor accepts."""
    with TestClient(create_app(store.path, CommandMockLLM({}), experimental=True)) as client:
        response = client.post(f'/api/campaigns/{cid}/party', json=dict(
            name=name, build=build(class_slug), prepared_spells=[], request_id='join-companion'))
        assert response.status_code == 200, response.text
        return response.json()['member']


def command(api, store, cid, action, request_id):
    return api.execute(cid, GameCommand(request_id=request_id,
                                        expected_version=store.campaign(cid)['version'],
                                        action=action))


def yard(distance=5):
    return dict(grid=dict(width=14, height=10), hero_position=dict(x=1, y=1, z=0),
                actors=[dict(id='goblin', monster='goblin-warrior',
                             position=dict(x=distance, y=1, z=0))])


def fight(tmp_path, name, companion_class='barbarian'):
    store, api, cid = campaign(tmp_path, name, encounters={'yard': yard()})
    member = join(store, cid, 'Мира', companion_class)
    opened = command(api, store, cid, dict(kind='start_combat', encounter_id='yard'), 'fight')
    return store, api, cid, member, opened


def test_a_companion_takes_its_own_turns_and_is_named_in_the_journal(tmp_path):
    store, api, cid, member, opened = fight(tmp_path, 'Свои ходы')
    view = store.campaign(cid)['state']['nat20']['combat']['view']
    # The engine rolled initiative for both, and both are party members with
    # their own hit points — a companion is not a monster on the host side.
    assert set(view['party_ids']) == {'hero', member}
    initiative = {row['entity_id']: row['initiative'] for row in view['initiative']}
    assert set(initiative) == {'hero', member, 'goblin'}
    assert all(isinstance(value, int) for value in initiative.values())

    turns = []
    outcomes = []
    for index in range(4):
        if not store.campaign(cid)['state']['nat20'].get('combat'):
            break
        result = command(api, store, cid, dict(kind='end_turn'), 'turn-' + str(index))
        turns += (result.get('result') or {}).get('companion_turns') or []
        outcomes.append(result['event'].payload['outcome'])

    assert turns, 'the master never played a companion turn'
    assert {turn['name'] for turn in turns} == {'Мира'}, turns
    actions = [action for turn in turns for action in turn['actions']]
    assert len([action for action in actions if action['kind'] == 'attack']) >= 2, actions
    # The engine's numbers, not a host summary: a roll and its total, and the
    # damage it applied on a hit.
    assert any('бросок' in action['line'] for action in actions), actions
    assert any('урон' in action['line'] or 'промах' in action['line'] for action in actions), actions
    # The journal sentence of the command names the companion and what it did.
    joined = ' '.join(outcomes)
    assert 'Мира (спутник):' in joined, joined
    assert 'атака' in joined, joined


def test_the_master_does_not_repeat_rage_while_it_is_active(tmp_path):
    store, api, cid, member, opened = fight(tmp_path, 'Ярость')
    rage_lines = 0
    for index in range(4):
        runtime = store.campaign(cid)['state']['nat20']
        if not runtime.get('combat'):
            break
        result = command(api, store, cid, dict(kind='end_turn'), 'turn-' + str(index))
        for turn in (result.get('result') or {}).get('companion_turns') or []:
            rage_lines += sum(1 for action in turn['actions'] if action['label'] == 'Ярость')
    runtime = store.campaign(cid)['state']['nat20']
    counters = (runtime['party'][1]['member'].get('custom_counters') or {})
    spent = (counters.get('feature_use:rage') or {}).get('spent')
    # The engine spends the Rage use once; a second activation while the effect
    # is up is a silent no-op (measured r215: counter stays at 1, no event), so
    # the master must not ask for it again - one line, one spent use.
    assert spent == 1, counters
    assert rage_lines == 1, rage_lines


def test_an_exhausted_companion_resource_is_refused_with_engine_numbers(tmp_path):
    """The capped feature runs out and the journal keeps the engine's reason."""
    store = Store(tmp_path / 'capped.sqlite3')
    api = Nat20GameEngine(store)
    identity = api.create_experimental_campaign('Ресурс', build=build('fighter'),
                                                encounters={'yard': yard()}, title='Ресурс')
    cid = identity['id']
    member = join(store, cid, 'Торн', 'fighter')
    # The fighter companion already spent both Second Wind uses and stands below
    # half hit points: exactly the state in which the master wants to heal.
    campaign_state = store.campaign(cid)['state']
    for item in campaign_state['nat20']['party']:
        if item['id'] == member:
            item['member']['hp_current'] = 3
            item['member']['custom_counters'] = {'feature_use:second-wind': {'spent': 2}}
    with store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?',
                   (__import__('json').dumps(campaign_state), cid))
    command(api, store, cid, dict(kind='start_combat', encounter_id='yard'), 'fight')
    refused = []
    for index in range(4):
        runtime = store.campaign(cid)['state']['nat20']
        if not runtime.get('combat'):
            break
        result = command(api, store, cid, dict(kind='end_turn'), 'turn-' + str(index))
        for turn in (result.get('result') or {}).get('companion_turns') or []:
            refused += turn.get('refused') or []
        if refused:
            break
    assert refused, 'the exhausted feature was not refused'
    reason = refused[0]['reason']
    assert 'no_uses_remaining' in reason, refused
    assert 'second-wind=2' in reason, refused


def test_plan_names_a_weapon_the_sheet_carries(tmp_path):
    """The decision itself: attack, move to reach, Rage first — from the view alone."""
    from solo.nat20_companion import plan_turn

    member = dict(id='companion-1', name='Мира',
                  build=dict(equipment=['greataxe', 'handaxe', 'shortbow']),
                  sheet=dict(features=['rage']), member=dict(hp_max=14))
    view = dict(initiative=[dict(entity_id='companion-1', name='Мира', hp_current=14, hp_max=14,
                                 action_available=True, bonus_action_available=True,
                                 worn_armor=None, is_alive=True),
                            dict(entity_id='goblin', name='Goblin Warrior', hp_current=10,
                                 is_alive=True)],
                encounter_ids=['goblin'], party_ids=['companion-1'], dead_ids=[],
                tracked_hp={'companion-1': 14, 'goblin': 10},
                actor_zone={'companion-1': '2,1', 'goblin': '5,1'})
    steps = plan_turn(view, member, 'companion-1', grid=dict(width=14, height=10))
    labels = [step['label'] for step in steps]
    assert labels[0] == 'Ярость', labels
    assert 'шаг к цели' in labels, labels
    assert 'атака' in labels, labels
    attack = next(step for step in steps if step['label'] == 'атака')
    assert attack['intent']['weapon_id'] in {'greataxe', 'handaxe', 'shortbow'}, attack

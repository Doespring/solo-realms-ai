"""A lost reply that is asked for again must say where the table stands now.

When a reply is lost the player repeats the request with the same request_id.
The stored text is what happened; the snapshot sent back is the campaign as it
stands NOW. If the table played on, those differ, and presenting the newer state
as this turn's result would quietly hide the turns in between.

The replay therefore reports "moved_on" beside the confirmed outcome.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio

from scripts.command_smoke import FixedDice, setup
from solo.storage import Store


def play(game, campaign, request_id, text):
    version = game.store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, request_id, text, version, rng=FixedDice()))


def play_in(store, campaign, request_id, text):
    """The same call, made against a world whose state carries a Nat20 runtime."""
    from solo.game import Game
    from scripts.command_smoke import SCENARIO, CommandMockLLM

    mock = CommandMockLLM({said: dict(status='command', action=action, message='')
                           for said, action in SCENARIO})
    version = store.campaign(campaign)['version']
    return asyncio.run(Game(store, mock).play_command(campaign, request_id, text, version, rng=FixedDice()))


def world_with_engine(tmp_path, request_id='req-walk', text='Исследую следы'):
    """A world campaign that also carries a Nat20 runtime.

    The legacy Python combat these checks used to lean on is gone: Nat20 is the
    only calculator, so the turn in between has to be a real engine turn. The
    engine needs its runtime inside the campaign state, which is what
    create_experimental_campaign builds.
    """
    from solo.game_commands import GameCommand
    from solo.nat20_api import Nat20GameEngine
    from solo.prototype_prepared import default_encounters
    from tests.nat20_builds import legal

    BUILD = dict(class_slug='fighter', species_slug='human', level=5,
                 subclass_slug='champion', equipment=['longsword', 'chain-mail', 'shield'])
    store = Store(tmp_path / 'world.sqlite3')
    api = Nat20GameEngine(store)
    campaign = api.create_experimental_campaign(
        'Проба', build=legal(**BUILD), encounters=default_encounters(),
        # The material has to print the check the player asks for; without it
        # the master cannot rule and the turn is correctly refused.
        checks={'следы': dict(kind='skill', skill='survival', ability=None, dc=12,
                              source=dict(kind='authored_scenario', id='traces', version='1',
                                          note='Traces the material prints for this yard'))},
        seed=3, title='Двор', description='д')['id']

    encounter = next(iter(default_encounters()))

    def fight(request_id='req-fight'):
        return api.execute(campaign, GameCommand(
            request_id=request_id, expected_version=store.campaign(campaign)['version'],
            action=dict(kind='start_combat', encounter_id=encounter)))

    return store, campaign, fight


def test_a_finished_turn_replays_its_own_outcome(tmp_path):
    store, campaign, game = setup(tmp_path)
    first = play(game, campaign, 'req-1', 'Исследую следы')
    assert first['status'] == 'resolved', first
    again = play(game, campaign, 'req-1', 'Исследую следы')
    assert again.get('replayed') is True
    assert again['narrative'] == first['narrative'], 'a repeat returns the confirmed outcome'


def test_a_repeat_right_after_the_turn_is_not_marked_as_moved(tmp_path):
    store, campaign, game = setup(tmp_path)
    play(game, campaign, 'req-a', 'Исследую следы')
    again = play(game, campaign, 'req-a', 'Исследую следы')
    assert again.get('moved_on') is False, 'nothing happened in between'


def test_a_repeat_after_more_turns_still_returns_the_old_outcome(tmp_path):
    store, campaign, game = setup(tmp_path)
    first = play(game, campaign, 'req-b', 'Исследую следы')
    play(game, campaign, 'req-c', 'Заканчиваю ход')
    again = play(game, campaign, 'req-b', 'Исследую следы')
    assert again.get('replayed') is True
    # The old turn's words must not be replaced by the newer story, whatever
    # the flag says; the flag is there to name the difference, not to hide it.
    assert again['narrative'] == first['narrative']
    assert 'moved_on' in again, 'the reply names whether the table moved on'



def test_a_mechanical_turn_says_the_table_moved_on(tmp_path):
    """The seen case: a walk, then a fight, then the walk's reply is repeated.

    Mechanics turns store their command, and that path compared the journal's
    already-decoded state against the campaign as text and swallowed the
    TypeError, so it always answered 'not moved' - even though the fight had
    started. Both paths must speak with one voice.
    """
    store, campaign, fight = world_with_engine(tmp_path)
    first = play_in(store, campaign, 'req-walk', 'Исследую следы')
    assert first['status'] == 'resolved', first
    fight()
    again = play_in(store, campaign, 'req-walk', 'Исследую следы')
    assert again.get('replayed') is True
    assert again['moved_on'] is True, 'the fight happened, the repeat must say so'
    assert again['narrative'] == first['narrative'], 'the outcome is still its own'



def _world(store, campaign):
    """Give the stand the three places travel.NODES knows, so a walk is legal."""
    import json
    from solo import travel
    current = store.campaign(campaign)
    state = json.loads(json.dumps(current['state']))
    names = [place['name'] for place in state['locations']]
    for node in travel.NODES:
        if node['name'] not in names:
            state['locations'].append(dict(name=node['name'], description=node['name'] + '.',
                                           npc=[], objects=[], facts=[]))
    state['location'] = travel.NODES[0]['name']
    with store.connection(True) as c:
        c.execute('update campaigns set state=? where id=?', (json.dumps(state, ensure_ascii=False), campaign))


def test_a_walk_committed_by_the_world_service_also_admits_it(tmp_path):
    """The other replay path: a turn recorded by WorldService, not by mechanics.

    WorldService stores the result under 'text' and never writes 'game_command',
    so this turn takes the second branch. The critic showed that branch carried
    the same broken comparison and that no test touched it.
    """
    from solo import travel
    from solo.world_service import WorldCommand, WorldService

    store, campaign, fight = world_with_engine(tmp_path)
    _world(store, campaign)
    spoken = 'Иду к старой башне'
    current = store.campaign(campaign)
    WorldService(store).execute(
        campaign,
        WorldCommand(request_id='req-world', expected_version=current['version'],
                     kind='travel', target=travel.NODES[1]['name']),
        original=spoken)
    assert store.campaign(campaign)['state']['location'] == travel.NODES[1]['name']

    alone = play_in(store, campaign, 'req-world', spoken)
    assert alone.get('replayed') is True, alone
    assert alone['moved_on'] is False, 'nothing happened after the walk'

    fight()
    later = play_in(store, campaign, 'req-world', spoken)
    assert later.get('replayed') is True, later
    assert later['moved_on'] is True, 'the fight happened after the walk'
    assert 'Старая башня' in later['narrative']

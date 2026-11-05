"""A repeated turn must not spend the hero's resources twice.

Section 7.2 of the repair task: "do not allow a second expenditure, reward,
movement or turn record". A lost reply makes players repeat requests, so the
repeat is an ordinary path, not an edge case.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sqlite3

import pytest

from scripts.command_smoke import FixedDice, setup
from solo.storage import Store


def play(game, campaign, request_id, text):
    version = game.store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, request_id, text, version, rng=FixedDice()))


def snapshot(store, campaign):
    state = store.campaign(campaign)['state']
    hero = state['hero']
    return dict(hp=hero.get('hp'), max_hp=hero.get('max_hp'),
                abilities=hero.get('abilities'), resources=hero.get('resources'),
                turn=state.get('turn'))


def event_count(store, campaign):
    with sqlite3.connect(str(store.path)) as c:
        return c.execute('SELECT COUNT(*) FROM events WHERE campaign=?', (campaign,)).fetchone()[0]


def nat20_fight(tmp_path):
    """A real Nat20 campaign with one legitimate combat already started.

    Nat20 is the only calculator, so a fight is opened through it; the legacy
    Python combat no longer exists and these checks must not lean on it.
    """
    from solo.nat20_api import Nat20GameEngine
    from solo.game_commands import GameCommand

    store = Store(tmp_path / 'fight.sqlite3')
    api = Nat20GameEngine(store)
    campaign = api.create_campaign('Fighter', definition_id='fighter-standard-array-2024-v1', seed=5)['id']

    def command(request_id, **action):
        return api.execute(campaign, GameCommand(
            request_id=request_id, expected_version=store.campaign(campaign)['version'], action=action))

    assert command('req-fight', kind='start_combat', encounter_id='basic')
    return store, campaign, api, command


def test_repeating_a_fight_spends_nothing_more(tmp_path):
    store, campaign, api, command = nat20_fight(tmp_path)
    before = api.snapshot(store.campaign(campaign))
    version_before = store.campaign(campaign)['version']
    events_before = event_count(store, campaign)

    with pytest.raises(ValueError):
        # The same request id may not be executed twice; presenting the newer
        # state as this turn's result would also hide the turns in between.
        command('req-fight', kind='start_combat', encounter_id='basic')

    assert store.campaign(campaign)['version'] == version_before, 'no second turn'
    assert api.snapshot(store.campaign(campaign)) == before, 'hp, resources and turn untouched'
    assert event_count(store, campaign) == events_before, 'no second turn record'


def test_repeating_a_move_does_not_move_twice(tmp_path):
    store, campaign, game = setup(tmp_path)
    first = play(game, campaign, 'req-1', 'Исследую следы')
    assert first['status'] == 'resolved', first
    place = store.campaign(campaign)['state'].get('location')
    turn = store.campaign(campaign)['state'].get('turn')

    play(game, campaign, 'req-1', 'Исследую следы')

    now = store.campaign(campaign)['state']
    assert now.get('location') == place, 'the hero did not walk a second time'
    assert now.get('turn') == turn, 'the turn counter did not advance again'

def test_a_repeated_request_does_not_ask_the_model_again(tmp_path):
    """A lost reply makes the player press the same request again.

    State alone is not enough: a repeat that re-ran the model would still
    land on the same numbers and pass the checks above, while paying twice
    and possibly showing a different text. The model call is the witness.
    """
    store, campaign, game = setup(tmp_path)
    play(game, campaign, "req-model", "Исследую следы")
    before = len(game.llm.metrics)
    again = play(game, campaign, "req-model", "Исследую следы")
    assert again.get("replayed") is True, "the repeat is a replay"
    assert len(game.llm.metrics) == before, "a repeat must not reach the model"

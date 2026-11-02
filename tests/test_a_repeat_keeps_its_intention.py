"""A repeated request_id must not be reused for a different intention.

solo/game.py:569 refuses a repeat whose text differs from the original, and
:572 refuses a repeat of a turn that was undone. Replacing either branch with
pass left 23 and 5 tests green respectively: the stored result is returned
either way, so the reply looks reasonable while it answers a different question.

Section 5: one line is one public turn.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio

from scripts.command_smoke import FixedDice, setup


def play(game, campaign, request_id, text):
    version = game.store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, request_id, text, version,
                                          rng=FixedDice()))


def test_the_same_id_for_another_intention_is_refused(tmp_path):
    store, cid, game = setup(tmp_path / 'scenario')
    play(game, cid, 'turn-1', 'Осматриваюсь')
    answer = play(game, cid, 'turn-1', 'Бегу на север')
    assert answer['status'] == 'rejected', answer['status']


def test_a_repeated_id_returns_the_original_result(tmp_path):
    store, cid, game = setup(tmp_path / 'scenario')
    first = play(game, cid, 'turn-1', 'Осматриваюсь')
    again = play(game, cid, 'turn-1', 'Осматриваюсь')
    assert again['status'] == first['status'], (first['status'], again['status'])
    assert again['state_version'] == first['state_version'], 'the turn ran twice'


def test_replaying_an_undone_turn_is_refused(tmp_path):
    store, cid, game = setup(tmp_path / 'scenario')
    store.record_scenario(cid, 'turn-1', 'Осматриваюсь',
                          lambda state: [dict(type='scripted', text='Тихо.')])
    store.undo(cid)
    answer = play(game, cid, 'turn-1', 'Осматриваюсь')
    assert answer['status'] == 'rejected', answer['status']

"""A repeated play_command answers from the record, not by running again.

solo/game.py:632 restores the finished turn when the same request_id arrives at
play_command. Replacing that branch with a refusal left 24 tests green: the
cycle test drives each request_id once, and the other replay test exercises the
command-turn path instead. There are two replay branches; this one is here.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

from solo.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tests'))
from command_smoke import setup as make_scenario  # noqa: E402
from test_command_cycle import play  # noqa: E402


def test_a_repeated_play_answers_from_the_record(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'scenario')
    store.record_scenario(cid, 'walk-1', 'Иду во двор',
                          lambda state: [dict(type='scripted', text='Двор пуст.')])
    version = store.campaign(cid)['version']

    first = play(game, cid, 'walk-1', 'Иду во двор', version)
    assert first.get('status') == 'resolved', first

    turns = store.campaign(cid)['state'].get('history', [])
    again = play(game, cid, 'walk-1', 'Иду во двор', version)
    after = store.campaign(cid)['state'].get('history', [])

    assert again.get('status') == 'resolved', (
        'the repeat was refused instead of restored: %s' % again.get('status'))
    assert again.get('replayed') is True, 'the repeat was not answered from the record'
    assert len(after) == len(turns), 'the repeat wrote a second turn'

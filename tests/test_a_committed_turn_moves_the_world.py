"""Committing a planned turn must write the new state, not only the event.

solo/storage.py:236 saves the campaign state and bumps its version. Replacing
that line with pass left 34 tests green: they read the event, the results and
the action status, and none re-reads the campaign afterwards.

A journal entry without the state is the worst kind of drift: the turn looks
committed, the narration is played, and the world is unchanged.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero, Plan, Step
from solo.rulesets import BUILTINS
from solo.storage import Store


def _look_action(store, cid, request_id, version):
    plan = Plan(summary="Осмотреться",
                steps=[Step(kind="observe", text="Осматриваюсь")],
                needs_confirmation=False)
    return store.stage(cid, request_id, 'Осматриваюсь', plan, version,
                       {'natural': True})


def test_a_committed_turn_moves_the_campaign_on(tmp_path):
    store = Store(tmp_path / 'commit.sqlite3')
    cid = store.create('Ход', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    before = store.campaign(cid)['version']
    action = _look_action(store, cid, 'look-1', before)

    store.apply(action['id'])

    after = store.campaign(cid)
    assert after['version'] == before + 1, (
        'the version did not move: %r -> %r' % (before, after['version']))


def test_the_saved_state_is_the_one_the_event_recorded(tmp_path):
    store = Store(tmp_path / 'commit2.sqlite3')
    cid = store.create('Ход', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    action = _look_action(store, cid, 'look-1', store.campaign(cid)['version'])

    store.apply(action['id'])

    event = store.event(store.history(cid)[-1]['id'])
    assert store.campaign(cid)['state'] == event['after_state'], (
        'the campaign state differs from what the journal recorded')
"""A committed action must not stay open for a second commit.

solo/storage.py:237 marks the action applied and stores its metrics. Replacing
it with pass left 35 tests green: they apply each action once and never look at
the row afterwards.

If status stays pending the row can be applied again; the version check at
:213 catches that only while nothing else moves. A second commit would append a
second journal entry for one player line, which section 5 forbids.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero, Plan, Step
from solo.rulesets import BUILTINS
from solo.storage import Store


def _look_action(store, cid, request_id, version):
    plan = Plan(summary="Осмотреться",
                steps=[Step(kind="observe", text="Осматриваюсь")],
                needs_confirmation=False)
    return store.stage(cid, request_id, 'Осматриваюсь', plan, version,
                       {'natural': True, 'router_ms': 1})


def test_a_committed_action_is_marked_applied(tmp_path):
    store = Store(tmp_path / 'applied.sqlite3')
    cid = store.create('Ход', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    action = _look_action(store, cid, 'look-1', store.campaign(cid)['version'])

    store.apply(action['id'])

    row = store.action(action['id'])
    assert row['status'] == 'applied', (
        'the committed action was left as %r' % row['status'])


def test_the_second_commit_does_not_write_a_second_entry(tmp_path):
    store = Store(tmp_path / 'applied2.sqlite3')
    cid = store.create('Ход', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    action = _look_action(store, cid, 'look-1', store.campaign(cid)['version'])
    store.apply(action['id'])
    entries = len(store.history(cid))

    try:
        store.apply(action['id'])
    except (ValueError, KeyError):
        pass

    assert len(store.history(cid)) == entries, (
        'the same action wrote a second journal entry')
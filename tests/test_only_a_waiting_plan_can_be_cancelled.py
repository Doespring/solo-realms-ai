"""Only a waiting plan may be cancelled.

solo/storage.py:199 writes cancelled only when the row is still pending and
raises otherwise. Replacing that condition with False left 17 tests green: none
of them cancels an action that has already been committed.

Without the guard the write is unconditional, so an applied action silently
becomes cancelled while its journal entry and its effect on the world stay.
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
                       {'natural': True})


def test_cancelling_a_waiting_plan_works(tmp_path):
    store = Store(tmp_path / 'cancel.sqlite3')
    cid = store.create('Ход', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    action = _look_action(store, cid, 'look-1', store.campaign(cid)['version'])

    store.cancel(action['id'])
    assert store.action(action['id'])['status'] == 'cancelled'


def test_a_committed_action_cannot_be_cancelled(tmp_path):
    store = Store(tmp_path / 'cancel2.sqlite3')
    cid = store.create('Ход', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    action = _look_action(store, cid, 'look-1', store.campaign(cid)['version'])
    store.apply(action['id'])

    with pytest.raises(ValueError):
        store.cancel(action['id'])
    assert store.action(action['id'])['status'] == 'applied', (
        'a committed action was relabelled %r' % store.action(action['id'])['status'])
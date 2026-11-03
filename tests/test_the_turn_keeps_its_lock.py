"""After waiting for the model the turn must own the lock again.

solo/game.py:481-482 reacquires the lock and sets _operation_owner back to the
current task. Setting that line to pass left 25 tests green: they exercise the
turn but never look at the owner field, so losing it stays invisible until a
later nested wait raises 'operation_without is only for the lock holder'.

Section 9.2: the read-check-commit around state stays serialised.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

from solo.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_the_turn_owns_the_lock_again_after_the_model_answers(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'scenario')

    async def body():
        async with game.operation:
            game._operation_owner = asyncio.current_task()
            inside = {}
            async with game.operation_without(cid):
                inside['locked_while_waiting'] = game.operation.locked()
                inside['owner_while_waiting'] = game._operation_owner
            return inside, game._operation_owner, game.operation.locked()

    inside, owner, locked = asyncio.run(body())
    assert inside['locked_while_waiting'] is False, (
        'the lock was not released for the model wait')
    assert inside['owner_while_waiting'] is None, (
        'the owner flag was not cleared while waiting')
    assert owner is not None, 'the owner flag was not restored after the wait'
    assert locked, 'the lock was not taken back'

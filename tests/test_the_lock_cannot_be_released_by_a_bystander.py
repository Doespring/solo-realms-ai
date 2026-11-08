"""A task must not re-enter the lock it already released.

A turn releases the campaign lock while it waits for the model. While it waits,
the lock is free on purpose and another request may take it. The owner field
used to keep naming the waiting task, so that task could call operation_without
again and release a lock somebody else was holding. Clearing the field on
release, and checking that the lock is actually held, closes that.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio

from solo.storage import Store
from solo.llm import MockLLM
from solo.game import Game
import tempfile
from pathlib import Path


def make():
    store = Store(Path(tempfile.mkdtemp())/'op.sqlite3')
    return Game(store, MockLLM())


def test_a_waiting_turn_cannot_use_the_released_lock():
    game = make()
    seen = {}

    async def owner():
        async with game.operation:
            game._operation_owner = asyncio.current_task()
            async with game.operation_without('c1'):
                # While the model is thinking the lock is free: someone else
                # legitimately takes it.
                async with game.operation:
                    try:
                        async with game.operation_without('c1'):
                            seen['second'] = 'entered'
                    except RuntimeError:
                        seen['second'] = 'refused'

    asyncio.run(owner())
    assert seen['second'] == 'refused', 'a task must not release a lock another task holds'


def test_a_task_that_never_held_the_lock_is_refused():
    game = make()
    seen = {}

    async def bystander():
        try:
            async with game.operation_without('c1'):
                seen['entered'] = True
        except RuntimeError:
            seen['refused'] = True

    asyncio.run(bystander())
    assert seen.get('refused') is True, 'only the lock holder may release it'


def test_the_holder_still_works_the_normal_way():
    game = make()
    order = []

    async def holder():
        async with game.operation:
            game._operation_owner = asyncio.current_task()
            async with game.operation_without('c1'):
                order.append('thinking')
            order.append('committing')
            assert game.operation.locked()

    asyncio.run(holder())
    assert order == ['thinking', 'committing']

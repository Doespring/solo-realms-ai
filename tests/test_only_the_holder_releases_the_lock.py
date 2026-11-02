"""Only the task that holds the campaign lock may release it.

solo/game.py:474 guards operation_without with two conditions: this task owns
the lock, and the lock is held. The existing test calls the helper with no lock
at all, so it is refused by the lock itself ('Lock is not acquired') and the
ownership half is never exercised. Dropping that half still passes 8 tests,
while a task that never held the lock can release it and break the
read/check/commit window the whole turn relies on.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio
from pathlib import Path

import pytest

from solo.app import create_app
from solo.llm import MockLLM


def test_a_stranger_cannot_release_the_campaign_lock(tmp_path):
    app = create_app(Path(tmp_path) / 'owner.sqlite3', MockLLM())
    game = app.state.game

    async def stranger():
        await game.operation.acquire()
        try:
            async with game.operation_without('any'):
                return 'released by a task that never held it'
        except RuntimeError as refused:
            return str(refused)

    outcome = asyncio.run(stranger())
    assert 'only for the lock holder' in outcome, outcome

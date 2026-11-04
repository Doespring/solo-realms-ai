"""A campaign without the engine must not resolve a fight in pure Python.

Section 7.1: Nat20 is the only D&D calculator. solo/command_engine.py:192 calls
self.combat.start_transition for start_combat, which is the Python CombatService
and touches no engine. A campaign whose state has no 'nat20' key therefore
resolves a fight locally - the very silent legacy fallback the task forbids.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio

import pytest

from scripts.command_smoke import FixedDice, setup


def _play(game, store, campaign, request_id, text):
    version = store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, request_id, text, version, rng=FixedDice()))


def test_a_fight_is_not_resolved_without_the_engine(tmp_path):
    store, campaign, game = setup(tmp_path)
    state = store.campaign(campaign)['state']
    assert 'nat20' not in state, 'this test is about the engine-less campaign'

    result = _play(game, store, campaign, 'nodll-1', 'Добавляю базовый бой')

    assert result.get('status') != 'resolved', (
        'a fight was resolved without the engine: ' + str(result.get('narrative'))[:120])

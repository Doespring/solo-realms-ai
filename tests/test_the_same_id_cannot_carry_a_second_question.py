"""A master question keeps its id too: one id, one question."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

import pytest

from solo.app import ActionRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_the_same_id_cannot_carry_a_second_question(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'ask')
    asyncio.run(game.route(cid, ActionRequest(
        request_id="ask-1", mode="gm", text="Мастер: что видно во дворе?")))

    with pytest.raises(ValueError) as caught:
        asyncio.run(game.route(cid, ActionRequest(
            request_id="ask-1", mode="gm", text="Мастер: где выход?")))
    assert 'уже использован' in str(caught.value), (
        'the second question was accepted: %s' % caught.value)
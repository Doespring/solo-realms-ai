"""One request_id belongs to one player line, never two."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

import pytest

from solo.app import ActionRequest
from solo.contracts import Plan, Step

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_the_same_id_cannot_carry_a_second_line(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'twice')
    game.llm.plan = Plan(summary='Осмотреться',
                         steps=[Step(kind='observe', text='Исследую следы')],
                         needs_confirmation=False)
    asyncio.run(game.route(cid, ActionRequest(request_id="turn-1",
                                                text="Исследую следы")))

    with pytest.raises(ValueError) as caught:
        asyncio.run(game.route(cid, ActionRequest(request_id="turn-1",
                                                    text="Заканчиваю ход")))
    assert 'уже использован' in str(caught.value), (
        'the second line was accepted: %s' % caught.value)
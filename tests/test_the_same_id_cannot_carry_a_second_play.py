"""A play request may not borrow the id of a recorded dialogue line."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

import pytest

from solo.app import ActionRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_a_play_may_not_reuse_a_dialogue_id(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'borrow')
    store.converse(cid, 'said-1', 'Здравствуй, Мирон', 'Мирон кивает.',
                   context={})

    with pytest.raises(ValueError) as caught:
        asyncio.run(game.play(cid, ActionRequest(request_id="said-1",
                                                   text="Иду во двор")))
    assert 'другой реплике' in str(caught.value), (
        'the borrowed id was accepted: %s' % caught.value)
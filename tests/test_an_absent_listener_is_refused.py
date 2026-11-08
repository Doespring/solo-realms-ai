"""A line may be addressed only to someone standing in the scene."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

import pytest

from solo.app import ActionRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_an_absent_npc_cannot_be_addressed(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'absent')
    with pytest.raises(ValueError) as caught:
        asyncio.run(game.route(cid, ActionRequest(
            request_id="say-1", mode="dialogue", target="Призрак",
            text="Кто ты?")))
    assert 'присутствующего' in str(caught.value), (
        'the absent listener was accepted: %s' % caught.value)
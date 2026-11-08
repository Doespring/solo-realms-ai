"""A recorded turn keeps its text: the same id may not carry a new line."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

import pytest

from solo.app import ActionRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_a_recorded_turn_cannot_change_its_text(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'recorded')
    store.record_scenario(cid, 'walk-1', 'Иду во двор',
                          lambda state: [dict(type='scripted', text='Двор пуст.')])

    with pytest.raises(ValueError) as caught:
        asyncio.run(game.play(cid, ActionRequest(request_id="walk-1",
                                                   text="Вхожу в башню")))
    assert 'другой реплике' in str(caught.value), (
        'the changed text was accepted: %s' % caught.value)
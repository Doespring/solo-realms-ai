"""A turn the player rolled back must not be narrated again."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def _drain(gen):
    async def run():
        return [chunk async for chunk in gen]
    return asyncio.run(run())


def test_an_undone_turn_is_not_narrated(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'undone')
    store.record_scenario(cid, 'walk-1', 'Иду во двор',
                          lambda state: [dict(type='scripted', text='Двор пуст.')])
    event = store.history(cid)[-1]['id']
    mark = store.save(cid, 'До двора')
    store.record_scenario(cid, 'walk-2', 'Вхожу в башню',
                          lambda state: [dict(type='scripted', text='Башня темна.')])
    second = store.history(cid)[-1]['id']
    store.restore(cid, mark)
    assert store.event(second)['undone'] == 1, 'the turn was not rolled back'

    with pytest.raises(ValueError) as caught:
        _drain(game.narrate(second))
    assert 'отменён' in str(caught.value), (
        'the rolled back turn was narrated: %s' % caught.value)
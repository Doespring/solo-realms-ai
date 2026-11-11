"""Undo is a write, so it must take the campaign lock like every other write.

solo/app.py:435 wraps the undo route in 'async with game.operation'. Removing
that wrapper left all four undo tests green: they call storage directly and never
contend for the lock. A turn may release the lock while it waits for the model
(section 9.2), so undo must not run beside another write either.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


class Recorder:
    """Stands in for the lock: records every entry and leaves it unlocked."""

    def __init__(self):
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1
        return self

    async def __aexit__(self, *kind):
        return False


def test_undo_takes_the_campaign_lock(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'scenario')
    recorder = Recorder()
    store.record_scenario(cid, 'probe-1', 'Осматриваюсь',
                          lambda state: [dict(type='scripted', text='Тихо.')])
    app = create_app(store.path, game.llm)
    # create_app builds its own Game; that instance is what the routes hold.
    app.state.game.operation = recorder
    with TestClient(app) as client:
        status = client.post('/api/campaigns/%s/undo' % cid).status_code
    assert status == 200, 'undo did not run: %s' % status
    # The app takes the lock once while building its state and once in the
    # route. Dropping the route's wrapper leaves the first entry behind, so a
    # ">= 1" check passes with the guard removed: count the exact number.
    assert recorder.entries == 2, (
        'undo route did not take the campaign lock (entries=%d)' % recorder.entries)

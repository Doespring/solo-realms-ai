"""Saving and restoring are writes: both routes must take the campaign lock.

solo/app.py:440 (save) and :444 (restore) wrap the store call in
'async with game.operation'. No test drives either route, so removing the
wrapper is invisible. A restore rewrites the whole campaign; running it beside
another write is exactly what section 9.2 rules out.
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
    def __init__(self):
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1
        return self

    async def __aexit__(self, *kind):
        return False


def _client(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'scenario')
    app = create_app(store.path, game.llm)
    recorder = Recorder()
    app.state.game.operation = recorder
    return store, cid, recorder, TestClient(app)


def test_save_takes_the_campaign_lock(tmp_path):
    store, cid, recorder, client = _client(tmp_path)
    with client:
        base = recorder.entries
        status = client.post('/api/campaigns/%s/saves' % cid,
                             json={'name': 'Веха'}).status_code
    assert status == 200, 'save did not run: %s' % status
    assert recorder.entries > base, 'saving wrote without taking the lock'


def test_restore_takes_the_campaign_lock(tmp_path):
    store, cid, recorder, client = _client(tmp_path)
    saved = store.save(cid, 'Веха')
    with client:
        base = recorder.entries
        status = client.post('/api/campaigns/%s/restore/%s' % (cid, saved)).status_code
    assert status == 200, 'restore did not run: %s' % status
    assert recorder.entries > base, 'restoring wrote without taking the lock'

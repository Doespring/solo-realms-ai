"""Player turns are writes and must run inside the campaign lock.

solo/game.py takes 'async with self.operation' in _play() (the body of play())
and in route(). Replacing both with 'if True:' left eight tests green: they
never watch the lock, only the result. Section 9.2: one write at a time.
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


def test_play_takes_the_lock(tmp_path):
    store, cid, recorder, client = _client(tmp_path)
    with client:
        base = recorder.entries
        status = client.post('/api/campaigns/%s/play' % cid,
                             json={'request_id': 'play-1',
                                   'text': 'Осматриваю двор'}).status_code
    assert status in (200, 422, 409), 'play answered %s' % status
    assert recorder.entries > base, 'play wrote without taking the lock'


def test_action_takes_the_lock(tmp_path):
    store, cid, recorder, client = _client(tmp_path)
    with client:
        base = recorder.entries
        status = client.post('/api/campaigns/%s/actions' % cid,
                             json={'request_id': 'act-1',
                                   'text': 'Осматриваю двор'}).status_code
    assert status in (200, 422, 409), 'action answered %s' % status
    assert recorder.entries > base, 'action wrote without taking the lock'

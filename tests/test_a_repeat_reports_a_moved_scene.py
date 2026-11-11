"""A repeat must say whether the world moved on since that turn.

solo/game.py _moved_on() compares the state recorded after the turn with the
campaign as it stands now, and both replay branches report it as moved_on.
Replacing it with a constant False left 22 tests green: they repeat a request
immediately, so nothing has changed yet.

Section 7.2: the caller repeats a request after something else happened, and
needs to hear that the scene is no longer the one that reply described.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tests'))
from test_nat20_command_cycle import model  # noqa: E402


def _campaign(tmp_path):
    store = Store(tmp_path / 'moved.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Сдвиг', seed=5)['id']
    return store, cid


def test_a_repeat_right_after_the_turn_reports_no_change(tmp_path):
    store, cid = _campaign(tmp_path)
    version = store.campaign(cid)['version']
    app = create_app(store.path, model())
    body = {'request_id': 'chk', 'text': 'check', 'expected_version': version}
    with TestClient(app) as client:
        client.post('/api/campaigns/%s/command-turn' % cid, json=body).json()
        again = client.post('/api/campaigns/%s/command-turn' % cid, json=body).json()
    assert again.get('replayed') is True, again.get('status')
    assert again.get('moved_on') is False, 'nothing changed, yet the reply says it moved'


def test_a_repeat_after_another_turn_reports_the_change(tmp_path):
    store, cid = _campaign(tmp_path)
    version = store.campaign(cid)['version']
    app = create_app(store.path, model())
    with TestClient(app) as client:
        client.post('/api/campaigns/%s/command-turn' % cid,
                    json={'request_id': 'chk', 'text': 'check',
                          'expected_version': version}).json()
        # Someone else plays a turn, so the world is no longer where it was.
        client.post('/api/campaigns/%s/command-turn' % cid,
                    json={'request_id': 'start', 'text': 'start',
                          'expected_version': store.campaign(cid)['version']}).json()
        again = client.post('/api/campaigns/%s/command-turn' % cid,
                            json={'request_id': 'chk', 'text': 'check',
                                  'expected_version': version}).json()
    assert again.get('replayed') is True, again.get('status')
    assert again.get('moved_on') is True, 'the world moved on, yet the reply hides it'
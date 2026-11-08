"""A repeated engine turn answers from the record, not by running again.

solo/game.py:582 restores the confirmed outcome of a finished command when the
same request_id arrives again. Setting that branch aside left 23 tests green:
test_command_cycle drives each request_id once, and the plain-repeat test lands
in play_command's own replay branch instead.

Section 7.2: after the fact the caller still receives what happened, and the
second copy must not execute. A replay is marked replayed=True and carries the
stored command; a fresh turn carries no such flag.
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


def test_a_repeated_engine_turn_is_answered_from_the_record(tmp_path):
    store = Store(tmp_path / 'repeat.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Повтор', seed=5)['id']
    version = store.campaign(cid)['version']
    app = create_app(store.path, model())
    body = {'request_id': 'chk', 'text': 'check', 'expected_version': version}
    with TestClient(app) as client:
        first = client.post('/api/campaigns/%s/command-turn' % cid, json=body).json()
        second = client.post('/api/campaigns/%s/command-turn' % cid, json=body).json()

    assert first.get('status') == 'resolved', first
    assert not first.get('replayed'), 'the first turn answered as a replay'
    assert second.get('status') == 'resolved', second
    assert second.get('replayed') is True, (
        'the repeat was not answered from the record: %s' % second.get('status'))
    assert second.get('command', {}).get('request_id') == 'chk', (
        'the replay lost the stored command')

"""A forged command digest must stop the replay.

solo/nat20_worker.py:100 compares the replayed snapshot and events with the
digest stored in the combat record. Setting that check to pass left seven
nat20 tests green: they drive real commands, so the stored digest always
matches and the check is never contradicted.

The digest is what makes a saved fight trustworthy across restarts: if the
replay may drift and still be accepted, a saved encounter proves nothing.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo import nat20_worker
from solo.app import create_app
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tests'))
from test_nat20_command_cycle import model, resolved  # noqa: E402


def test_a_forged_digest_is_refused(tmp_path):
    store = Store(tmp_path / 'forged.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Проверка отпечатка', seed=5)['id']
    app = create_app(store.path, model())
    with TestClient(app) as client:
        resolved(client, store, cid, 'check', 'check')
        resolved(client, store, cid, 'start', 'start')
        resolved(client, store, cid, 'move', 'move')
    combat = store.campaign(cid)['state']['nat20']['combat']
    assert combat['commands'], 'no committed command to replay'

    attack = dict(combat)
    records = [dict(item) for item in combat['commands']]
    records[0]['result_digest'] = '0' * 64
    attack['commands'] = records
    with pytest.raises(ValueError, match='replay diverged'):
        asyncio.run(nat20_worker.replay(attack))

    opening = dict(combat)
    opening['initial_digest'] = '0' * 64
    with pytest.raises(ValueError, match='initial replay diverged'):
        asyncio.run(nat20_worker.replay(opening))

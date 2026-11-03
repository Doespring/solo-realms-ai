"""One player line makes exactly one public turn.

Section 5. solo/game.py builds the reply from a committed engine transaction,
a buffered narration and a journal entry; there are several places that could
add a second history row (the intent, the narration, the grounding check).
The campaign answer's 'history' length is what the table sees, so it is the
honest measure: after two commands it must be two longer than after one.
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
from test_nat20_command_cycle import model, resolved  # noqa: E402


def _history(client, cid):
    return len(client.get('/api/campaigns/%s' % cid).json()['history'])


def test_one_line_is_one_public_turn(tmp_path):
    store = Store(tmp_path / 'turns.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Счёт ходов', seed=5)['id']
    app = create_app(store.path, model())
    with TestClient(app) as client:
        resolved(client, store, cid, 'one', 'check')
        after_one = _history(client, cid)
        resolved(client, store, cid, 'two', 'start')
        after_two = _history(client, cid)
    assert after_one == 1, 'the first command made %d public turns' % after_one
    assert after_two - after_one == 1, (
        'the second command made %d public turns' % (after_two - after_one))

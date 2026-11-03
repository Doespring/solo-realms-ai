"""The combat API is not a fallback backend for a campaign without Nat20.

`CommandEngine` refuses a fight for such a campaign on `/commands`, but the
combat endpoints are mounted separately and used to reach the Python combat
engine directly: `POST /combat/start` returned 200 and a whole fight could be
played through `/combat/command`. That is the silent legacy calculator the
integration forbids. This test drives the public HTTP surface a player uses.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault('SOLO_TEST', '1')

from solo.app import create_app
from solo.llm import CommandMockLLM

CASES = [
    ('combat/start', dict(request_id='cs', expected_version=0, confirm=True)),
    ('combat/command', dict(request_id='cc', expected_version=0, kind='attack', target='foe-1')),
]


@pytest.mark.parametrize('path,body', CASES)
def test_a_campaign_without_nat20_cannot_fight_through_the_combat_api(tmp_path, path, body):
    app = create_app(tmp_path / 'ob.db', CommandMockLLM({}))
    with TestClient(app) as client:
        created = client.post('/api/campaigns', json={'name': 'Обход', 'hero': {'name': 'Мирон'}})
        assert created.status_code == 200, created.text
        cid = created.json()['id']
        state = app.state.store.campaign(cid)['state']
        assert 'nat20' not in state, 'this test is about a campaign without the engine'
        before = app.state.store.campaign(cid)
        answer = client.post('/api/campaigns/%s/%s' % (cid, path), json=body)
        assert answer.status_code == 409, answer.text
        assert answer.json()['detail']['code'] == 'command_backend_required'
        # The refusal is not a half-started fight.
        assert app.state.store.campaign(cid) == before
        assert not (app.state.store.campaign(cid)['state'].get('combat') or {}).get('active')


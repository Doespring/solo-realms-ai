"""A damaged record must not turn the whole campaign into a server error.

The stored master record was merged with defaults as a mapping. A row whose
master had been written as a list raised inside the request and the player got
a 500 on opening the campaign - a broken product instead of a scene.

A damaged record is now reported as missing and replaced with defaults, so the
campaign still opens and still saves.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path/'master.sqlite3', MockLLM())
    with TestClient(app, raise_server_exceptions=False) as c:
        cid = c.post('/api/campaigns', json=dict(
            name='Проверка', hero=dict(name='Мирон', hp=12, max_hp=12), world_mode='story')).json()['id']
        yield c, app, cid


def test_a_well_formed_campaign_opens(client):
    c, app, cid = client
    assert c.get('/api/campaigns/' + cid).status_code == 200


def test_a_damaged_master_does_not_become_a_server_error(client):
    c, app, cid = client
    with app.state.store.connection(True) as db:
        db.execute("UPDATE campaigns SET master=? WHERE id=?", ('[1,2,3]', cid))
    response = c.get('/api/campaigns/' + cid)
    assert response.status_code == 200, 'a damaged record must not break the campaign'
    body = response.json()
    assert isinstance(body['master'], dict), 'the caller still receives usable defaults'
    assert body['state']['location'], 'the scene is still there'


def test_a_damaged_master_is_not_written_back_silently(client):
    c, app, cid = client
    with app.state.store.connection(True) as db:
        db.execute("UPDATE campaigns SET master=? WHERE id=?", ('[1,2,3]', cid))
    c.get('/api/campaigns/' + cid)
    # Reading must not rewrite history: the stored value stays as it was until
    # something genuinely saves the campaign.
    with app.state.store.connection() as db:
        row = db.execute('SELECT master FROM campaigns WHERE id=?', (cid,)).fetchone()
    assert row[0] == '[1,2,3]', 'a read is not a repair'

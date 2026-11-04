"""A browser page from elsewhere must not drive the local server."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app


def test_a_foreign_page_cannot_post(tmp_path):
    app = create_app(tmp_path / 'origin.sqlite3')
    with TestClient(app) as client:
        answer = client.post('/campaigns', json={},
                             headers={"origin": "https://evil.example"})
    assert answer.status_code == 403, (
        'a foreign page posted: %s' % answer.status_code)
    assert 'Чужой Origin' in answer.json()['detail'], answer.json()
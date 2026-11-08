"""Cloud spending must be switched on by the player, never silently."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app


def _profile(**extra):
    body = {'id': '11111111-1111-1111-1111-111111111111',
            'base_url': 'https://api.example/v1'}
    body.update(extra)
    return body


def test_an_enabled_profile_is_not_stored_silently(tmp_path):
    app = create_app(tmp_path / 'enable.sqlite3')
    with TestClient(app) as client:
        answer = client.post('/api/ai/profiles',
                             json=_profile(enabled=True))
    assert answer.status_code != 200, (
        'the cloud was switched on without asking: %s' % answer.status_code)
    text = str(answer.json().get('detail'))
    assert 'Включить' in text, text
"""Deleting a key really removes it, and the answer tells the truth."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud_settings import CloudProfile

PID = '11111111-1111-1111-1111-111111111111'


def test_the_key_is_gone_after_deleting_it(tmp_path):
    app = create_app(tmp_path / 'key.sqlite3')
    app.state.cloud_settings.save(CloudProfile.model_validate(
        {'id': PID, 'base_url': 'https://api.example/v1'}))
    app.state.cloud_settings.vault.set(PID, 'sk-test')

    with TestClient(app) as client:
        answer = client.delete(f'/api/ai/profiles/{PID}/key')
    assert answer.status_code == 200, answer.text
    assert app.state.cloud_settings.vault.exists(PID) is False, (
        'the answer claimed the key was gone but it is still there')
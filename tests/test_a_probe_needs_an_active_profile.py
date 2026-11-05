"""A connection probe runs only on the profile the player activated."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud_settings import CloudProfile

PID = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'
MODEL = {'model': 'test-model'}


def _profile(pid):
    return CloudProfile.model_validate({
        'id': pid, 'base_url': 'https://api.example/v1', 'enabled': True,
        'total_budget': '5', 'interpreter': MODEL, 'narrator': MODEL})


def test_a_probe_is_refused_for_an_inactive_profile(tmp_path):
    app = create_app(tmp_path / 'probe.sqlite3')
    app.state.cloud_settings.save(_profile(PID))
    app.state.cloud_settings.save(_profile(OTHER))
    app.state.cloud_settings.activate(OTHER)
    app.state.cloud_settings.vault.set(OTHER, 'sk-test')
    app.state.cloud_settings.vault.set(PID, 'sk-test')
    assert app.state.cloud_settings.active != PID

    with TestClient(app) as client:
        answer = client.post(f'/api/ai/profiles/{PID}/probe')
    assert answer.status_code != 200, (
        'a probe ran on an inactive profile: %s' % answer.status_code)
    text = str(answer.json().get('detail'))
    assert 'активируйте' in text, (
        'the probe went past the activation check: %s' % text)
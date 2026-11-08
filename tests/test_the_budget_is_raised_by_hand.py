"""The spending ceiling changes only through its own action."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud_settings import CloudProfile

PID = '11111111-1111-1111-1111-111111111111'


def _profile(**extra):
    body = {'id': PID, 'base_url': 'https://api.example/v1'}
    body.update(extra)
    return body


def test_the_budget_cannot_be_raised_by_saving_a_profile(tmp_path):
    app = create_app(tmp_path / 'budget.sqlite3')
    app.state.cloud_settings.save(CloudProfile.model_validate(
        _profile(total_budget='5')))
    with TestClient(app) as client:
        answer = client.post('/api/ai/profiles', json=_profile(total_budget='500'))
    assert answer.status_code != 200, (
        'the ceiling was raised silently to %s' % answer.status_code)
    text = str(answer.json().get('detail'))
    assert 'лимит' in text, text
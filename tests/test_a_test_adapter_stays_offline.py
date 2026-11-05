"""A test adapter must not be wired to the real cloud profile."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

import solo.app as app_module
from solo.app import create_app


def test_a_test_adapter_cannot_set_up_the_cloud(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'cloud.sqlite3')

    class Adapter:
        """Stand-in for the offline adapter: not a CloudLLM."""

    app.state.game.llm = Adapter()
    with TestClient(app) as client:
        answer = client.post('/api/ai/setup', json={'provider': 'Groq',
                                                    'api_key': 'sk-test'})
    assert answer.status_code != 200, (
        'the adapter was wired to the cloud: %s' % answer.status_code)
    text = str(answer.json().get('detail'))
    assert 'тестового адаптера' in text, text
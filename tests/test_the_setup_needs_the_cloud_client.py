"""The cloud setup route must not run against a test adapter."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app


class Adapter:
    """A local stand-in that shares nothing with the cloud client."""


def test_the_setup_route_refuses_a_test_adapter(tmp_path):
    app = create_app(tmp_path / 'setup.sqlite3', llm=Adapter())
    body = {"provider": "RouterAI", "api_key": "sk-test",
            "free_account_confirmed": True}
    with TestClient(app) as client:
        answer = client.post('/api/ai/setup', json=body)
    assert answer.status_code != 200, (
        'the setup ran on a test adapter: %s' % answer.status_code)
    assert 'тестового адаптера' in str(answer.json().get('detail'))
"""A test adapter must not reach the cloud catalog."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud_settings import CloudProfile

PID = '11111111-1111-1111-1111-111111111111'


class Adapter:
    """Stands in for a local test adapter: shares nothing with CloudLLM."""


def _test_app(root):
    app = create_app(root / 'catalog.sqlite3', llm=Adapter())
    return app


def test_the_catalog_is_closed_to_a_test_adapter(tmp_path):
    app = _test_app(tmp_path)
    app.state.cloud_settings.save(CloudProfile.model_validate(
        {'id': PID, 'base_url': 'https://api.example/v1'}))
    with TestClient(app) as client:
        answer = client.get(f'/api/ai/profiles/{PID}/models')
    assert answer.status_code != 200, (
        'the catalog answered a test adapter: %s' % answer.status_code)
    assert 'Каталог недоступен' in str(answer.json().get('detail'))


def test_the_endpoints_are_closed_to_a_test_adapter(tmp_path):
    app = _test_app(tmp_path)
    with TestClient(app) as client:
        answer = client.get(f'/api/ai/profiles/{PID}/endpoints/connection')
    assert answer.status_code != 200, (
        'the endpoints answered a test adapter: %s' % answer.status_code)
    assert 'Каталог недоступен' in str(answer.json().get('detail'))
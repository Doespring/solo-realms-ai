"""A drained hero may not start above the ceiling exhaustion leaves."""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero


def test_a_drained_hero_cannot_start_above_the_ceiling(tmp_path):
    app = create_app(tmp_path / 'drained.sqlite3')
    data = Hero(name="Герой").model_dump(mode="json")
    data["exhaustion"] = 6
    with TestClient(app) as client:
        answer = client.post('/api/campaigns',
                             json={'name': 'Кампания', 'hero': data})
    assert answer.status_code != 200, (
        'a drained hero started at full hits: %s' % answer.status_code)
    assert 'Хиты' in str(answer.json().get('detail')) or (
        'Хиты' in str(answer.json().get('detail'))), (
        'refused for another reason: %s' % answer.json())
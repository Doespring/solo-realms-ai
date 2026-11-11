"""The server rejects what the filtered UI never offers; UI and engine agree."""
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from solo.app import create_app
from solo.preview_mock import PreviewMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


def build(klass, level, subclass=None, species='dwarf'):
    body = legal(species_slug=species, class_slug=klass, level=level, equipment=['dagger'])
    if subclass:
        body['subclass_slug'] = subclass
    return body


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp('ui') / 'server.sqlite3'
    with TestClient(create_app(path, PreviewMock(), experimental=True, offline_mock=True)) as c:
        yield c


@pytest.mark.parametrize('klass,level,subclass,ok', [
    ('fighter', 3, 'champion', True),
    ('fighter', 3, 'evoker', False),      # wrong class
    ('fighter', 2, 'champion', False),    # below the level gate
    ('wizard', 3, 'evoker', True),
    ('wizard', 3, 'champion', False),
])
def test_preview_enforces_the_same_class_and_level_rules_the_ui_filters(client, klass, level, subclass, ok):
    response = client.post('/api/prototype/build/preview',
                           json=dict(build=build(klass, level, subclass), prepared_spells=[]))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['valid'] is ok, payload
    if not ok:
        assert payload['issues'], payload

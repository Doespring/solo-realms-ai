"""A change to the interface is a change too.

The first drift check compared only the loaded backend modules, so editing
web/play.js left drift=False. The message "the files on disk changed" was
therefore false for the interface, and a screen built from another file was
assumed fine. The page is served from disk on every request, so it takes effect
without a restart - that is exactly why it needs its own answer.
"""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path

from test_dialogue_paging_api import MockLLM
from solo.app import create_app
from solo.build_identity import build_identity
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def health(tmp_path):
    return TestClient(create_app(tmp_path/'h.sqlite3', MockLLM())).get('/api/health').json()


def test_health_reports_the_interface_separately(tmp_path):
    body = health(tmp_path)
    assert 'ui' in body and 'ui_drift' in body, 'the interface needs its own answer'
    assert body['ui_drift'] is False, 'an untouched tree has not drifted'


def test_editing_an_interface_file_is_noticed(tmp_path):
    before = build_identity(refresh=True)
    if before.get('ui_drift'):
        import pytest
        pytest.skip('the interface already differs from what the server started with')
    target = ROOT/'web'/'play.js'
    original = target.read_bytes()
    try:
        target.write_bytes(original + b'\n// ui drift probe\n')
        after = build_identity(refresh=True)
        assert after['ui_drift'] is True, 'a changed interface file must show'
        assert after['drift'] is True, 'and it counts as drift overall'
        assert after['build'] == before['build'], 'the server itself did not change'
    finally:
        target.write_bytes(original)
    back = build_identity(refresh=True)
    assert back['ui_drift'] is False and back['drift'] is False

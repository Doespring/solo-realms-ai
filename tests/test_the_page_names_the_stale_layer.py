"""The health answer must name WHICH layer diverged, and coverage by path.

Two defects found by the verifier critic (round 2):

1. ui_disk was computed (build_identity.py:124) but never sent. With only web/*
   edited the server still runs its own current code, yet the page said
   "его код на диске изменён" and blamed the server.
2. covered held module NAMES ('solo.app'), not paths, so a caller asking
   "is this file covered?" matched nothing.
"""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.build_identity import build_identity

from test_dialogue_paging_api import MockLLM


def test_health_says_which_layer_diverged(tmp_path):
    api = TestClient(create_app(tmp_path/'h.sqlite3', MockLLM()))
    body = api.get('/api/health').json()
    for key in ('drift', 'disk', 'ui', 'ui_drift', 'ui_disk'):
        assert key in body, f'health must report {key}, otherwise the page guesses'


def test_coverage_can_be_asked_about_a_file():
    identity = build_identity(refresh=True)
    assert identity['covered_files'], 'file paths must be listed, not only module names'
    assert all(str(item).endswith('.py') for item in identity['covered_files']), identity['covered_files'][:3]
    assert any(item.startswith('solo/') for item in identity['covered_files'])


def test_module_names_are_still_reported():
    identity = build_identity(refresh=True)
    assert 'solo.app' in identity['covered'], 'existing callers read module names'


def test_a_changed_page_does_not_claim_the_server_is_old():
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1]/'web'/'play.js').read_text(encoding='utf-8')
    assert 'uiOnly' in text, 'the page must tell an old page from an old server'
    assert 'Обновите страницу.' in text

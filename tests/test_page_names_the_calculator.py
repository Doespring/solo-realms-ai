"""The page must say which calculator answers a mechanical command.

Section 7.1 forbids a silent fallback. A campaign created before the engine
existed runs its fights in the plain Python service; that cannot be changed
without a migration, but it must not be invisible either. The state response
now names the backend, so the table can see it.
"""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM
from scripts.command_smoke import setup


def _page(tmp_path):
    store, campaign, game = setup(tmp_path / 'db', llm=MockLLM())
    api = TestClient(create_app(store.path, MockLLM())) if hasattr(store, 'path') else None
    if api is None:
        raise AssertionError('Store exposes no path; see scripts/command_smoke.setup')
    return api.get(f'/api/campaigns/{campaign}').json()


def test_a_legacy_campaign_names_its_calculator(tmp_path):
    page = _page(tmp_path)
    assert page.get('rules_backend') == 'legacy', page.get('rules_backend')
    assert page.get('rules_backend_note'), 'the player is told in words'


def test_the_note_does_not_hide_mechanics(tmp_path):
    note = _page(tmp_path).get('rules_backend_note') or ''
    assert 'упрощённым' in note, note

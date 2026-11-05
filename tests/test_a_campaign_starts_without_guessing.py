"""Preparation survives an unnamed level, party size and opening place.

The table actually opening is covered by tests/test_material_analyzer_open_table.py
(launch through /api/prototype/campaigns); this file covers the preparation step.
"""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient
from solo.app import create_app
from test_preparation_pipeline import PreparationFixture
from test_material_analyzer_entry import idea, prepared, result_of
from test_nat20_prototype import build


def body_of(client, url):
    draft_id = url.rsplit('/', 1)[-1]
    return client.app.state.creation.owned(draft_id)['body']


def test_the_pasted_premise_without_level_or_party_stays_playable(tmp_path):
    llm = PreparationFixture(result=idea())
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        url, prep, status = prepared(client)
        result = result_of(client, status)
        assert result['ready'], result.get('quarantine')
        body = body_of(client, url)
        assert body['start_name'] == ''
        assert body['questions'], 'the Master asks instead of guessing'


def test_a_named_start_without_levels_needs_no_guess(tmp_path):
    payload = idea()
    payload['start'] = 'Доки Глубоководья'
    llm = PreparationFixture(result=payload)
    with TestClient(create_app(tmp_path / 'solo2.sqlite3', llm)) as client:
        url, prep, status = prepared(client)
        result = result_of(client, status)
        assert result['ready'], result.get('quarantine')
        body = body_of(client, url)
        assert body['start_name'] == 'Доки Глубоководья'
        assert body['opening']

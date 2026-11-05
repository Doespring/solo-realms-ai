"""The public API must never carry the GM's private material.

Section 4 of the task: 'not only browser markup is protected, but the public API,
the narrator's context and the materials available to the player'. The two fields
that hold what the party must not know are 'secrets' and 'sealed_text', so they are
checked on the real responses a player can receive.
"""
import os
os.environ['SOLO_TEST']='1'

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dialogue_paging_api import MockLLM  # noqa: E402

from solo.app import create_app  # noqa: E402

HERO = {'name': 'Проба', 'hp': 12,
        'abilities': {k: 12 for k in ('strength', 'dexterity', 'constitution',
                                      'intelligence', 'wisdom', 'charisma')}}

PRIVATE = ('secrets', 'sealed_text')


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / 's.sqlite3', MockLLM()),
                      raise_server_exceptions=False)


def make(client):
    answer = client.post('/api/campaigns', json={
        'name': 'Секрет', 'hero': HERO, 'ruleset': 'dnd2024',
        'world_mode': 'story', 'map_travel': True})
    assert answer.status_code == 200, answer.text
    return answer


def test_creation_does_not_return_private_fields(client):
    body = json.dumps(make(client).json(), ensure_ascii=False)
    for field in PRIVATE:
        assert field not in body, f'{field} leaked from creation'


def test_reading_the_campaign_does_not_return_private_fields(client):
    campaign = make(client).json()['id']
    answer = client.get('/api/campaigns/' + campaign)
    assert answer.status_code == 200
    body = json.dumps(answer.json(), ensure_ascii=False)
    for field in PRIVATE:
        assert field not in body, f'{field} leaked from the campaign read'


def test_the_page_does_not_embed_the_campaign_state(client):
    # The screen is plain markup and fetches data itself; if a campaign snapshot
    # were baked into it, private fields could ride along unnoticed.
    campaign = make(client).json()['id']
    text = client.get('/').text
    assert campaign not in text, 'the page must not embed a campaign snapshot'
    for field in PRIVATE:
        assert field not in text, f'{field} appears in the page'

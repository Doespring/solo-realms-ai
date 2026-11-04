"""Ability scores are bounded from the served schema down to the engine.

Two rules hold at once. Nat20's own JSON Schema leaves the six scores unbounded,
so a number input accepted 999 and the derived sheet kept it (strength 999 ->
modifier +494); these tests pin the ceiling Nat20 itself enforces (SRD 5.2
increases stop at 20) on both the served schema and the engine entry point.

And six arbitrary numbers are not a character: a score array has to come from a
generation method (Standard Array or Point Buy), so 1/20 as raw inputs are
refused as firmly as 999 is.
"""
import json
from pathlib import Path
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402

SCORES = ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')
BASE = legal(class_slug='fighter', species_slug='human', level=1, equipment=[])


@pytest.fixture(scope='module')
def client():
    app = create_app(Path(tempfile.mkdtemp()) / 'p.sqlite3', MockLLM(),
                     local_port=1234, experimental=True, offline_mock=True)
    with TestClient(app) as test_client:
        yield test_client


def post(client, build):
    return client.post('/api/prototype/build/preview', json={'build': build, 'prepared_spells': []})


@pytest.mark.parametrize('score', [999, 0, -5, 21])
def test_out_of_range_score_is_rejected_with_a_readable_message(client, score):
    build = {**BASE, 'ability_scores': {**BASE['ability_scores'], 'strength': score}}
    response = post(client, build)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['valid'] is False, f'strength={score} was accepted: {json.dumps(body)[:400]}'
    assert body['issues'], body
    message = ' '.join(str(issue) for issue in body['issues'])
    assert 'strength' in message and str(score) in message, message
    assert '1' in message and '20' in message, message


def test_a_score_no_generation_method_could_produce_is_refused(client):
    """1 and 20 are inside the engine bound but outside every legal array."""
    for score in (1, 20):
        build = {**BASE, 'ability_scores': {**BASE['ability_scores'], 'strength': score}}
        body = post(client, build).json()
        assert body['valid'] is False, f'strength={score} was accepted: {json.dumps(body)[:400]}'
        message = ' '.join(str(issue) for issue in body['issues'])
        assert 'point_buy' in message or 'standard_array' in message, message


def test_a_legal_standard_array_is_accepted(client):
    body = post(client, BASE).json()
    assert body['valid'] is True, body.get('issues')
    assert body['sheet']['ability_scores']['strength'] == 15


def test_a_build_without_a_generation_method_is_refused(client):
    build = {key: value for key, value in BASE.items() if key != 'ability_score_method'}
    body = post(client, build).json()
    assert body['valid'] is False
    message = ' '.join(str(issue) for issue in body['issues'])
    assert 'standard_array' in message and 'point_buy' in message, message


def test_the_served_schema_publishes_the_same_bounds(client):
    from dnd5e_engine.rules.character import MAX_SCORE_FROM_INCREASES
    schema = client.get('/api/prototype/options').json()['build_schema']
    scores = schema['$defs']['AbilityScores']['properties']
    assert set(scores) == set(SCORES)
    for name, node in scores.items():
        assert node['minimum'] == 1, (name, node)
        assert node['maximum'] == MAX_SCORE_FROM_INCREASES, (name, node)


def test_an_absurd_score_never_reaches_a_sheet(client):
    build = {**BASE, 'ability_scores': {**BASE['ability_scores'], 'strength': 999}}
    body = post(client, build).json()
    assert body['valid'] is False
    assert 'sheet' not in body

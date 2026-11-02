"""A real campaign books the printed reward, and never a zero receipt."""
import os
os.environ['SOLO_TEST'] = '1'
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.preview_mock import PreviewMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


def build_app(tmp_path, port=4711):
    return create_app(tmp_path / 'reward.sqlite3', PreviewMock(), local_port=port,
                      experimental=True, offline_mock=True)


def create_campaign(api):
    created = api.post('/api/prototype/campaigns', json=dict(
        name='Арден', request_id='create-reward', adventure='authored',
        description='Погреб.', build=legal(class_slug='fighter', species_slug='human', background='soldier'),
        prepared_spells=[], encounter_monsters=['rat'], encounter_attack_bonuses={'rat': 0},
        encounter_attack_sources={'rat': 'Authored smoke encounter: explicit +0 selected by test author.'}))
    assert created.status_code == 200, created.text
    return created.json()['id']


def test_a_printed_reward_is_booked_with_its_page(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as api:
        cid = create_campaign(api)
    current = app.state.store.campaign(cid)
    runtime = current['state']['nat20']
    source = runtime['definition']['encounters']['opening']['reward']['source']
    runtime['definition']['encounters']['opening']['reward'].update(
        kind='printed_experience', experience=100, page=14)
    runtime['experience'] = 100
    runtime['rewards'] = [dict(id=runtime['definition']['encounters']['opening']['reward']['id'],
                              encounter_id='opening', combat_id='opening', experience=100,
                              outcome_sha256='0' * 64, source=source)]
    import json
    with app.state.store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=?, version=version+1 WHERE id=?',
                   (json.dumps(current['state'], ensure_ascii=False), cid))
    saved = app.state.store.campaign(cid)
    runtime = saved['state']['nat20']
    assert runtime['experience'] == 100
    assert runtime['rewards'][0]['experience'] == 100
    assert runtime['definition']['encounters']['opening']['reward']['page'] == 14


def test_the_definition_still_round_trips_through_storage(tmp_path):
    app = build_app(tmp_path, port=4712)
    with TestClient(app) as api:
        cid = create_campaign(api)
    current = app.state.store.campaign(cid)
    runtime = current['state']['nat20']
    reward = runtime['definition']['encounters']['opening']['reward']
    assert 'experience' not in reward, 'an unpriced native reward keeps its old shape'
    assert 'page' not in reward

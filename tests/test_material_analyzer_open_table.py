"""Scenario: the Master asks what the material never said, hears the answer, keeps it.

A prepared world whose book never printed a level and never named its opening
place must still open the table. The Master asks in dialogue; the missing answer
does not lock the door, and it does not silently invent either. These checks use
the offline provider only: no model profile, network or paid call.
"""
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'tests' / 'fixtures'))

from solo.app import create_app
from solo.preparation import Draft
from solo.prototype_profile import application_database, prepare_profile
from solo import open_settings
from nat20_builds import legal
from test_preparation_pipeline import PreparationFixture, terminal


def idea(payload=None):
    """A world the source never placed: two places, a person, a printed road."""
    return dict(title='Долина Ири', ruleset='dnd2024', start='', entities=[
        dict(key='town', kind='location', origin='original',
             data=dict(name='Городок', description='Дома стоят на склоне, мост уходит на другой берег.')),
        dict(key='bridge', kind='location', origin='original',
             data=dict(name='Мост', description='Балки скрипят под ногами, за рекой темнеет лес.')),
        dict(key='olga', kind='npc', location='Городок', origin='original',
             data=dict(name='Ольга', character='Стражник у ворот.', motivation='Не пускает за мост после дождя.')),
        dict(key='road', kind='route', origin='original',
             data={'from': 'Городок', 'to': 'Мост', 'meters': 200, 'minutes': 5,
                   'description': 'Дорога ведёт от городка к мосту', 'explorable': True}),
    ])


@pytest.fixture()
def place(tmp_path):
    """A prepared, confirmed world launched as a campaign with no agreed level."""
    client = TestClient(create_app(
        application_database(prepare_profile(tmp_path / 'profile') / 'solo.sqlite3'),
        PreparationFixture(idea), experimental=True))
    # The preparation runs on the app's own loop. Entering the client keeps that
    # loop alive for the whole scenario; without it the run is cancelled midway
    # and the world arrives as "interrupted" instead of prepared.
    client.__enter__()
    draft = client.post('/api/creation/drafts').json()
    url = '/api/creation/drafts/' + draft['id']
    client.put(url, json=dict(revision=draft['revision'],
                              description='Долина Ири, городок у реки, мост закрыт после дождя'))
    run = client.post(url + '/prepare',
                      json=dict(request_id='idea', consent_to_send=True, ruleset='dnd2024')).json()['id']
    done = terminal(client, run)
    assert done['ready'], json.dumps(done.get('message'), ensure_ascii=False)
    readiness = client.get(url + '/world-readiness').json()
    client.post(url + '/confirm-world',
                json=dict(expected_revision=readiness['revision'], run_id=run, confirm=True))
    app = client.app
    current = app.state.preparation.get(draft['id'])
    body = current['body']
    body['background_confirmed'] = True
    app.state.preparation.save(draft['id'], Draft.model_validate(body), current['revision'])
    launched = client.post('/api/prototype/campaigns', json=dict(
        name='Рив', build=legal(class_slug='fighter', species_slug='human', level=1),
        prepared_spells=[], adventure='prepared:' + draft['id'],
        description='Долина Ири, городок у реки.', encounter_monsters=[],
        mechanics_ruleset='dnd2024', rules_confirmed=True, hero_confirmed=True,
        background_confirmed=True, hero_background='Ищет дорогу через мост',
        adventure_revision=app.state.preparation.get(draft['id'])['revision'],
        request_id='start-1'))
    assert launched.status_code == 200, launched.text[:400]
    yield client, draft['id'], launched.json()['id']
    client.__exit__(None, None, None)


def state(client, campaign):
    return client.app.state.game.store.campaign(campaign)['state']


def test_world_without_level_opens_the_table(place):
    """No level in the material does not keep the player outside the story."""
    client, draft, campaign = place
    world = state(client, campaign)
    assert (world.get('preparation') or {}).get('open_questions'), 'the missing level must travel with the campaign'
    assert not (world.get('preparation') or {}).get('answers'), 'nothing is answered by being silent'
    assert world['preparation']['background_confirmed'] is True
    assert len(state(client, campaign)['locations']) == 2, 'the printed places are in play'


def test_master_asks_the_missing_question_first(place):
    """The first question is the first thing the Master settles, in its own words."""
    client, draft, campaign = place
    world = state(client, campaign)
    asked = open_settings.next_question(world)
    assert asked and asked['field'] == 'start'
    assert open_settings.question_text(asked) == 'С какого места начинаем?'


def test_answer_is_heard_kept_and_not_asked_again(place):
    """A named place is recorded as the player's decision and leaves the queue."""
    client, draft, campaign = place
    world = state(client, campaign)
    def resolve(text):
        kind, found = open_settings.match_place(world, text)
        return found if kind == 'known' else None
    heard = open_settings.answer(world, 'Начнём в Городке', resolver=resolve)
    assert heard is not None
    field, value, entry = heard
    assert (field, value) == ('start', 'Городок'), 'the printed name is kept, not the declined form'
    assert entry['field'] == 'start', 'the answered question is the one that was asked'
    kept_origin = world['preparation']['answers']['start']['provenance']
    assert kept_origin == 'player_answer', 'an answer given by the table must carry its origin'
    # Persist through the same atomic event transaction the game uses, so the
    # restart below reads the saved campaign, not the in-memory dict.
    def record(live):
        live.setdefault('preparation', {}).setdefault('answers', {})['start'] = dict(
            value='Городок', text='Начнём в Городке', provenance='player_answer')
        return [dict(type='scripted', kind='notice', text='Место начала: Городок.')]
    client.app.state.game.store.record_scenario(campaign, 'answer-start', 'Ответ в диалоге', record)
    kept = state(client, campaign)['preparation']['answers']
    assert kept['start']['value'] == 'Городок'
    assert kept['start']['text'] == 'Начнём в Городке'
    assert open_settings.next_question(state(client, campaign)) is not None
    assert open_settings.next_question(state(client, campaign))['field'] != 'start', 'the same question must not repeat'


def test_level_dependent_step_waits_for_the_answer(place):
    """Entering dialogue is open; a level-dependent step waits for the table."""
    client, draft, campaign = place
    world = state(client, campaign)
    assert 'ждёт ответа' in open_settings.pending_for_mechanics(world)
    to_dict = json.loads(json.dumps(world))
    to_dict['preparation']['answers']['level'] = dict(value=5, text='Пятый уровень', provenance='player_answer')
    assert open_settings.pending_for_mechanics(to_dict) == '', 'an agreed level releases the dependent step'


def test_unknown_place_is_a_question_not_a_refusal(place):
    """An unfamiliar name is a wish to agree, never a silent creation or a 409."""
    client, draft, campaign = place
    world = state(client, campaign)
    kind, value = open_settings.match_place(world, 'Чёрная цитадель')
    assert kind == 'add', 'an unknown place is offered for agreement'
    assert value == 'Чёрная цитадель', 'the player spelling is kept'
    kind, value = open_settings.match_place(world, 'Городке')
    assert (kind, value) == ('known', 'Городок'), 'a real place is recognised through its case'
    kind, message = open_settings.match_place(world, '')
    assert kind == 'ask' and message, 'an empty answer asks again instead of failing'

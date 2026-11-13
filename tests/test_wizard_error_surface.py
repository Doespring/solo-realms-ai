"""Отказ API виден игроку и остаётся в журнале, а не превращается в «не удалось».

Мобильный репорт r248: редактор героя отправлял пустое имя, сервер отвечал 422
списком pydantic, клиент показывал общее «Не удалось обработать запрос», а в
журнале не было ни строки. Эти тесты держат обе половины: причина в ответе и
трейсбек в журнале.
"""
import logging

from fastapi.testclient import TestClient

from solo.app import create_app, validation_message
from solo.preview_mock import PreviewMock


def make_app(tmp_path, monkeypatch):
    monkeypatch.setattr('solo.nat20_api.secrets.randbits', lambda bits: 5)
    return create_app(tmp_path / 'game.sqlite3', PreviewMock(), experimental=True, offline_mock=True)


def test_a_refused_form_names_the_field_in_russian(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as api:
        # Полное, законное тело кампании с одним дефектом: у героя нет имени.
        body = dict(name='', request_id='r248-probe', adventure='sandbox',
                    build=dict(species_slug='human', class_slug='fighter', level=1,
                               ability_scores=dict(strength=15, dexterity=14, constitution=13,
                                                   intelligence=12, wisdom=10, charisma=8),
                               selected_choices=[]),
                    prepared_spells=[], description='', encounter_monsters=[],
                    rules_confirmed=True, hero_confirmed=True, background_confirmed=True)
        response = api.post('/api/prototype/campaigns', json=body)
    assert response.status_code == 422, response.text
    payload = response.json()
    # Форма списка `detail` не меняется: её читают другие клиенты и тесты.
    assert isinstance(payload['detail'], list) and payload['detail']
    assert payload['detail'][0]['loc'] == ['body', 'name']
    assert 'имя героя' in payload['message'] and 'пустое' in payload['message'], payload['message']


def test_a_refused_form_is_written_to_the_log(tmp_path, monkeypatch, caplog):
    app = make_app(tmp_path, monkeypatch)
    with caplog.at_level(logging.WARNING, logger='solo.app'):
        with TestClient(app) as api:
            api.post('/api/prototype/campaigns', json=dict(name=''))
    assert 'форма запроса отклонена' in caplog.text
    assert 'body.name' in caplog.text and 'string_too_short' in caplog.text


def test_an_unhandled_error_is_a_json_500_and_a_traceback(tmp_path, monkeypatch, caplog):
    app = make_app(tmp_path, monkeypatch)

    def boom():
        raise RuntimeError('бум r248')

    app.add_api_route('/api/_probe/boom', boom, methods=['POST'])
    with caplog.at_level(logging.ERROR, logger='solo.app'):
        with TestClient(app, raise_server_exceptions=False) as api:
            response = api.post('/api/_probe/boom', json={})
    assert response.status_code == 500, response.text
    payload = response.json()
    assert payload['detail']['code'] == 'internal'
    assert 'Внутренняя ошибка' in payload['detail']['message']
    assert 'необработанная ошибка' in caplog.text and '/api/_probe/boom' in caplog.text
    # Трейсбек, а не одна строка: без него причину в журнале не найти.
    assert 'RuntimeError' in caplog.text and 'бум r248' in caplog.text


def test_the_contract_keeps_the_name_the_player_typed(tmp_path, monkeypatch):
    """Сервер не теряет имя — терял клиент, поэтому имя проверяется отдельно."""
    app = make_app(tmp_path, monkeypatch)
    draft = dict(species_slug='human', class_slug='wizard', background_slug='sage', level=1,
                 ability_score_method='point_buy', name='Алрик', backstory='Ищет брата.',
                 ability_scores=dict(strength=15, dexterity=14, constitution=13,
                                     intelligence=12, wisdom=10, charisma=8))
    with TestClient(app) as api:
        response = api.post('/api/prototype/wizard/contract', json=dict(draft=draft, answers={}))
    assert response.status_code == 200, response.text
    assert response.json()['draft']['name'] == 'Алрик'
    assert response.json()['draft']['backstory'] == 'Ищет брата.'


def test_the_message_helper_speaks_about_an_unknown_field_without_english():
    rows = [{'loc': ['body', 'request_id'], 'type': 'missing', 'msg': 'Field required'}]
    message = validation_message(rows)
    assert 'номер запроса' in message and 'не передано' in message
    assert 'Field required' not in message


LEGAL = dict(species_slug='human', species_base='Human', class_slug='wizard',
             background_slug='sage', level=1, ability_score_method='point_buy',
             ability_scores=dict(strength=15, dexterity=14, constitution=13,
                                 intelligence=12, wisdom=10, charisma=8),
             selected_choices=['background:constitution+2,intelligence+1',
                               'skill:investigation', 'skill:religion', 'alert',
                               'skill:perception'],
             equipment_kits={'class:wizard': 'A', 'background:sage': 'A'},
             prepared_spells=['fire-bolt', 'light', 'mage-hand', 'magic-missile',
                              'shield', 'sleep', 'mage-armor'])


def test_a_nameless_hero_is_not_confirmable(tmp_path, monkeypatch):
    """ready=True с пустым именем — то, из-за чего кнопка горела, а создание отвечало 422."""
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as api:
        nameless = api.post('/api/prototype/wizard/contract',
                            json=dict(draft=dict(LEGAL, name=''), answers={})).json()
        named = api.post('/api/prototype/wizard/contract',
                         json=dict(draft=dict(LEGAL, name='Алрик'), answers={})).json()
    assert nameless['ready'] is True, nameless['issues'] or nameless['blocking']
    assert nameless['confirm_ready'] is False
    assert 'имя героя' in nameless['confirm_issues'][0]
    assert named['confirm_ready'] is True, named['confirm_issues']

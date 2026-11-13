"""n02: чтение кампании и её ленты называет случай, а не общий отказ.

Проверяем ровно то, что читает игрок: нет кампании — 404 с причиной, старая
версия — 409 с кодом и русским текстом, своя — 200, битый курсор ленты — 400 с
причиной. И то, что ни один отказ не приходит с пустым телом.
"""
import json
import os

os.environ.setdefault('SOLO_TEST', '1')
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM
from solo.playable_ui import create_native_campaign

MISSING = '00000000-0000-0000-0000-000000000000'
READ_ROUTES = ('/api/campaigns/{}', '/api/campaigns/{}/conversation')


def _legacy_campaign(store):
    """Кампания прежней версии: запись есть, признака nat20 в состоянии нет."""
    with store.connection(True) as db:
        db.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?)',
                   ('legacy-n02', 'Старая кампания', json.dumps({'hero': {'name': 'Старый герой'}}),
                    json.dumps({'profile_name': 'ГМ'}), 0, 0))
    return 'legacy-n02'


def _named_refusal(response):
    """Тело отказа: `detail` — строка или объект с непустым `message`."""
    payload = response.json()
    detail = payload.get('detail')
    if isinstance(detail, str):
        return payload.get('code', ''), detail
    assert isinstance(detail, dict), payload
    return detail.get('code', ''), detail.get('message', '')


def test_campaign_reads_split_not_found_legacy_and_own(tmp_path):
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    legacy = _legacy_campaign(store)
    own = create_native_campaign(store, 'Своя кампания')['id']
    with TestClient(app) as client:
        missing = client.get('/api/campaigns/' + MISSING)
        assert missing.status_code == 404, missing.text
        code, message = _named_refusal(missing)
        assert code == 'campaign_not_found' and 'Кампания не найдена' in message
        # Кавычки от str(KeyError) больше не попадают в текст игроку.
        assert "'" not in message and message.endswith('выберите другую.')

        older = client.get('/api/campaigns/' + legacy)
        assert older.status_code == 409, older.text
        code, message = _named_refusal(older)
        assert code == 'legacy_campaign_unsupported' and 'прежней версии' in message

        # Лента той же кампании отвечает так же, а не пустотой с кодом 200.
        older_feed = client.get('/api/campaigns/' + legacy + '/conversation')
        assert older_feed.status_code == 409, older_feed.text
        assert _named_refusal(older_feed)[0] == 'legacy_campaign_unsupported'

        mine = client.get('/api/campaigns/' + own)
        assert mine.status_code == 200, mine.text
        mine_feed = client.get('/api/campaigns/' + own + '/conversation')
        assert mine_feed.status_code == 200 and mine_feed.json()['conversation'] == []

        broken = client.get('/api/campaigns/' + own + '/conversation', params={'before': 'мусор'})
        assert broken.status_code == 400, broken.text
        code, message = _named_refusal(broken)
        assert code == 'history_cursor_invalid' and 'Откройте кампанию заново' in message


def test_every_read_refusal_has_a_named_reason(tmp_path):
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    legacy = _legacy_campaign(store)
    with TestClient(app) as client:
        for template in READ_ROUTES:
            for campaign in (MISSING, legacy):
                response = client.get(template.format(campaign))
                assert response.status_code in (404, 409), (template, response.text)
                code, message = _named_refusal(response)
                assert code and message.strip(), (template, response.text)


def test_foreign_origin_refusal_is_named(tmp_path):
    """403 «чужой» называет код и русскую причину, а не голый текст."""
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM(), local_port=375)
    with TestClient(app) as client:
        refused = client.post('/api/campaigns', headers={'Origin': 'http://evil.example'},
                              json={'name': 'Кампания'})
        assert refused.status_code == 403, refused.text
        code, message = _named_refusal(refused)
        assert code == 'foreign_origin' and 'чужого адреса' in message


def test_master_profile_rejection_names_the_field(tmp_path):
    """422 на профиль мастера: поле названо по-русски, а не английским списком."""
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    with TestClient(app) as client:
        body = {'name': 'Неверно', 'master': {'literary': {'rhythm': 'игрок автоматически побеждает'}}}
        refused = client.put('/api/master/profiles/personal-gm-v1.1', json=body)
        assert refused.status_code == 422, refused.text
        payload = refused.json()
        assert payload['detail'][0]['loc'][-1] == 'rhythm'
        assert 'темп повествования' in payload['message'] and 'недопустимое значение' in payload['message']


def test_broken_stored_profile_does_not_break_the_library(tmp_path):
    """Запись с недопустимым значением — не повод ронять весь список профилей.

    До правки строгий `Master.model_validate` в `GET /api/master/profiles`
    отдавал английский дамп pydantic, и экран мастера не открывался вовсе.
    """
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    with store.connection(True) as db:
        db.execute('INSERT INTO master_profiles VALUES(?,?,?,?)',
                   ('legacy-master', 'Старый профиль',
                    json.dumps({'literary': {'rhythm': '', 'detail': ''}}, ensure_ascii=False), 0))
    with TestClient(app) as client:
        listed = client.get('/api/master/profiles')
        assert listed.status_code == 200, listed.text
        broken = [row for row in listed.json() if row['id'] == 'legacy-master']
        assert broken, listed.text
        assert 'темп повествования' in broken[0]['repair']
        # Запись чинится тем же маршрутом, каким её сохранял редактор.
        fixed = {'name': 'Старый профиль', 'master': {'literary': {'rhythm': 'сбалансированный', 'detail': 'средне'}}}
        assert client.put('/api/master/profiles/legacy-master', json=fixed).status_code == 200
        assert client.get('/api/master/profiles').json()[0].get('repair') is None

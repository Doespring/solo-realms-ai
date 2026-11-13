"""Регресс r124: игровой экран Nat20 должен доходить до движка, а не получать 409.

Дефект, который ловит этот файл: `one_backend` в solo/playable_ui.py не считал
маршрут `/api/campaigns/<id>/screen/commands` сервисным, поэтому КАЖДАЯ кнопка
экрана («Начать бой», проверки, отдых) получала 409 command_backend_required.
Плюс web/game-screen.js слал относительный путь через api(), который сам
добавляет /api. Оба места проверяются здесь: серверная часть — живым запросом,
клиентская — чтением настоящего исходника.
"""
from pathlib import Path
import re

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.preview_mock import PreviewMock

ROOT = Path(__file__).resolve().parents[1]


def test_screen_commands_reach_the_engine(tmp_path, monkeypatch):
    monkeypatch.setattr('solo.nat20_api.secrets.randbits', lambda bits: 5)
    app = create_app(tmp_path / 'r124.sqlite3', PreviewMock())
    with TestClient(app) as client:
        cid = client.post('/api/nat20/campaigns', json=dict(name='Проверка', scene='lantern-post')).json()['id']
        packet = client.get(f'/api/campaigns/{cid}/screen').json()
        # Сцена сначала идёт разговором: бой предлагается после осмотра.
        dialogue = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
            request_id='r124-talk', expected_version=packet['snapshot']['version'], text='Осматриваю следы'))
        assert dialogue.status_code == 200, dialogue.text
        packet = client.get(f'/api/campaigns/{cid}/screen').json()
        command = next(c for c in packet['commands'] if c['parameters']['kind'] == 'start_combat')
        body = {k: command[k] for k in ('kind', 'parameters', 'expected_version')} | dict(request_id='r124-start')
        response = client.post(f'/api/campaigns/{cid}/screen/commands', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['kind'] == 'event'
        after = client.get(f'/api/campaigns/{cid}/screen').json()['snapshot']
        assert after['combat']['active'] is True
        # Повтор той же команды другим request_id — тем же маршрутом, без 409.
        second = client.post(f'/api/campaigns/{cid}/screen/commands', json=dict(
            body, request_id='r124-move', parameters=dict(kind='move', position=dict(x=2, y=1, z=0)),
            expected_version=client.get(f'/api/campaigns/{cid}/screen').json()['snapshot']['version']))
        assert second.status_code == 200, second.text


def test_other_legacy_backends_stay_closed(tmp_path):
    app = create_app(tmp_path / 'r124-legacy.sqlite3', PreviewMock())
    with TestClient(app) as client:
        cid = client.post('/api/nat20/campaigns', json=dict(name='Закрыто')).json()['id']
        for suffix in ('play', 'actions', 'combat/start', 'rest'):
            response = client.post(f'/api/campaigns/{cid}/{suffix}', json={})
            assert response.status_code == 409, (suffix, response.text)
            assert response.json()['detail']['code'] == 'command_backend_required'
        # r124: экранный маршрут Nat20 — сервисный, поэтому пустое тело получает
        # 422 от собственной валидации, а не 409 гейта. Осмысленное тело доходит
        # до движка (проверка ниже), а не к прежнему обработчику.
        empty = client.post(f'/api/campaigns/{cid}/screen/commands', json={})
        assert empty.status_code == 422, empty.text
        # Сам игровой экран Nat20 через тот же гейт проходит.
        screen = client.post(f'/api/campaigns/{cid}/screen/commands', json=dict(
            request_id='r124-gate', expected_version=0, kind='engine', parameters=dict(kind='check', check_id='athletics')))
        assert screen.status_code == 200, screen.text


def test_browser_sends_absolute_screen_commands_path():
    screen = (ROOT / 'web' / 'game-screen.js').read_text(encoding='utf-8')
    play = (ROOT / 'web' / 'play.js').read_text(encoding='utf-8')
    # api() умеет абсолютный базовый путь, иначе он добавит /api дважды.
    assert re.search(r"async function api\(path,body,method=body===undefined\?'GET':'POST',absolute\)", play)
    assert "fetch((absolute||'/api')+path" in play
    # game-screen.js передаёт абсолютный базовый путь для экранных команд.
    assert "screen/commands',command,undefined,'/api'" in screen

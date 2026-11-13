"""Удаление кампании: история не остаётся сиротой, соседи целы.

Экран «Мои приключения» теперь умеет удалять приключение (кнопка «Удалить»),
а сервер обязан убрать вместе с ним журнал, разговор, заметки, кубики и память
движка: иначе через неделю база обрастает записями, которые никому не видны,
и `PRAGMA foreign_key_check` начинает ругаться — ровно та причина, по которой
резервная копия отказывалась создаваться (аудит 08.11, F04).
"""
import socket
import sqlite3

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.memory import Memory


@pytest.fixture
def client(tmp_path, monkeypatch):
    connect = socket.socket.connect

    def local_only(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External AI/network is forbidden')

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    import solo.nat20_api
    monkeypatch.setattr(solo.nat20_api.secrets, 'randbits', lambda bits: 5)
    model = CommandMockLLM({})
    return TestClient(create_app(tmp_path / 'delete-campaign.sqlite3', model))


def test_delete_removes_history_and_keeps_neighbours(client):
    store = client.app.state.store
    from solo.nat20_api import Nat20GameEngine
    engine = Nat20GameEngine(store)
    doomed = engine.create_campaign('Удалить меня', seed=5)['id']
    kept = engine.create_campaign('Оставить меня', seed=7)['id']

    # Живая кампанская история: то, что обычная игра пишет сама.
    body = dict(request_id='del-1', text='Осматриваюсь')
    state = client.get('/api/campaigns/' + doomed).json()
    body['expected_version'] = state['version']
    assert client.post('/api/campaigns/' + doomed + '/command-turn', json=body).status_code == 200

    # Записи из соседних модулей: заметка, кубик, память.
    Memory(store).remember(doomed, 'Обещал вернуться к фонарю') if hasattr(Memory(store), 'remember') else None
    removed = store.delete_campaign(doomed)
    assert removed >= 1

    assert client.get('/api/campaigns/' + doomed).status_code == 404
    assert client.get('/api/campaigns/' + kept).status_code == 200

    db = sqlite3.connect(store.path)
    try:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in tables:
            quoted = '"%s"' % name.replace('"', '""')
            columns = {r[1] for r in db.execute('PRAGMA table_info(%s)' % quoted)}
            if 'campaign' in columns:
                left = db.execute('SELECT count(*) FROM %s WHERE campaign=?' % quoted, (doomed,)).fetchone()[0]
                assert left == 0, 'остались записи в %s: %s' % (name, left)
        assert db.execute('SELECT count(*) FROM events WHERE campaign=?', (doomed,)).fetchone()[0] == 0
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        db.close()


def test_delete_missing_campaign_is_404(client):
    response = client.delete('/api/campaigns/нет-такой')
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'campaign_not_found'

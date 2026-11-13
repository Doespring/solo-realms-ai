"""A02: экран старой кампании в настоящем браузере — причина ДО ввода и три пути.

Сервер и механика настоящие (Nat20), ответов сервера тест не подменяет: всё,
что видно на экране, посчитано кодом. Старая запись сделана так же, как в живой
базе: у одной кампании определение разошлось с проверенной регистрацией (S64),
у второй в ``world.events`` лежат события прежнего обработчика (S61/S63).
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from solo.app import create_app
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine


def test_old_campaign_screen_offers_three_ways(tmp_path, monkeypatch):
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node')
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT')
    if not node or not playwright or not Path(playwright).exists():
        pytest.skip('Локальный Node/Playwright нужен для браузерной проверки')
    connect = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'compat-ui.sqlite3', CommandMockLLM({}), local_port=port)
    store = app.state.store
    engine = Nat20GameEngine(store)

    # S64: определение кампании разошлось с проверенной регистрацией.
    broken = engine.create_campaign('A02 · старая запись', seed=7)['id']
    current = store.campaign(broken)
    engine.execute(broken, GameCommand(request_id='before-a02', expected_version=current['version'],
                                       action=dict(kind='check', check_id='athletics')))
    with store.connection(True) as db:
        state = json.loads(json.dumps(store.campaign(broken, db)['state']))
        state['nat20']['definition']['title'] = 'Дозор на тракте · старая запись'
        db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state), broken))

    # S61/S63: события прежнего обработчика списком.
    legacy = engine.create_campaign('A02 · старые события', seed=3)['id']
    with store.connection(True) as db:
        state = json.loads(json.dumps(store.campaign(legacy, db)['state']))
        state['world'] = dict(settings=dict(mode='sandbox'), triggered=[],
                              events=[dict(id='foe-printed-kobold', after_minutes=0, kind='encounter',
                                           location='Тракт', text='Противник из источника: kobold',
                                           npc='', target='', relationship=0,
                                           encounter=dict(environment='room', seed=1))])
        db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state), legacy))
    history_before = len(store.history(broken, limit=1000))

    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        # Экран грузит несколько модулей и первый запрос поднимает движок:
        # ждём, что приложение уже отвечает, а не только «started».
        import httpx
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(f'http://127.0.0.1:{port}/api/health', timeout=5).status_code == 200:
                    break
            except Exception:
                time.sleep(.1)
        else:
            raise AssertionError('Приложение не ответило на /api/health за 60 с')
        evidence = os.environ.get('SOLO_UI_EVIDENCE') or str(tmp_path / 'ui')
        result = subprocess.run(
            [str(node), str(Path(__file__).with_suffix('.cjs')), f'http://127.0.0.1:{port}',
             broken, legacy, str(tmp_path)],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright), 'SOLO_UI_EVIDENCE': evidence},
            capture_output=True, text=True, encoding='utf-8', timeout=300,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(10)

    # Экран обновил кампанию: история не уменьшилась, запись об обновлении одна.
    history = store.history(broken, limit=1000)
    assert len(history) >= history_before
    assert 'Кампания обновлена' in history[-1]['original']

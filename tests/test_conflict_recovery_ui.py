"""n01: отказ по версии на ходе не превращается в шторм запросов.

Изолированный браузерный прогон: приложение своё (временная база, mock-модель,
свободный порт), ответы сцены и хода подменены фикстурами внутри теста. Ни
живого профиля, ни движка, ни платных вызовов.
"""
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
from solo.llm import MockLLM
from solo.nat20_api import Nat20GameEngine


def test_conflict_does_not_storm_the_server(tmp_path, monkeypatch):
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node')
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT')
    browser = os.environ.get('SOLO_UI_BROWSER')
    if not node or not playwright or not browser or not Path(playwright).exists() or not Path(browser).is_file():
        pytest.skip('Нужен локальный Node + Playwright + Chromium (SOLO_UI_* переменные)')

    connect = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'conflict.sqlite3', MockLLM(), local_port=port)
    # Кампания именно nat20: у записи есть `state` (герой, места, ход), без него
    # экран игры не рендерится и проверка «нажатие → отказ → строка» не доходит
    # до клиента вовсе.
    campaign = Nat20GameEngine(app.state.store).create_campaign('Conflict · UI fixture', seed=7)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [str(node), str(Path(__file__).with_suffix('.cjs')),
             f'http://127.0.0.1:{port}', campaign['id'], str(tmp_path)],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright)},
            capture_output=True, text=True, encoding='utf-8', timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()

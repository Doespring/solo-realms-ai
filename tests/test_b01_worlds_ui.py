"""B01/F06+F05 на живом экране: имя файла — подсказка, сравнение правил — чтение.

Приёмка Q13:
1. файл с браузерным суффиксом «(1)» импортируется (1 из 1), ошибки «не пакет
   мира» нет;
2. чужое содержимое по-прежнему отвергается — со своей причиной;
3. «Сравнить с правилами кампании» отдаёт список различий, а не сообщение об
   отключённом обработчике.

Стенд свой: временная база, свободный порт, mock-модель без сети. Живой профиль
и порт приложения не трогаются.
"""
import os

os.environ.setdefault('SOLO_TEST', '1')

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.world_service import export_package
from test_r236_worlds import world_campaign


def test_world_file_is_judged_by_content_and_rules_are_compared(tmp_path, monkeypatch):
    node = os.environ.get('SOLO_UI_NODE') or ''
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or ''
    if not Path(node).is_file() or not Path(playwright).exists():
        pytest.skip('Local browser runtime required')
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    app = create_app(tmp_path / 'b01.sqlite3', CommandMockLLM({}), local_port=port,
                     offline_mock=True)
    store = app.state.store
    world = world_campaign(store, 'Аудит Тиамат')
    package = export_package(store, world)
    # Имя с браузерным суффиксом: то же содержимое, что у принятого файла.
    named = tmp_path / 'test-tiamat-08-11.soloworld (1).json'
    named.write_text(json.dumps(package, ensure_ascii=False), encoding='utf-8')
    foreign = tmp_path / 'чужой-файл.json'
    foreign.write_text(json.dumps({'format': 'foundry-world', 'name': 'Чужой мир'},
                                  ensure_ascii=False), encoding='utf-8')
    rules = Nat20GameEngine(store).create_campaign('Свои правила', seed=5)['id']

    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [node, str(Path(__file__).with_suffix('.cjs')), f'http://127.0.0.1:{port}', str(tmp_path)],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': playwright,
                 'SOLO_UI_BROWSER': os.environ.get('SOLO_UI_BROWSER', ''),
                 'SOLO_WORLD_PACKAGE': str(named), 'SOLO_WORLD_FOREIGN': str(foreign),
                 'SOLO_WORLD_NAME': package['name'], 'SOLO_RULES_CAMPAIGN': rules},
            capture_output=True, text=True, encoding='utf-8', timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True; thread.join(10); sock.close(); assert not thread.is_alive()

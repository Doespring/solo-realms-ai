"""Живой браузер: уровень героя выбирается в редакторе и собирается целиком.

Сценарий владельца: книга просит 8-й уровень — в редакторе виден селект со
значением 8 и подписью, из какой книги уровень; герой собирается на 8-м
(подкласс, повышения на 4 и 8, заклинания всех уровней), а после создания и в
карточке кампании, и на листе стоит «уровень 8», а не 1.
"""
import os
os.environ['SOLO_TEST'] = '1'

from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.preview_mock import PreviewMock


def _browser_runtime():
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node')
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT')
    if not playwright:
        candidates = sorted(Path.home().glob('.npm/_npx/*/node_modules/playwright'))
        playwright = str(candidates[0]) if candidates else ''
    browsers = sorted(Path.home().glob('.hermes/tools/chromium-*/chrome-linux64/chrome'))
    browser = os.environ.get('SOLO_UI_BROWSER') or (str(browsers[-1]) if browsers else '')
    return node, playwright, browser


def test_the_editor_offers_the_books_level_and_builds_there(tmp_path, monkeypatch):
    node, playwright, browser = _browser_runtime()
    if not (node and Path(playwright).exists() and Path(browser).is_file()):
        pytest.skip('Local browser runtime required')
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'level-ui.sqlite3', PreviewMock(), local_port=port,
                     experimental=True, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [str(node), str(Path(__file__).with_suffix('.cjs')),
             f'http://127.0.0.1:{port}', str(tmp_path)],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': playwright,
                 'SOLO_UI_BROWSER': browser},
            capture_output=True, text=True, encoding='utf-8', timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
        assert app.state.store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])
    finally:
        server.should_exit = True
        thread.join(30)
        sock.close()
        assert not thread.is_alive()

"""N1: первый кадр создания и экран подготовки книги — проверка живым браузером."""
import os
os.environ['SOLO_TEST'] = '1'

from pathlib import Path
import glob
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.preview_mock import PreviewMock


def _ui_runtime():
    """node + playwright + браузер: переменные окружения, затем Windows-рантайм
    прошлых заходов, затем локальный Linux (npx-кэш playwright и ms-playwright).

    Без поиска по Linux тест молча уходил в `skip`, и «тест проверяет призыв в
    кадре» было неправдой: проверить было нечем. `skip` остаётся только когда
    браузера нет вовсе.
    """
    node = os.environ.get('SOLO_UI_NODE') or ''
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or ''
    browser = os.environ.get('SOLO_UI_BROWSER') or ''
    windows = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    if not node and (windows / 'bin/node.exe').is_file():
        node = str(windows / 'bin/node.exe')
    if not playwright and (windows / 'node_modules/playwright').exists():
        playwright = str(windows / 'node_modules/playwright')
    if not node:
        node = shutil.which('node') or ''
    if not playwright:
        found = sorted(glob.glob(str(Path.home() / '.npm/_npx/*/node_modules/playwright')))
        playwright = found[-1] if found else ''
    if not browser:
        found = sorted(glob.glob(str(Path.home() / '.cache/ms-playwright/chromium-*/chrome-linux64/chrome')))
        browser = found[-1] if found else ''
    return node, playwright, browser


def test_n1_creation_ui(tmp_path, monkeypatch):
    node, playwright, browser = _ui_runtime()
    if not (node and Path(node).is_file() and playwright and Path(playwright).exists()):
        pytest.skip('Local browser runtime required')
    if browser and not os.environ.get('SOLO_UI_BROWSER'):
        monkeypatch.setenv('SOLO_UI_BROWSER', browser)
    deps = Path.home() / '.hermes/chromium-deps/usr/lib/x86_64-linux-gnu'
    if deps.is_dir() and not os.environ.get('LD_LIBRARY_PATH'):
        monkeypatch.setenv('LD_LIBRARY_PATH', str(deps))
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    app = create_app(tmp_path / 'n1-creation.sqlite3', PreviewMock(), local_port=port,
                     experimental=True, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [node, str(Path(__file__).with_suffix('.cjs')), f'http://127.0.0.1:{port}', str(tmp_path)],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright)},
            capture_output=True, text=True, encoding='utf-8', timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True; thread.join(10); sock.close(); assert not thread.is_alive()

"""Isolated source-browser checks; responses are explicit UI fixtures, no AI calls."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest

from browser_available import require_browser
import uvicorn

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM


def test_small_window_ui(tmp_path, monkeypatch):
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime / 'bin/node.exe'
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime / 'node_modules/playwright'
    require_browser()
    connect = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'small-window.sqlite3', MockLLM(), local_port=port)
    campaign = app.state.store.create('Small window · UI fixture', Hero(name='Мирон'))
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
            capture_output=True, text=True, encoding='utf-8', timeout=100,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()

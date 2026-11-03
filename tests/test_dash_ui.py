"""Real pinned Nat20 + PreviewMock; only test-owned data and a local source host."""
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
from solo.lantern_scene import install
from solo.nat20_api import Nat20GameEngine
from solo.preview_mock import PreviewMock


def test_real_nat20_dash_ui(tmp_path, monkeypatch):
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
    model = PreviewMock()
    app = create_app(tmp_path / 'dash-ui.sqlite3', model, local_port=port)
    store = app.state.store
    cid = Nat20GameEngine(store).create_campaign(
        'Dash UI · supported fighter', definition_id='fighter-standard-array-2024-v1', seed=5)['id']
    store.record_scenario(cid, 'lantern-install', 'Письмо у старого фонаря', install)
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
             f'http://127.0.0.1:{port}', cid, str(tmp_path)],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright)},
            capture_output=True, text=True, encoding='utf-8', timeout=240,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()

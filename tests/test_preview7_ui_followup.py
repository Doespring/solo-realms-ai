"""Source-browser regressions only; never launches or changes the frozen Preview."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM


def test_preview7_ui_followup(tmp_path, monkeypatch):
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime / 'bin/node.exe'
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime / 'node_modules/playwright'
    if not Path(node).is_file() or not Path(playwright).exists():
        pytest.skip('Set SOLO_UI_NODE and SOLO_UI_PLAYWRIGHT to the local browser-test runtime')
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if address[0] not in ('127.0.0.1', '::1'):
            raise AssertionError('UI tests must not connect to external services')
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'ui.sqlite3', MockLLM(), local_port=port)
    campaign = app.state.store.create('UI follow-up · synthetic', Hero(name='UI герой'))
    app.state.store.save(campaign['id'], 'UI контрольная точка')
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started, 'Isolated UI host did not start'
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
        assert not thread.is_alive(), 'Isolated UI host did not stop'

"""The browser keeps the pages it already loaded (web/play.js mergeOlder).

The server test proves every stored turn is reachable. This one proves the
screen does not throw those pages away when a new turn arrives, and that the
reading cursor is remembered: mergeOlder runs in a real Edge page.
"""
import os
os.environ['SOLO_TEST']='1'

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from browser_available import require_browser
import uvicorn
from solo.app import create_app
from solo.llm import MockLLM


def test_loaded_pages_survive_a_new_turn(tmp_path, monkeypatch):
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime / 'bin/node.exe'
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime / 'node_modules/playwright'
    require_browser()
    connect = socket.socket.connect
    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    app = create_app(tmp_path / 'merge.sqlite3', MockLLM(), local_port=port)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        script = Path(__file__).with_suffix('.cjs')
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run([str(node), str(script), 'http://127.0.0.1:%d' % port],
            cwd=str(root),
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright)},
            capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PAGING_MERGE_OK' in result.stdout
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()
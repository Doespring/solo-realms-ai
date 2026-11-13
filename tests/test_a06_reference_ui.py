"""Экран мастера: приложение — справочник, цитата и правка типа на месте."""
import os

os.environ.setdefault('SOLO_TEST', '1')

from pathlib import Path
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.preview_mock import PreviewMock


def test_the_review_screen_indexes_appendices_and_keeps_a_persons_correction(tmp_path, monkeypatch):
    node = os.environ.get('SOLO_UI_NODE') or ''
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or ''
    if not Path(node).is_file() or not Path(playwright).exists():
        pytest.skip('Local browser runtime required')
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_book_readiness import library_with_book
    library, bundle = library_with_book(tmp_path, episodes=2, appendices=1)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    app = create_app(tmp_path / 'solo.sqlite3', PreviewMock(), local_port=port,
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
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': playwright}, capture_output=True, text=True,
            encoding='utf-8', timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True; thread.join(10); sock.close(); assert not thread.is_alive()

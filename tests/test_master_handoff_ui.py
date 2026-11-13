"""Real browser seam for A03/F01: the master's last step hands over its draft.

The audit's path is a screen path, not an API one, so it is checked in a real
browser at 390 px: eight filled steps end on «Выбрать героя Nat20», the launch
opens *that* draft (one draft, not two), a step left and returned to keeps what
was entered, and the hero editor starts with the master's name and items
instead of an empty «Новое приключение».
"""
import os
os.environ['SOLO_TEST'] = '1'

from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.preview_mock import PreviewMock

sys.path.insert(0, str(Path(__file__).parent))
from test_master_handoff_f01 import ITEMS, PAGES, book, master_draft  # noqa: E402


def runtime():
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node')
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT')
    if not playwright:
        candidates = sorted(Path.home().glob('.npm/_npx/*/node_modules/playwright'))
        playwright = str(candidates[-1]) if candidates else ''
    browser = os.environ.get('SOLO_UI_BROWSER')
    if not browser:
        found = sorted(Path.home().glob('.hermes/tools/chromium-*/chrome-linux64/chrome'))
        browser = str(found[-1]) if found else ''
    return node, playwright, browser


def test_the_last_step_opens_the_same_draft_in_the_launch(tmp_path, monkeypatch):
    node, playwright, browser = runtime()
    if not node or not playwright or not Path(playwright).exists() or not browser or not Path(browser).is_file():
        pytest.skip('Local browser runtime required')
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'handoff-ui.sqlite3', PreviewMock(), local_port=port,
                     experimental=True, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        # The fixture is the eight-step master's own record, saved server-side the
        # way the master saves it: one draft, its parts filled, its step on 7.
        draft_id, bundle, module = prepare(app, 'Тиамат · хроника')
        # Item names as the master's own screen prints them: the second pass checks
        # the boxes by text, the way a player does.
        from solo.inventory import CATALOG
        names = '|'.join(CATALOG[key]['name'] for key in ITEMS)
        result = subprocess.run(
            [str(node), str(Path(__file__).with_suffix('.cjs')), f'http://127.0.0.1:{port}',
             str(tmp_path), draft_id, bundle, module, '|'.join(ITEMS), names, PAGES[1]],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright), 'SOLO_UI_BROWSER': str(browser)},
            capture_output=True, text=True, encoding='utf-8', timeout=420)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(30)
        sock.close()
        assert not thread.is_alive()


def prepare(app, name='Тиамат · хроника'):
    """Save the master's finished draft without going through the browser."""
    from solo.preparation import Draft
    library = app.state.adventures
    preparation = app.state.preparation
    bundle = book(library)
    module = next(row for row in library.modules(bundle) if row['ready'])['id']
    draft = preparation.create()
    body = Draft.model_validate(draft['body'])
    body.name = name
    body.description = 'Книга о драконах'
    body.adventure_bundle = bundle
    body.adventure_module = module
    body.source_kind = 'ready'
    body.prepared = True
    body.background_confirmed = True
    body.start_name = 'Гринест'
    body.opening = PAGES[1]
    body.equipment = list(ITEMS)
    body.step = 7
    body.hero.update(name='Лира', species='Человек', class_name='Воин', level=1,
                     hp=12, max_hp=12, ac=16, speed_ft=30,
                     background='Выросла в Гринесте и знает каждую улицу.')
    preparation.save(draft['id'], body, draft['revision'])
    return draft['id'], bundle, module

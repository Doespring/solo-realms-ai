"""The playable loop in a real browser, on the real book, in one process."""
import copy
import os
os.environ['SOLO_TEST'] = '1'
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from solo.adventure_engine import initialize
from solo.adventure_library import AdventureLibrary
from solo.first_episode import OPENING, encounters as first_encounters
from solo.first_episode import definition as first_definition
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store
from solo.world import WorldSettings, import_package

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402

PROFILE = Path.home() / 'AppData' / 'Local' / 'SoloRealmsAI-Prototype-1'


def browser_runtime():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime / 'bin/node.exe'
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime / 'node_modules/playwright'
    return Path(node), Path(playwright)


def prepared_parts(tmp_path):
    """The chapter and the legal hero, from the owner copy of the book."""
    source = PROFILE / "adventure.sqlite3"
    if not source.is_file():
        pytest.skip('Владелец не импортировал книгу в этом профиле')
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    shutil.copy2(source, library_dir / "adventure.sqlite3")
    files = library_dir / "adventure-files"
    files.mkdir()
    for item in (PROFILE / "adventure-files").iterdir():
        if item.is_file():
            shutil.copy2(item, files / item.name)
    library = AdventureLibrary(library_dir)
    definition = first_definition(library, "dnd2024", 1)
    # Level one with no subclass: page 15 grants level two, which is the level
    # this chapter is written for.
    build = legal(class_slug="fighter", species_slug="human", level=1,
                  equipment=["longsword", "chain-mail", "shield"])
    return definition, build


def install(store, cid, definition):
    def transition(state):
        world = dict(location=OPENING, turn=0, locations=[], facts=[], deferred=[],
                     geography=dict(nodes=[], routes=[]),
                     world=dict(settings=WorldSettings(mode='story').model_dump()))
        import_package(world, definition.world)
        for key in ("location", "locations", "geography", "world"):
            state[key] = copy.deepcopy(world[key])
        initialize(state, "hoard-episode-1", definition)
        return [dict(type='scripted', kind='prepared_world', text='старт')]
    store.record_scenario(cid, "hoard-episode-1", "старт", transition)
    return store, cid, definition


def test_the_loop_shows_the_book_progression_in_the_browser(tmp_path, monkeypatch):
    node, playwright = browser_runtime()
    if not node.is_file() or not playwright.exists():
        pytest.skip('Local browser runtime required')
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    definition, build = prepared_parts(tmp_path)
    from solo.app import create_app
    from solo.preview_mock import PreviewMock
    from fastapi.testclient import TestClient
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'loop.sqlite3', PreviewMock(), local_port=port,
                      experimental=True, offline_mock=True)
    # Build the campaign inside the application store, so the browser reads the
    # very campaign the scenario installed.
    store = app.state.store
    api = Nat20GameEngine(store)
    campaign = api.create_experimental_campaign('Лира', build=build,
        encounters=first_encounters(), checks=None, prepared_spells=(), seed=3,
        title=OPENING, description='d', progression=definition.progression)
    cid = campaign['id']
    install(store, cid, definition)
    # The chapter is finished, which is what earns the milestone level.
    import json as _json
    with store.connection(True) as db:
        row = db.execute("SELECT state FROM campaigns WHERE id=?", (cid,)).fetchone()
        state = _json.loads(row[0])
        state.setdefault("adventure", {})["completed_modules"] = ["hoard-episode-1"]
        db.execute("UPDATE campaigns SET state=? WHERE id=?",
                   (_json.dumps(state, ensure_ascii=False), cid))
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [str(node), str(Path(__file__).with_suffix(".cjs")),
             f'http://127.0.0.1:{port}', str(tmp_path), cid],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright)},
            capture_output=True, text=True, encoding='utf-8', timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()

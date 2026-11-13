"""Ordinary browser UI over the control campaign, with no faked endpoints."""
import json
import os

os.environ['SOLO_TEST'] = '1'

import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from solo.app import create_app
from solo.llm import MockLLM

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.uitmp' / 'control'


def test_the_control_campaign_is_played_through_the_ordinary_screen(tmp_path):
    runtime = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('SOLO_UI_NODE') or runtime/'bin/node.exe'
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime/'node_modules/playwright'
    if not Path(node).is_file() or not Path(playwright).exists():
        pytest.skip('Local browser runtime required')
    database = WORK/'solo.sqlite3'
    # This check consumes a manual concierge run, so its files may simply be absent.
    marker = WORK/'campaign-id.txt'
    if not database.is_file() or not marker.is_file():
        pytest.skip('The control campaign has not been launched yet')
    campaign = marker.read_text(encoding='utf-8').strip()
    if not campaign:
        pytest.skip('The control campaign has not been launched yet')
    out = Path(os.environ.get('SOLO_UI_EVIDENCE', tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    original = socket.socket.connect
    def local(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External connections forbidden'
        return original(sock, address)
    socket.socket.connect = local
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    app = create_app(database, MockLLM(), local_port=port, experimental=True)
    # The player starts at the book's opening scene: the walk is demonstrated from
    # there, not from wherever an earlier probe left the party.
    from solo.world import ScenarioPackage
    from solo.world_service import WorldCommand, WorldService
    store = app.state.store
    state = store.campaign(campaign)['state']
    if state['location'] != 'Bell Landing':
        opening = next(p['name'] for p in state['locations']
                       if p['name'] in ('Bell Landing',))
        # A fresh request id: the same one would replay the earlier move instead of
        # walking, leaving the party wherever the previous run stopped.
        WorldService(store).execute(campaign, WorldCommand(
            request_id='ui-reset-'+str(int(time.time()*1000)), kind='travel',
            target=opening), from_map=True)

    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False,
                                           timeout_graceful_shutdown=5))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=5) as answer:
            assert answer.status == 200
        env = {**os.environ, 'SOLO_TEST': '1', 'SOLO_UI_PLAYWRIGHT': str(playwright)}
        run = subprocess.run([str(node), str(Path(__file__).with_suffix('.cjs')),
                              f'http://127.0.0.1:{port}', campaign, str(out)],
                             env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
        assert run.returncode == 0, run.stdout+run.stderr
        report = json.loads((out/'ui-control.json').read_text(encoding='utf-8'))
    finally:
        server.should_exit = True
        thread.join(40)
    assert report['world_visible'] and report['has_both'], report
    assert report['routes'] > 0, report
    assert not report['errors'], report
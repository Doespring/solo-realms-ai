"""Local end-to-end acceptance: material -> preparation -> hero -> chat -> actions
-> save -> restart -> same campaign. Controlled provider, isolated profile.

This is a LOCAL run with a scripted provider. It is not a live-model check.
"""
import os
os.environ['SOLO_TEST']='1'

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from browser_available import require_browser
import uvicorn
from fastapi.testclient import TestClient

from solo.app import create_app
from test_preparation_pipeline import PreparationFixture
from test_material_analyzer_entry import idea, prepared, result_of
from test_pdf_world_http import build, new_game
from solo.llm import CommandMockLLM


def _safe_socket(monkeypatch):
    connect = socket.socket.connect
    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_only)


def _turn_model():
    # The interpreter reads the player's words verbatim, so the scripted answer is
    # keyed by the utterance and carries a status: a bare action is not an intent.
    return CommandMockLLM({'Осматриваюсь у входа':
                               dict(status='command', action=dict(kind='check', check_id='perception'), message=''),
                           'Иду вдоль причала':
                               dict(status='command', action=dict(kind='check', check_id='perception'), message='')})


def _walk(tmp_path, db, llm):
    app = create_app(db, llm, experimental=True, offline_mock=True)
    # Dialogue turns use the interpreter role; the preparation role is scripted
    # separately, so the same isolated profile carries both a scripted provider.
    app.state.game.llm = _turn_model()
    with TestClient(app) as client:
        url, prep, status = prepared(client)
        did = url.rsplit('/', 1)[-1]
        ready = client.get(url + '/world-readiness').json()
        assert ready['world_ready'], ready
        confirmed = client.post(url + '/confirm-world', json=dict(
            expected_revision=ready['revision'], run_id=ready['run_id'], confirm=True))
        assert confirmed.status_code == 200, confirmed.text
        packet = new_game(did, confirmed.json()['revision'])
        started = client.post('/api/prototype/campaigns', json=packet)
        assert started.status_code == 200, started.text
        cid = started.json()['id']
        rest = '/api/campaigns/' + cid
        # Two real turns that the engine resolves, and one the interpreter cannot
        # place (it asks a question). None of them may be silently a failure.
        for key, text_turn, expected in (
                ('first', 'Осматриваюсь у входа', 'resolved'),
                ('second', 'Иду вдоль причала', 'resolved'),
                ('third', 'Здороваюсь с прохожим', 'clarification')):
            response = client.post(rest + '/command-turn', json=dict(
                request_id=key, text=text_turn,
                expected_version=app.state.store.campaign(cid)['version']))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body['status'] == expected, (text_turn, body)
            if expected == 'resolved':
                assert body['events'], 'a resolved turn must write an event'
        chat = client.get(rest).json()
        store = app.state.store
        rows = store.conversation_history(cid, limit=200)
        assert len(rows) >= 1, (rows, chat.get('conversation_total'))
        assert len(app.state.store.history(cid)) >= 2, 'the journal kept no resolved turn'
        saved = client.post(rest + '/saves', json=dict(name='Контроль'))
        assert saved.status_code == 200, saved.text
        turns = app.state.store.campaign(cid)['state']['turn']
        assert turns >= 3, ('the turns did not advance', turns)
    return cid, turns


def test_local_journey_survives_a_real_restart(tmp_path, monkeypatch):
    _safe_socket(monkeypatch)
    db = tmp_path / 'profile' / 'solo.sqlite3'
    db.parent.mkdir(parents=True, exist_ok=True)
    cid, turns = _walk(tmp_path, db, PreparationFixture(result=idea()))
    # The same profile opened again: the campaign and its journal continue.
    again = create_app(db, PreparationFixture(result=idea()), experimental=True, offline_mock=True)
    again.state.game.llm = _turn_model()
    with TestClient(again) as client:
        listed = [c['id'] for c in client.get('/api/campaigns').json()]
        assert cid in listed
        resumed = client.get('/api/campaigns/' + cid).json()
        assert resumed['state']['turn'] == turns
        assert resumed['conversation_total'] >= 1


def _node():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    return (os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime / 'bin/node.exe',
            os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime / 'node_modules/playwright')


def test_the_player_opens_the_chat_in_a_real_browser(tmp_path, monkeypatch):
    node, playwright = _node()
    require_browser()
    _safe_socket(monkeypatch)
    db = tmp_path / 'browser' / 'solo.sqlite3'
    db.parent.mkdir(parents=True, exist_ok=True)
    cid, _ = _walk(tmp_path, db, PreparationFixture(result=idea()))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(db, PreparationFixture(result=idea()), experimental=True,
                     offline_mock=True, local_port=port)
    app.state.game.llm = _turn_model()
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        script = Path(__file__).with_suffix('.cjs')
        result = subprocess.run([str(node), str(script), 'http://127.0.0.1:%d' % port, cid],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright)},
            capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'JOURNEY_UI_OK' in result.stdout
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()
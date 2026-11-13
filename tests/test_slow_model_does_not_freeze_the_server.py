"""A slow model must not freeze the server for everyone else.

Section 9.2 of the task: the global Game.operation was held across the model
call, and the Nat20 worker was a blocking subprocess.run of 1.5-3.5 seconds
(start_combat measured at 3.55 s). Both froze the event loop, so a plain state
read waited 1.218 s for a turn on an unrelated request to finish thinking.

The lock now guards the read/check/commit around state, not the thinking. These
tests pin the two properties that must both hold: the server stays responsive,
and a turn still executes exactly once with a correct version.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import json
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import CommandMockLLM, MockLLM
from solo.nat20_api import Nat20GameEngine


TEXT = 'Осматриваю калитку'


class SlowModel(CommandMockLLM):
    """Медленный интерпретатор одного действия Nat20; сети и денег нет."""
    delay = 1.2

    async def interpret_command(self, *a, **k):
        await asyncio.sleep(self.delay)
        return await super().interpret_command(*a, **k)


def _serve(tmp_path):
    model = SlowModel({TEXT: dict(status='command', action=dict(kind='check', check_id='perception'),
                                 message='')})
    app = create_app(tmp_path / 'solo.sqlite3', model)
    cid = Nat20GameEngine(app.state.store).create_campaign('К', seed=5)['id']
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    threading.Thread(target=server.run, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    for _ in range(80):
        try:
            urllib.request.urlopen(base + '/api/campaigns/' + cid, timeout=1)
            break
        except OSError:
            time.sleep(0.05)
    return server, base, cid


def _version(base, cid):
    return json.loads(urllib.request.urlopen(base + '/api/campaigns/' + cid, timeout=60).read())['version']


def _turn(base, cid, request_id, text=TEXT, expected_version=0):
    request = urllib.request.Request(base + '/api/campaigns/' + cid + '/command-turn',
                                     data=json.dumps(dict(request_id=request_id, text=text,
                                                          expected_version=expected_version)).encode(),
                                     headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(request, timeout=60).read())


def test_a_read_does_not_wait_for_a_turn(tmp_path):
    server, base, cid = _serve(tmp_path)
    try:
        version = _version(base, cid)
        worker = threading.Thread(target=lambda: _turn(base, cid, 'r1', expected_version=version), daemon=True)
        worker.start()
        time.sleep(0.25)  # the turn is now waiting on the model
        started = time.perf_counter()
        urllib.request.urlopen(base + '/api/campaigns/' + cid, timeout=60).read()
        waited = time.perf_counter() - started
        worker.join(timeout=30)
        assert waited < 0.5, 'a read waited %.2f s for an unrelated turn' % waited
    finally:
        server.should_exit = True


def test_the_turn_still_executes_once_with_a_correct_version(tmp_path):
    server, base, cid = _serve(tmp_path)
    try:
        first = _turn(base, cid, 'r1')
        assert first.get('status') == 'resolved' and first.get('journal_id'), first
        version = _version(base, cid)
        replay = _turn(base, cid, 'r1', expected_version=version)
        after = _version(base, cid)
        assert replay.get('journal_id') == first['journal_id'], 'a replay names the same event'
        assert after == version, 'a replay must not move the version'
    finally:
        server.should_exit = True


def test_the_helper_refuses_a_task_that_does_not_hold_the_lock(tmp_path):
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    game = app.state.game

    async def misuse():
        async with game.operation_without('any'):
            pass
    with pytest.raises(RuntimeError):
        asyncio.run(misuse())


def test_the_worker_wrapper_runs_off_the_loop():
    from solo.nat20_api import Nat20GameEngine
    assert asyncio.iscoroutinefunction(Nat20GameEngine._worker_async)

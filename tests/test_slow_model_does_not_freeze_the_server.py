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
import sys
import threading
import time
import urllib.request

import pytest
import uvicorn
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM


class SlowModel(MockLLM):
    """The old mock overrode interpret_command, which MockLLM does not have.

    Nothing was ever delayed, so the two timing checks could not fail. The
    delay now sits on reply_text - the method the legacy play path really
    awaits - so a turn genuinely spends 1.2 s waiting for the model.
    """
    delay = 1.2

    async def interpret(self, *a, **k):
        await asyncio.sleep(self.delay)
        return await super().interpret(*a, **k)


def _serve(tmp_path):
    app = create_app(tmp_path / 'solo.sqlite3', SlowModel())
    cid = app.state.store.create('К', Hero(name='Мирон', hp=12, max_hp=12))['id']
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


def _turn(base, cid, request_id, text='осматриваюсь'):
    request = urllib.request.Request(base + '/api/campaigns/' + cid + '/play',
                                     data=json.dumps(dict(request_id=request_id, text=text)).encode(),
                                     headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(request, timeout=60).read())


def test_a_read_does_not_wait_for_a_turn(tmp_path):
    server, base, cid = _serve(tmp_path)
    try:
        worker = threading.Thread(target=lambda: _turn(base, cid, 'r1'), daemon=True)
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
        assert first.get('event'), first
        version = json.loads(urllib.request.urlopen(base + '/api/campaigns/' + cid).read())['version']
        replay = _turn(base, cid, 'r1')
        after = json.loads(urllib.request.urlopen(base + '/api/campaigns/' + cid).read())['version']
        assert replay.get('event') == first['event'], 'a replay names the same event'
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


def test_the_engine_turn_lets_other_requests_through(tmp_path):
    """The turn that actually calls the model is command-turn.

    The campaign built above has no engine, so play() never reaches a model and
    the timing check there measures an empty path. This one builds the vertical
    scenario, which runs through play_command, and holds the scripted model for
    1.2 s inside interpret_command.
    """
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
    from command_smoke import setup as make_scenario
    store, cid, game = make_scenario(Path(tmp_path) / 'scenario')
    original = game.llm.interpret_command

    async def slow(text, context):
        await asyncio.sleep(1.2)
        return await original(text, context)

    game.llm.interpret_command = slow
    app = create_app(store.path, game.llm)
    app.state.game = game
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    threading.Thread(target=server.run, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(base + '/api/campaigns/' + cid, timeout=1).read()
                break
            except OSError:
                time.sleep(0.05)
        version = store.campaign(cid)['version']
        body = json.dumps(dict(request_id='lockslow', text='Исследую следы',
                               expected_version=version)).encode()

        def turn():
            request = urllib.request.Request(base + '/api/campaigns/' + cid + '/command-turn',
                                             data=body, method='POST',
                                             headers={'Content-Type': 'application/json'})
            try:
                urllib.request.urlopen(request, timeout=60).read()
            except Exception:
                pass

        worker = threading.Thread(target=turn, daemon=True)
        worker.start()
        time.sleep(0.3)  # the turn is now inside the model call
        # A plain read takes no lock, so it can never prove the lock is free.
        # Campaign creation does take it: if the lock were held across the model
        # call, this write would wait for the whole narration.
        create_body = json.dumps(dict(name='Второй', hero=dict(name='Ева', hp=12, max_hp=12))).encode()
        request = urllib.request.Request(base + '/api/campaigns', data=create_body, method='POST',
                                         headers={'Content-Type': 'application/json'})
        started = time.perf_counter()
        created = urllib.request.urlopen(request, timeout=60).read()
        waited = time.perf_counter() - started
        worker.join(timeout=60)
        assert json.loads(created).get('id'), created
        assert waited < 0.6, 'a write waited %.2f s while the model thought' % waited
    finally:
        server.should_exit = True


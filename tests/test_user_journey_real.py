"""One reproducible browser acceptance of the player path, in an isolated profile.

Every step is a click, a field or a file choice in the page: no prepared hero,
campaign id or state is injected. The server process is STOPPED and a NEW one
starts on the same profile; the player continues the same campaign.
The provider is scripted: a LOCAL acceptance, not a live-model check.
"""
import json
import os
import uuid
os.environ['SOLO_TEST']='1'

import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from browser_available import require_browser

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'runtime' / 'ui-tmp' / 'journey_server.py'
# Two runs of this acceptance at once (a reviewer and the author) must not share
# screenshots or a material file, so every run writes into its own folder.
EVIDENCE = ROOT / 'runtime' / 'ui-tmp' / ('journey-evidence-' + uuid.uuid4().hex[:8])

MATERIAL = (Path(__file__).parent / 'fixtures' / 'journey-chronicle.md').read_text(encoding='utf-8')


def _node():
    runtime = Path.home() / '.cache' / 'codex-runtimes' / 'codex-primary-runtime' / 'dependencies' / 'node'
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node') or str(runtime / 'bin' / 'node.exe')
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or str(runtime / 'node_modules' / 'playwright')
    return node, playwright


def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _start(port, database, log):
    stream = open(log, 'w', encoding='utf-8')
    handle = subprocess.Popen([sys.executable, str(SERVER), str(port), str(database)],
                              stdout=stream, stderr=subprocess.STDOUT, cwd=str(ROOT))
    handle._log_stream = stream
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if handle.poll() is not None:
            raise AssertionError('server exited: ' + log.read_text(encoding='utf-8')[-3000:])
        if log.exists() and 'Traceback' in log.read_text(encoding='utf-8', errors='replace'):
            # A traceback in the server log is the reason a step failed; quoting it
            # here turns 'nothing happened' into the actual defect.
            raise AssertionError('server traceback: ' + log.read_text(encoding='utf-8', errors='replace')[-1500:])
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/api/health' % port, timeout=2) as r:
                if r.status == 200:
                    return handle
        except Exception:
            time.sleep(.25)
    handle.kill()
    raise AssertionError('server did not start: ' + log.read_text(encoding='utf-8')[-3000:])


def _stop(handle, log):
    handle.terminate()
    try:
        handle.wait(30)
    except subprocess.TimeoutExpired:
        handle.kill()
        handle.wait(30)
    assert handle.poll() is not None, 'server did not stop'
    handle._log_stream.close()


def _run(script, base, *rest, env):
    node, _ = _node()
    done = subprocess.run([node, str(script), base, *map(str, rest)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=900, env=env,
                          encoding="utf-8", errors="replace")
    output = (done.stdout or '') + (done.stderr or '')
    assert done.returncode == 0, output[-4000:]
    return output


def test_the_whole_player_path_survives_a_real_process_restart(tmp_path, monkeypatch):
    node, playwright = _node()
    require_browser()
    connect = socket.socket.connect
    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_only)
    profile = tmp_path / 'profile'
    profile.mkdir(parents=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    material = EVIDENCE / 'chronicle.md'
    material.write_text(MATERIAL, encoding='utf-8')
    database = profile / 'solo.sqlite3'
    env = {**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright), 'SOLO_TEST': '1'}

    # First run: material, preparation, hero, confirmation, chat, check, journey, save.
    (tmp_path / 'keep').mkdir(exist_ok=True)
    log1 = EVIDENCE / 'server-1.log'
    port = _free_port()
    first = _start(port, database, log1)
    try:
        base = 'http://127.0.0.1:%d' % port
        out = _run(ROOT / 'tests' / 'test_user_journey_real.cjs', base, material, EVIDENCE, env=env)
        assert 'JOURNEY_PART1_OK' in out, out[-4000:]
    finally:
        _stop(first, log1)

    # The process is gone; the profile on disk is all that remains.
    assert first.poll() is not None
    before = json.loads((EVIDENCE / 'before.json').read_text(encoding='utf-8'))

    # Second run: a new process, the same profile, the same chronicle.
    log2 = EVIDENCE / 'server-2.log'
    port2 = _free_port()
    second = _start(port2, database, log2)
    try:
        base2 = 'http://127.0.0.1:%d' % port2
        out = _run(ROOT / 'tests' / 'test_user_journey_real_resume.cjs', base2, EVIDENCE, env=env)
        assert 'JOURNEY_PART2_OK' in out, out[-4000:]
    finally:
        _stop(second, log2)
    after = json.loads((EVIDENCE / 'after.json').read_text(encoding='utf-8'))
    # The run leaves its own record next to the test, so a reviewer can repeat
    # the same acceptance without guessing which temporary folder it used.
    proof = Path(__file__).parent / 'fixtures' / 'journey-evidence.json'
    proof.write_text(json.dumps(dict(before=before, after=after, evidence=str(EVIDENCE.relative_to(ROOT))),
                                ensure_ascii=False, indent=1), encoding='utf-8')
    # The journey itself is asserted in the browser; here the record of it is checked.
    assert before['campaign'] and after['before'] == before
    assert after['after']['location'] == before['location'], 'the restart moved the hero'
    assert after['after']['turn'] >= before['turn'], 'the story did not continue'

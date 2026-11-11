"""A real server restart must not lose or replay a running game.

Stage D of the single-path hand-over asks for more than reopening a database in
the same process: the HTTP server is stopped and a second, genuinely separate
server process is started on the same profile. The same hero, world, memory and
resources must continue, and a repeated request must not create a second
campaign or execute the same turn twice.

Every assertion here was chosen because the obvious weaker version could pass
while the claim was false:

* the answered server is proved to be another process (it reports its own
  `os.getpid()`, which is neither this interpreter nor older than the campaign)
  and to have really opened this profile (a sentinel table created over a
  separate sqlite connection is read back through the child's own store);
* the turn is proved to be travel by the engine's own event
  (`results[0]['kind'] == 'world_travel'`), not by a location that happened to
  match after some other kind of reply;
* after a replay, the database is byte-identical and the state version and the
  event count are unchanged, so "did not execute the turn again" is observable
  even though every walk replay is answered with the same generic refusal text;
* a *fresh* request id to the same place must bump the version, which proves the
  unchanged version above can actually fail instead of being vacuous;
* the "иду в <место>" entry is proved to reach the world layer at all, because a
  destination the map cannot resolve falls through to the model instead of
  walking — checked last, since a clarification reply settles an open question
  that the *next* turn would otherwise absorb.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_control_play import table, world  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
SERVICE = """
import os
os.environ['SOLO_TEST']='1'
import sys, threading, time, uvicorn
from pathlib import Path
from solo.app import create_app
from solo.preview_mock import PreviewMock
port = int(os.environ['SOLO_CHILD_PORT'])
app = create_app(sys.argv[1], PreviewMock(), local_port=port, experimental=True, offline_mock=True)
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error',
                                       access_log=False, timeout_graceful_shutdown=5))
def serve():
    try:
        server.run()
    except BaseException as exc:
        print('RUNERR', type(exc).__name__, str(exc)[:160], flush=True)
thread = threading.Thread(target=serve, daemon=True)
thread.start()
deadline = time.monotonic() + 20
while not server.started and time.monotonic() < deadline:
    time.sleep(.05)
print('READY', server.started, flush=True)
# The parent proves it is talking to a different interpreter, not to itself.
Path(sys.argv[2] + '.pid').write_text(str(os.getpid()), encoding='utf-8')
try:
    sys.stdin.close()
except Exception:
    pass
while not os.path.exists(sys.argv[2]):
    time.sleep(.05)
server.should_exit = True
thread.join(15)
"""


def call(port, method, path, payload=None):
    """One request as a plain socket; urllib would hand loopback to the proxy."""
    import http.client
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Content-Type': 'application/json'} if body else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None
    finally:
        connection.close()


def free_port():
    """A port nobody holds right now.

    Not a reservation: uvicorn binds only when the child calls `run()`, so the
    socket is closed here again and a race window stays open. `start_server`
    retries an address-in-use child instead of failing the run.
    """
    reservation = socket.socket()
    try:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]
    finally:
        reservation.close()


def lineage(process):
    """The launched process and everything below it, captured while it lives."""
    import psutil
    try:
        root = psutil.Process(process.pid)
    except psutil.Error:
        return [process]
    return [process] + root.children(recursive=True)


def assert_separate_process(token, created):
    """The pid the child reported must be a live, later process than ours."""
    import psutil
    reported = Path(str(token) + '.pid')
    assert reported.exists(), 'child never reported its pid'
    pid = int(reported.read_text(encoding='utf-8'))
    assert pid != os.getpid(), 'the answered server is this very process'
    process = psutil.Process(pid)
    assert process.is_running(), pid
    assert process.create_time() >= created, 'the answered server predates the campaign'


def start_server(tmp_path, log, database, created, attempts=3):
    """A second server process on the same profile, started and waited for."""
    for attempt in range(attempts):
        child_port = free_port()
        token = tmp_path / ('stop-%d-%d' % (child_port, attempt))
        env = {**os.environ, 'SOLO_CHILD_PORT': str(child_port)}
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', '-c', SERVICE,
                                    str(database), str(token)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   text=True, env=env)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                status, health = call(child_port, 'GET', '/api/health')
            except (OSError, ValueError):
                status, health = None, None
            if status == 200:
                assert health.get('product') == 'Solo Realms AI', health
                assert_separate_process(token, created)
                return process, child_port, token
            if process.poll() is not None:
                log.flush()
                text = Path(log.name).read_text(encoding='utf-8', errors='replace')
                if 'address already in use' in text.lower() and attempt + 1 < attempts:
                    break
                raise AssertionError('second server exited rc=%r: %s' % (process.returncode, text[-600:]))
            time.sleep(.05)
        else:
            process.kill(); process.wait(10); log.flush()
            raise AssertionError('second server never answered: '
                                 + Path(log.name).read_text(encoding='utf-8', errors='replace')[-600:])
        process.wait(10)
    raise AssertionError('second server could not get a free port')


def stop_server(process, token):
    """Stop the child by its token, then prove nothing of it is left running."""
    import psutil
    watched = lineage(process)
    if process.poll() is None:
        token.write_text('stop', encoding='utf-8')
    try:
        process.wait(40)
    except subprocess.TimeoutExpired:
        for item in watched:
            if isinstance(item, subprocess.Popen):
                item.kill()
            else:
                item.kill()
        process.wait(10)
        raise AssertionError('a server process outlived its stop token (leak caught)')
    for item in watched:
        if isinstance(item, subprocess.Popen):
            continue
        try:
            item.wait(5)
        except psutil.TimeoutExpired:
            pid = item.pid
            item.kill()
            raise AssertionError('a grandchild of the server outlived its stop: %r' % (pid,))
    return process.returncode


def database_digest(path):
    """Size and bytes of the store and its WAL, which a write would change."""
    parts = []
    for suffix in ('', '-wal', '-shm'):
        candidate = Path(str(path) + suffix)
        parts.append(str(suffix) + ':' + (str(candidate.stat().st_size) if candidate.exists() else '-'))
        parts.append(candidate.read_bytes().hex() if candidate.exists() and suffix != '-shm' else '')
    import hashlib
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()


def test_a_running_game_survives_a_real_server_restart(world, tmp_path):  # noqa: F811
    """Stop the server, start another one, and keep playing the same table."""
    import sqlite3
    import psutil
    store, cid, service = table(tmp_path, world)
    from solo.world_service import WorldCommand
    service.execute(cid, WorldCommand(request_id='walk', kind='travel',
                                      target='The Sunken Gallery'), from_map=True)
    before = store.campaign(cid)['state']
    assert before['location'] == 'The Sunken Gallery'
    hero_before = dict(before['hero'])
    database = tmp_path / 'play.sqlite3'
    assert database.exists(), 'the game must live in the profile the child is given'
    created = psutil.Process(os.getpid()).create_time()

    # Written over a *separate* connection to the same file: if the child answers
    # for this campaign, it opened this profile and not an empty sibling.
    with sqlite3.connect(database) as probe:
        probe.execute('CREATE TABLE IF NOT EXISTS critic_sentinel (k TEXT PRIMARY KEY, v TEXT)')
        probe.execute("INSERT OR REPLACE INTO critic_sentinel VALUES ('restart', 'seen')")
        probe.commit()
        assert probe.execute("SELECT v FROM critic_sentinel WHERE k='restart'").fetchone() == ('seen',)

    log = (tmp_path / 'server.log').open('w', encoding='utf-8')
    process, port, token = start_server(tmp_path, log, database, created)
    try:
        status, campaigns = call(port, 'GET', '/api/campaigns')
        assert status == 200, campaigns
        assert len([c for c in campaigns if c.get('id') == cid]) == 1, \
            'the restarted server must show exactly one such campaign'

        status, current = call(port, 'GET', '/api/campaigns/' + cid)
        assert status == 200, current
        assert current['state']['location'] == 'The Sunken Gallery'
        assert current['state']['hero']['name'] == hero_before['name']
        assert {p['name'] for p in current['state']['locations']} == {
            p['name'] for p in before['locations']}
        assert store.campaign(cid)['state']['location'] == 'The Sunken Gallery', \
            'the parent process must not have moved while the child played'

        # Play continues on the restarted server through the free-text entry a
        # player types, and the engine itself says the turn was travel.
        version = int(current['version'])
        payload = dict(request_id='back-after-restart', text='Иду в Bell Landing',
                       expected_version=version)
        status, moved = call(port, 'POST', '/api/campaigns/%s/command-turn' % cid, payload)
        assert status == 200, moved
        assert moved['status'] == 'resolved', moved
        assert len(moved['events']) == 1, moved
        assert moved['events'][0]['results'][0]['kind'] == 'world_travel', moved['events'][0]
        version_after = int(moved['state_version'])
        assert version_after > version, (version, version_after)
        status, after = call(port, 'GET', '/api/campaigns/' + cid)
        assert after['state']['location'] == 'Bell Landing', after['state']['location']

        # The very same request id, replayed with the stale version the player
        # still holds: no second walk, no second campaign, no version bump, and
        # not one byte written to the profile.
        #
        # This assertion used to demand 'rejected'. A refusal is not safety here:
        # the hero had already walked, and telling the player "that did not work"
        # invited them to repeat a finished action under a new id. The audit
        # (section 5) recorded exactly that. The replay now returns the confirmed
        # outcome, and every guarantee below still proves nothing executed twice.
        fingerprint = database_digest(database)
        status, again = call(port, 'POST', '/api/campaigns/%s/command-turn' % cid, payload)
        assert status == 200, again
        assert again['status'] == 'resolved', again
        assert again.get('replayed') is True, 'the reply must name itself a replay'
        assert again['events'], 'a replay still answers with the confirmed outcome'
        assert again['events'][0]['id'] == moved['events'][0]['id'], 'the replay names the original event'
        assert int(again['state_version']) == version_after, (again['state_version'], version_after)
        log.flush()
        status, repeated = call(port, 'GET', '/api/campaigns/' + cid)
        assert int(repeated['version']) == version_after, 'the replay moved the game'
        assert repeated['state']['location'] == 'Bell Landing'
        assert len(repeated.get('history') or []) == len(after.get('history') or []), \
            'the replay appended history'
        assert database_digest(database) == fingerprint, 'the replay wrote to the profile'
        status, final = call(port, 'GET', '/api/campaigns')
        assert len([c for c in final if c.get('id') == cid]) == 1

        # Control: a *fresh* request id to the same place does execute and does
        # bump the version, so "unchanged" above can actually fail.
        status, fresh = call(port, 'POST', '/api/campaigns/%s/command-turn' % cid,
                             dict(request_id='fresh-walk', text='Иду в Bell Landing',
                                  expected_version=version_after))
        assert status == 200, fresh
        assert fresh['status'] == 'resolved' and len(fresh['events']) == 1, fresh
        assert int(fresh['state_version']) > version_after, fresh

        # Last, because a clarification reply opens a question that would be
        # absorbed by the next turn: an unresolvable destination must be refused
        # by the engine, never silently turned into a model cycle.
        version_end = int(fresh['state_version'])
        status, refused = call(port, 'POST', '/api/campaigns/%s/command-turn' % cid,
                               dict(request_id='unresolvable-walk', text='Иду в Никуда',
                                    expected_version=version_end))
        assert status == 200, refused
        assert refused['status'] in ('rejected', 'notice', 'clarification', 'unsupported', 'ai_error'), refused
        assert refused['events'] == [], refused
        status, unchanged = call(port, 'GET', '/api/campaigns/' + cid)
        assert int(unchanged['version']) == version_end, 'a refused walk moved the game'
        assert unchanged['state']['location'] == 'Bell Landing'
    finally:
        code = stop_server(process, token)
        log.close()
    assert code == 0, (tmp_path / 'server.log').read_text(encoding='utf-8')

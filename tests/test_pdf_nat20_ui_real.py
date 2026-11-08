"""Synthetic validated world -> real HTTP/UI/Nat20 -> separate process continuation."""
import os
os.environ['SOLO_TEST']='1'

import json
import hashlib
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from browser_available import require_browser
import uvicorn
from solo.app import create_app
from solo.preview_mock import PreviewMock
from test_pdf_nat20_start import prepare
from wizard_fill import legal_draft


def test_pdf_nat20_ui_real_start_and_process_restart(tmp_path,monkeypatch):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node=os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime/'bin/node.exe'
    playwright=os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime/'node_modules/playwright'
    require_browser()
    original=socket.socket.connect
    def local_only(sock,address):
        assert address[0] in ('127.0.0.1','::1'),'External connections forbidden'
        return original(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local_only)
    # Shared backend test utility imports a short genuine synthetic PDF and validates
    # a full schema response through MockTransport. No browser endpoint is faked below.
    _,client,did,_,status,_=prepare(tmp_path)
    client.__exit__(None,None,None)
    assert status['ready'],status
    db=tmp_path/'game.sqlite3'
    script=Path(__file__).with_suffix('.cjs')
    out=Path(os.environ.get('SOLO_UI_EVIDENCE',tmp_path));out.mkdir(parents=True,exist_ok=True)
    # The wizard resumes a saved draft, so it is seeded with a legal one: a build
    # payload alone leaves every owed step unanswered.
    # Playwright writes its browser artifacts under TEMP, which must stay inside
    # the run's own scratch directory rather than the user's profile.
    scratch=out if out.is_absolute() else (Path.cwd()/out)
    scratch.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'SOLO_TEST':'1','SOLO_UI_PLAYWRIGHT':str(playwright),'SOLO_UI_DRAFT':json.dumps(legal_draft()),
         'TEMP':str(scratch),'TMP':str(scratch),'TMPDIR':str(scratch)}
    def browser(port,stage):
        result=subprocess.run([str(node),str(script),f'http://127.0.0.1:{port}',did,str(out),stage],env=env,
                              capture_output=True,text=True,encoding='utf-8',timeout=150)
        assert result.returncode==0,result.stdout+result.stderr
        print(result.stdout.strip())
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(db,PreviewMock(),local_port=port,experimental=True,offline_mock=True)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='info',access_log=False,timeout_graceful_shutdown=5))
    sock.close()
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+20
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        browser(port,'start')
        result_file=out/'real-results.json';record=json.loads(result_file.read_text(encoding='utf-8'))
        current=app.state.store.campaign(record['campaign_id'])
        assert current['state']['nat20']['definition']['encounters']=={}
        assert current['state']['nat20']['definition']['build']['classes']['rogue']==1
        record['native_sha256']=hashlib.sha256(json.dumps(current['state']['nat20'],sort_keys=True).encode()).hexdigest()
        result_file.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    finally:
        server.should_exit=True;thread.join(30);sock.close();assert not thread.is_alive()
    # A fresh Python process opens only this task's synthetic profile.
    program="""
import os;os.environ['SOLO_TEST']='1'
import sys,socket,threading,time,uvicorn,json,hashlib
from solo.app import create_app
from solo.preview_mock import PreviewMock
original=socket.socket.connect
def local(sock,address):
 assert address[0] in ('127.0.0.1','::1'),'External connections forbidden'
 return original(sock,address)
socket.socket.connect=local
port=int(os.environ['SOLO_CHILD_PORT'])
print('PORT',port,flush=True)
app=create_app(sys.argv[1],PreviewMock(),local_port=port,experimental=True,offline_mock=True)
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False,timeout_graceful_shutdown=5))
def _serve():
 try:
  server.run()
 except BaseException as exc:
  print('RUNERR',type(exc).__name__,str(exc)[:160],flush=True)
thread=threading.Thread(target=_serve,daemon=True)
thread.start()

_wait=time.monotonic()+20
while not server.started and time.monotonic()<_wait:
 time.sleep(.05)
print('READY',server.started,flush=True)


# This process must never sit in a blocking read on the inherited stdin PIPE.
# /api/health calls build_identity() -> subprocess.run(['git',...],capture_output=True)
# and that child inherits this process's stdin handle. While the main thread is
# blocked in _io on that pipe, git never signals EOF, the stdout reader thread stays
# in subprocess._readerthread, communicate() never returns, and every health request
# hangs even though the port is LISTENING. So close the inherited copy and stop on a
# sentinel file instead: no handler-side subprocess can inherit a blocked pipe.
try:sys.stdin.close()
except Exception:pass
_token=sys.argv[3]
t0=time.monotonic()
while not os.path.exists(_token) and time.monotonic()-t0<180:
 time.sleep(.1)
server.should_exit=True
thread.join(15)
record=json.load(open(sys.argv[2],encoding='utf-8'))
native=app.state.store.campaign(record['campaign_id'])['state']['nat20']
assert hashlib.sha256(json.dumps(native,sort_keys=True).encode()).hexdigest()==record['native_sha256']
assert not thread.is_alive()
assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
"""
    # The port is reserved then released and handed to the child; the child is the
    # only one that binds it, so a process taking it in between makes the child
    # fail to serve instead of silently serving somewhere else.
    reservation=socket.socket();reservation.bind(('127.0.0.1',0))
    child_port=reservation.getsockname()[1];reservation.close()
    with (tmp_path/'restart.log').open('w',encoding='utf-8') as log:
        child_env={**env,'SOLO_CHILD_PORT':str(child_port)}
        # stdin is NOT a pipe here. The child closes its inherited stdin because
        # subprocess.run inside /api/health (build_identity -> git) would inherit a
        # blocked pipe and never return; the child is stopped by writing a token file.
        token=tmp_path/'stop.token'
        proc=subprocess.Popen([sys.executable,'-c',program,str(db),str(out/'real-results.json'),str(token)],
                              stdin=subprocess.DEVNULL,stdout=log,stderr=log,text=True,env=child_env)
        try:
            deadline=time.monotonic()+60;t0=time.monotonic()
            while True:
                try:
                    # A system proxy would carry this loopback probe to itself, so the child is
                    # reached with a direct opener that ignores the environment.
                    # A system proxy would carry a urllib loopback probe to the proxy itself,
                    # and the child serves through its own loop; a plain socket read is the
                    # only probe that cannot be redirected or swallowed.
                    _raw=socket.create_connection(('127.0.0.1',child_port),timeout=2)
                    _raw.settimeout(3)
                    _raw.sendall(b'GET /api/health HTTP/1.1' + bytes([13,10]) + ('Host: 127.0.0.1:'+str(child_port)).encode() + bytes([13,10,13,10]))
                    _reply=_raw.recv(40);_raw.close()
                    assert b'200' in _reply,_reply
                    break
                except OSError:
                    assert proc.poll() is None,'child exited rc='+str(proc.poll())+chr(10)+(tmp_path/'restart.log').read_text(encoding='utf-8')
                    assert time.monotonic()<deadline
                    time.sleep(.5)
            browser(child_port,'resume')
        finally:
            try:
                token.write_text('stop',encoding='utf-8')
                proc.wait(40)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait(10)
                raise
        assert proc.returncode==0,(tmp_path/'restart.log').read_text(encoding='utf-8')
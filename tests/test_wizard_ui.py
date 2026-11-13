"""Real browser seam for the step wizard: contract, sheet, create, level-up refusal."""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.preview_mock import PreviewMock


def test_wizard_ui_real_create(tmp_path,monkeypatch):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node=os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime/'bin/node.exe'
    playwright=os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime/'node_modules/playwright'
    if not Path(node).is_file() or not Path(playwright).exists():pytest.skip('Local browser runtime required')
    original=socket.socket.connect
    def local_only(sock,address):
        assert address[0] in ('127.0.0.1','::1'),'External calls forbidden'
        return original(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local_only)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(tmp_path/'wizard-ui.sqlite3',PreviewMock(),local_port=port,experimental=True,offline_mock=True)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+20
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        result=subprocess.run([str(node),str(Path(__file__).with_suffix('.cjs')),f'http://127.0.0.1:{port}',str(tmp_path)],
            env={**os.environ,'SOLO_UI_PLAYWRIGHT':str(playwright)},capture_output=True,text=True,encoding='utf-8',timeout=300)
        assert result.returncode==0,result.stdout+result.stderr
        print(result.stdout.strip())
        assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    finally:
        # Shutdown is cooperative: under load the server may still be closing its
        # transports when the join window ends, so wait long enough before calling
        # a live thread a failure.
        server.should_exit=True;thread.join(30);sock.close();assert not thread.is_alive()
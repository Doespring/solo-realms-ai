"""Readiness/resume browser regressions with explicit mocked provider endpoints."""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path
import sys
import shutil
import socket
import subprocess
import threading
import time

import pytest

from browser_available import require_browser
import uvicorn
from solo.app import create_app
from solo.llm import MockLLM


def test_pdf_play_today_ui_guards(tmp_path,monkeypatch):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node=os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime/'bin/node.exe'
    playwright=os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime/'node_modules/playwright'
    require_browser()
    connect=socket.socket.connect
    def local_only(sock,address):
        assert address[0] in ('127.0.0.1','::1'),'External calls forbidden'
        return connect(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local_only)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(tmp_path/'pdf-nat20-ui.sqlite3',MockLLM(),local_port=port,experimental=True,offline_mock=True)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+20
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        # The fixture must not hand-write rule choices: SRD 5.2 asks for skills,
        # expertise and an origin feat, and a build that skips them is refused.
        sys.path.insert(0,str(Path(__file__).parent))
        # The seed is a wizard draft (choices and kits included), not the final
        # build payload: the contract must reopen every step already answered.
        from wizard_fill import legal_draft
        import json as _json
        result=subprocess.run([str(node),str(Path(__file__).with_suffix('.cjs')),f'http://127.0.0.1:{port}',str(tmp_path)],
            env={**os.environ,'SOLO_UI_PLAYWRIGHT':str(playwright),'SOLO_UI_DRAFT':_json.dumps(legal_draft())},capture_output=True,text=True,encoding='utf-8',timeout=90)
        assert result.returncode==0,result.stdout+result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit=True;thread.join(10);sock.close();assert not thread.is_alive()

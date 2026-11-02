"""Real browser seam for the level-up mode: earned level, delta, atomic confirm."""
import json
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest

from browser_available import require_browser
import uvicorn

from solo.app import create_app
from solo.preview_mock import PreviewMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


def test_level_up_ui_real(tmp_path,monkeypatch):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node=os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime/'bin/node.exe'
    playwright=os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime/'node_modules/playwright'
    require_browser()
    original=socket.socket.connect
    def local_only(sock,address):
        assert address[0] in ('127.0.0.1','::1'),'External calls forbidden'
        return original(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local_only)
    monkeypatch.setattr('solo.nat20_api.secrets.randbits',lambda bits:5)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(tmp_path/'levelup-ui.sqlite3',PreviewMock(),local_port=port,experimental=True,offline_mock=True)
    from fastapi.testclient import TestClient
    with TestClient(app) as api:
        created=api.post('/api/prototype/campaigns',json=dict(name='Арден',request_id='create-ui',adventure='authored',
            description='Пустой погреб.',build=legal(class_slug='fighter',species_slug='human',background='soldier'),
            prepared_spells=[],encounter_monsters=['rat'],encounter_attack_bonuses={'rat':0},
            encounter_attack_sources={'rat':'Authored smoke encounter: explicit +0 selected by test author.'}))
        assert created.status_code==200,created.text
        cid=created.json()['id']
    current=app.state.store.campaign(cid)
    runtime=current['state']['nat20']
    reward=runtime['definition']['encounters']['opening']['reward']
    runtime['experience']=300
    runtime['rewards']=[dict(id=reward['id'],encounter_id='opening',combat_id='opening',experience=300,
        outcome_sha256='0'*64,source=reward['source'])]
    with app.state.store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=?, version=version+1 WHERE id=?',(json.dumps(current['state'],ensure_ascii=False),cid))
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+20
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        result=subprocess.run([str(node),str(Path(__file__).with_suffix('.cjs')),f'http://127.0.0.1:{port}',str(tmp_path),cid],
            env={**os.environ,'SOLO_UI_PLAYWRIGHT':str(playwright)},capture_output=True,text=True,encoding='utf-8',timeout=300)
        assert result.returncode==0,result.stdout+result.stderr
        print(result.stdout.strip())
        assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    finally:
        server.should_exit=True;thread.join(10);sock.close();assert not thread.is_alive()

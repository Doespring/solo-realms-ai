"""Native action parameter UI smoke; isolated real backend with no live AI."""
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
from solo.llm import MockLLM


def test_stage2_death_and_spell_ui(tmp_path,monkeypatch):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node=os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime/'bin/node.exe'
    playwright=os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime/'node_modules/playwright'
    if not Path(node).is_file() or not Path(playwright).exists():pytest.skip('Local browser runtime required')
    connect=socket.socket.connect
    def local_only(sock,address):
        assert address[0] in ('127.0.0.1','::1'),'External calls forbidden'
        return connect(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local_only)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(tmp_path/'stage2-ui.sqlite3',MockLLM(),local_port=port,experimental=True,offline_mock=True)
    import json
    from solo.nat20_api import Nat20GameEngine
    from test_nat20_prototype import build
    engine=Nat20GameEngine(app.state.store)
    from test_nat20_prototype import run
    dying=engine.create_campaign('UI Dying',seed=5,hp_current=1)['id']
    run(engine,dying,'start',kind='start_combat',encounter_id='basic')
    for index in range(6):
        run(engine,dying,'wait-'+str(index),kind='end_turn')
        if app.state.store.campaign(dying)['state']['hero']['hp']==0:break
    else:raise AssertionError('Fixture never reached zero HP')
    unsupported=next(row['slug'] for row in engine.catalog('spells',limit=200)['entries'] if row['execution_status']=='narrative_or_unsupported_in_combat')
    wizard=engine.create_experimental_campaign('UI Spells',build=build('wizard'),prepared_spells=[unsupported,'magic-missile','bless'],encounters={},seed=5)['id']
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+20
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        result=subprocess.run([str(node),str(Path(__file__).with_suffix('.cjs')),f'http://127.0.0.1:{port}',str(tmp_path)],
            env={**os.environ,'SOLO_UI_PLAYWRIGHT':str(playwright)},capture_output=True,text=True,encoding='utf-8',timeout=240)
        assert result.returncode==0,result.stdout+result.stderr
        print(result.stdout.strip())
        import json
        record=json.loads((Path(os.environ.get('SOLO_UI_EVIDENCE',tmp_path))/'results.json').read_text(encoding='utf-8'))
        assert app.state.store.campaign(dying)['state']['nat20']['experience']==0
        assert app.state.store.campaign(wizard)['version']==0
        record['verified_no_xp_or_spell_mutation']=True
        (Path(os.environ.get('SOLO_UI_EVIDENCE',tmp_path))/'results.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    finally:
        server.should_exit=True;thread.join(40);sock.close();assert not thread.is_alive()

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


def test_native_action_parameters_real_ui(tmp_path,monkeypatch):
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
    app=create_app(tmp_path/'native-parameters.sqlite3',MockLLM(),local_port=port,experimental=True,offline_mock=True)
    import json
    from solo.nat20_api import Nat20GameEngine
    from test_nat20_prototype import build
    engine=Nat20GameEngine(app.state.store)
    scene=dict(grid=dict(width=10,height=8),hero_position=dict(x=1,y=1,z=0),actors=[dict(id='foe',monster='ogre',position=dict(x=3,y=1,z=0))])
    # The level must reach the classes map too, and the subclass gate it opens
    # must be spent, so each build is derived from its level and picks.
    from nat20_builds import legal
    wizard=legal(class_slug='wizard',species_slug='human',level=5,subclass_slug='evoker',equipment=['quarterstaff','wand-of-magic-missiles'])
    paladin=legal(class_slug='paladin',species_slug='human',level=2,equipment=['longsword'])
    ids={name:engine.create_experimental_campaign('UI '+name,build=chosen,encounters={'trial':scene},prepared_spells=spells,seed=5)['id'] for name,chosen,spells in [('Wizard resources',wizard,['burning-hands']),('Paladin pool',paladin,[])]}
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
        wizard_state=app.state.store.campaign(ids['Wizard resources'])['state']['nat20']
        assert any(key.endswith('wand-of-magic-missiles') and value['spent']==2 for key,value in wizard_state['hero']['custom_counters'].items())
        assert wizard_state['hero']['spell_slots']['1']==3
        paladin_state=app.state.store.campaign(ids['Paladin pool'])['state']['nat20']
        assert paladin_state['hero']['custom_counters']['feature_use:lay-on-hands']['spent']==3
        record.update(verified_resources=dict(wand_charges_spent=2,first_level_slots_remaining=3,lay_on_hands_spent=3))
        (Path(os.environ.get('SOLO_UI_EVIDENCE',tmp_path))/'results.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    finally:
        server.should_exit=True;thread.join(40);sock.close();assert not thread.is_alive()

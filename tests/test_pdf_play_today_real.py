"""Real user path and persisted resume, isolated synthetic PDF and offline transport."""
import os
os.environ['SOLO_TEST']='1'
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import httpx
import pytest
import uvicorn
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from solo.app import create_app
from solo.narrative_grounding import fallback
from scripts.playable_ai_readiness import mock_cloud
from test_cloud import sse
from test_pdf_nat20_start import SOURCE,proposal


def synthetic_book():
    writer=PdfWriter()
    for index in range(4):
        place=['Forge','Market','Tower','Cave'][index]
        lines=(SOURCE if index==0 else [f'D&D 2014. Forge and Tower. {place} is quiet.'])+['This is synthetic descriptive background. '*2]*50
        page=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 8 Tf 20 750 Td 12 TL '+' '.join('('+line.replace('(','\\(').replace(')','\\)')+') Tj T*' for line in lines)+' ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output);return output.getvalue()


def serve(folder,port,phase,out):
    folder,out=Path(folder),Path(out);calls=[]
    original=socket.socket.connect
    def local(sock,address):
        assert address[0] in ('127.0.0.1','::1'),'External connections forbidden'
        return original(sock,address)
    socket.socket.connect=local
    def respond(request):
        packet=json.loads(json.loads(request.content)['messages'][-1]['content'])
        if packet.get('task') in ('campaign-prepare-v1','source-extract-v1'):
            index=packet['batch'];calls.append(index)
            if phase=='partial' and index==1:return httpx.Response(503,text='Synthetic block failure')
            text=packet['pages'][0]['text']
            if 'Dorn is in Forge' in text:value=proposal(packet,'full')
            else:
                place='Market' if 'Market is quiet.' in text else 'Cave'
                value=dict(title='Forge and Tower',ruleset='dnd2014',start='Forge',entities=[dict(key=place.lower(),kind='location',data=dict(name=place,description=place+' is quiet.'),evidence=[dict(source_span='S001')])])
        elif 'player_intent' in packet:
            text=packet['player_intent']
            if text.startswith('Спрашиваю'):value=dict(status='conversation',target=packet['context']['scene']['npc'][0]['id'])
            elif text.startswith('Проверяю'):value=dict(status='command',action=dict(kind='check',check_id='save-wis' if 'wis' in text else 'save-dex'),message='')
            else:value=dict(status='observe',target='')
        else:value=fallback(packet)
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(value,ensure_ascii=False)))]),dict(choices=[],usage=dict(prompt_tokens=1000,completion_tokens=100))])
    llm,_=mock_cloud(folder,respond)
    app=create_app(folder/'game.sqlite3',llm,local_port=int(port),experimental=True)
    record_file=out/'real-path.json'
    if phase=='continue':
        record=json.loads(record_file.read_text(encoding='utf-8'));native=app.state.store.campaign(record['campaign_id'])['state']['nat20']
        assert hashlib.sha256(json.dumps(native,sort_keys=True).encode()).hexdigest()==record['native_sha256']
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(port),log_level='error',access_log=False,timeout_graceful_shutdown=5))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    # Windows import workers inherit stdin. A blocking pipe read in their parent
    # can stall interpreter startup; use an isolated stop marker instead.
    try:
        while not (folder/(phase+'.stop')).exists():time.sleep(.05)
    finally:
        server.should_exit=True;thread.join(40)
    assert not thread.is_alive()
    with app.state.preparation.connection() as connection:
        good=[dict(row) for row in connection.execute('SELECT id,body FROM creation_parts WHERE status="validated" ORDER BY id')]
    if phase=='partial':(out/'good-blocks.json').write_text(json.dumps(good),encoding='utf-8')
    else:
        before=json.loads((out/'good-blocks.json').read_text(encoding='utf-8'))
        assert all(row in good for row in before),'Saved validated blocks changed'
        if phase=='resume':assert calls==[1],calls
    if phase=='resume':
        record=json.loads(record_file.read_text(encoding='utf-8'));current=app.state.store.campaign(record['campaign_id'])
        assert current['state']['nat20']['definition']['encounters']=={}
        assert current['state']['nat20']['definition']['build']['classes']['rogue']==1
        record['native_sha256']=hashlib.sha256(json.dumps(current['state']['nat20'],sort_keys=True).encode()).hexdigest()
        record_file.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        assert len(app.state.store.list_campaigns())==1
    assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    (out/(phase+'-server.json')).write_text(json.dumps(dict(phase=phase,preparation_batches=calls,validated_blocks=len(good),old_validated_blocks_unchanged=True,process_stopped=True,real_provider_calls=0)),encoding='utf-8')


def test_pdf_play_today_real_user_path_restart_and_retry(tmp_path):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node=os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime/'bin/node.exe'
    playwright=os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime/'node_modules/playwright'
    if not Path(node).is_file() or not Path(playwright).exists():pytest.skip('Local browser runtime required')
    out=Path(os.environ.get('SOLO_UI_EVIDENCE',tmp_path));out.mkdir(parents=True,exist_ok=True)
    source=tmp_path/'synthetic-book.pdf';source.write_bytes(synthetic_book())
    # The hero the browser drafts must pass the server's own admissibility check:
    # a wizard draft (choices and kits included), not the final build payload.
    sys.path.insert(0,str(Path(__file__).parent))
    from wizard_fill import legal_draft
    env={**os.environ,'SOLO_TEST':'1','SOLO_UI_PLAYWRIGHT':str(playwright),'SOLO_UI_DRAFT':json.dumps(legal_draft())}
    program="import sys;sys.path.insert(0,'tests');from test_pdf_play_today_real import serve;serve(*sys.argv[1:])"
    for phase in ('partial','resume','continue'):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with (tmp_path/(phase+'.log')).open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-c',program,str(tmp_path),str(port),phase,str(out)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,text=True,env=env)
            try:
                deadline=time.monotonic()+25
                while True:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:assert response.status==200
                        break
                    except OSError:
                        assert process.poll() is None,(tmp_path/(phase+'.log')).read_text(encoding='utf-8')
                        assert time.monotonic()<deadline;time.sleep(.1)
                result=subprocess.run([str(node),str(Path(__file__).with_suffix('.cjs')),f'http://127.0.0.1:{port}',phase,str(out),str(source)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=200)
                assert result.returncode==0,result.stdout+result.stderr
                print(result.stdout.strip(),flush=True)
            finally:
                (tmp_path/(phase+'.stop')).write_text('stop',encoding='ascii')
                try:process.wait(timeout=50)
                except subprocess.TimeoutExpired:process.kill();process.wait(10);raise
            assert process.returncode==0,(tmp_path/(phase+'.log')).read_text(encoding='utf-8')

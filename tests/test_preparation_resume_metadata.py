"""Safe persisted resume mode; all source/profile data is synthetic and isolated."""
import os
os.environ['SOLO_TEST']='1'

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.playable_ai_readiness import mock_cloud
from solo.app import ROOT,create_app
from solo.preparation_pipeline import resume_metadata
from solo.prototype_prepared import prepared_start_readiness
from test_cloud import sse


CANARY='PRIVATE_RESUME_SOURCE_KEY_BODY_CANARY'


def terminal(client,run):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        value=client.get('/api/creation/runs/'+run).json()
        assert CANARY not in json.dumps(value)
        if value['status'] not in ('queued','running'):return value
        time.sleep(.02)
    raise AssertionError('Synthetic preparation did not finish')


def model(directory,*,fail=False):
    calls=[];running=[];holder={}
    def respond(request):
        packet=json.loads(json.loads(request.content)['messages'][-1]['content'])
        index=packet['batch'];calls.append(index)
        if holder:
            value=holder['app'].state.preparation_pipeline.latest(holder['draft'])
            running.append((value['status'],value['preparation_mode'],value['source_ruleset']))
        if fail and index==1:return httpx.Response(503,json=dict(error='Synthetic unavailable'))
        entities=[]
        for page in packet['pages']:
            place=re.search(r'Place[A-C]',page['text']).group()
            span=re.search(r'\[(S\d+)\]',page['text'])[1]
            entities.append(dict(key=place,kind='location',data=dict(name=place,description=place+' is quiet.'),evidence=[dict(source_span=span)]))
        value=dict(title='Synthetic resume',ruleset='dnd2014',start=entities[0]['data']['name'],entities=entities)
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(value)))]),dict(choices=[],usage=dict(prompt_tokens=1000,completion_tokens=200))])
    llm,profile=mock_cloud(directory,respond)
    profile.context_tokens=262144  # Synthetic large-block stand, no user limits.
    llm.settings.save(profile)
    return llm,calls,running,holder


@pytest.mark.parametrize('legacy_unknown',[False,True])
def test_partial_world_new_process_local_readiness_and_explicit_resume(tmp_path,legacy_unknown):
    llm,calls,running,holder=model(tmp_path,fail=True)
    db=tmp_path/'game.sqlite3'
    with TestClient(create_app(db,llm)) as client:
        created=client.post('/api/creation/drafts').json();did=created['id'];url='/api/creation/drafts/'+did
        client.put(url,json=dict(description='Synthetic D&D 2014',revision=created['revision'])).raise_for_status()
        for suffix in 'ABC':
            raw=((f'Read aloud: Place{suffix} is quiet.\nPlace{suffix} {CANARY}\n')*170).encode()
            uploaded=client.post(url+'/files',files={'file':('source'+suffix+'.txt',raw)});uploaded.raise_for_status()
        deadline=time.monotonic()+20
        while any(f['job']['status']!='complete' for f in client.get(url).json()['files']):
            assert time.monotonic()<deadline;time.sleep(.02)
        holder.update(app=client.app,draft=did)
        request=dict(request_id='world-first',world_structure=True,ruleset='dnd2014',consent_to_send=True)
        queued=client.post(url+'/prepare',json=request).json()
        assert queued['preparation_mode']=='world_structure' and queued['source_ruleset']=='dnd2014'
        first=terminal(client,queued['id'])
        assert first['status']=='partial' and first['done']==first['total']-1
        assert all(row[1:]==('world_structure','dnd2014') for row in running)
        assert running and all(row[0]=='running' for row in running)
        pipeline=client.app.state.preparation_pipeline;prep=client.app.state.preparation
        # Same request remains idempotent, with unchanged fingerprint and no calls.
        count=len(calls);fingerprint=pipeline.raw(first['id'])['fingerprint']
        assert client.post(url+'/prepare',json=request).json()['id']==first['id'] and len(calls)==count
        with prep.connection() as conn:
            good={r['id']:(r['signature'],r['fingerprint'],hashlib.sha256(r['body'].encode()).hexdigest(),r['attempts']) for r in conn.execute('SELECT * FROM creation_parts WHERE status="validated"')}
            if legacy_unknown:
                conn.execute('UPDATE creation_runs SET result=NULL WHERE id=?',(first['id'],))
    program='''
import os;os.environ['SOLO_TEST']='1'
import sys,json
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.llm import MockLLM
from solo.prototype_prepared import prepared_start_readiness
model=MockLLM()
with TestClient(create_app(sys.argv[1],model)) as c:
 status=c.get('/api/creation/runs/'+sys.argv[3]).json()
 local=c.post('/api/creation/drafts/'+sys.argv[2]+'/readiness',json={});assert local.status_code==200,local.text
 projected=c.get('/api/creation/drafts/'+sys.argv[2]).json()['preparation']
 world=prepared_start_readiness(c.app.state.preparation,sys.argv[2])
 print(json.dumps(dict(status=status,local=local.json(),projected=projected,world=world,ai_calls=len(model.metrics))))
'''
    child=subprocess.run([sys.executable,'-B','-c',program,str(db),did,first['id']],cwd=ROOT,
        env={**os.environ,'SOLO_TEST':'1','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,encoding='utf8',timeout=40)
    assert child.returncode==0,child.stderr
    safe=json.loads(child.stdout);assert CANARY not in child.stdout and safe['ai_calls']==0
    mode='unknown' if legacy_unknown else 'world_structure'
    for key in ('status','local','projected'):
        assert safe[key]['preparation_mode']==mode and safe[key]['source_ruleset']=='dnd2014'
        assert not safe[key]['ready']
    assert not safe['world']['world_ready']
    llm,resumed_calls,_,_=model(tmp_path)
    with TestClient(create_app(db,llm)) as client:
        # Omitting the mode must never re-send world blocks as compact names. An uploaded
        # book now defaults to world structure, so omission keeps the stored mode instead
        # of downgrading it, and only the missing block is paid for again.
        missing=client.post(url+'/prepare',json=dict(request_id='missing-mode',ruleset='dnd2014',consent_to_send=True))
        if legacy_unknown:
            # A run whose stored mode was lost still needs one explicit choice.
            assert missing.status_code==409 and not resumed_calls
        else:
            assert missing.status_code==200,missing.text
            assert missing.json()['preparation_mode']=='world_structure'
        resumed=client.post(url+'/prepare',json=dict(request_id='explicit-resume',world_structure=True,ruleset='dnd2014',consent_to_send=True))
        resumed.raise_for_status();done=terminal(client,resumed.json()['id'])
        assert done['preparation_mode']=='world_structure' and done['source_ruleset']=='dnd2014'
        assert done['done']==done['total'] and resumed_calls==[1]
        assert client.app.state.preparation_pipeline.raw(first['id'])['fingerprint']==fingerprint
        with client.app.state.preparation.connection() as conn:
            for identity,expected in good.items():
                r=conn.execute('SELECT * FROM creation_parts WHERE id=?',(identity,)).fetchone()
                assert (r['signature'],r['fingerprint'],hashlib.sha256(r['body'].encode()).hexdigest(),r['attempts'])==expected


@pytest.mark.parametrize('result,mode,edition',[
    (None,'unknown',None),
    ({'definition':{'ruleset':'dnd2014'}},'unknown','dnd2014'),
    ({'start_contract':{'world_structure':True},'definition':{'ruleset':'dnd2014'}},'world_structure','dnd2014'),
    ({'start_contract':{'world_structure':False},'definition':{'ruleset':'dnd2024'}},'source_extract','dnd2024'),
    ({'start_contract':{'world_structure':1}},'unknown',None),
    ({'preparation_metadata':{'preparation_mode':'invalid','source_ruleset':CANARY},'private':CANARY},'unknown',None),
])
def test_safe_explicit_metadata_without_inference(result,mode,edition):
    assert resume_metadata(result)==dict(preparation_mode=mode,source_ruleset=edition)


def test_legacy_ready_status_is_not_world_ready(tmp_path):
    llm,calls,_,_=model(tmp_path)
    with TestClient(create_app(tmp_path/'legacy.sqlite3',llm)) as client:
        did=client.post('/api/creation/drafts').json()['id'];pipeline=client.app.state.preparation_pipeline
        private=dict(ready=True,definition=dict(ruleset='dnd2014',world=dict(locations=[])),quarantine=[],private_body=CANARY)
        with client.app.state.preparation.connection() as conn:
            conn.execute('INSERT INTO creation_runs VALUES(?,?,?,?,?,?,?,?,?,?)',('legacy',did,'old','unchanged','ready',37,37,'',json.dumps(private),1))
        result=pipeline.status('legacy')
        assert result['preparation_mode']=='unknown' and result['source_ruleset']=='dnd2014' and not result['ready']
        assert not prepared_start_readiness(client.app.state.preparation,did)['world_ready']
        assert CANARY not in json.dumps(result) and not calls

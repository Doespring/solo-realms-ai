"""Explicit peaceful experimental worlds; no dummy enemy or user data."""
from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import ROOT
from solo.nat20_prototype import PrototypeEncounter
from solo.nat20_screen import Nat20Screen
from solo.storage import Store
from test_nat20_prototype import build, encounter, offline, run


def test_explicit_empty_rogue_world_check_save_new_process_and_unknown_encounter(tmp_path):
    api=Nat20GameEngine(Store(tmp_path/'peaceful.sqlite3'))
    cid=api.create_experimental_campaign('Peaceful Rogue',build=build('rogue'),encounters={},seed=5)['id']
    current=api.store.campaign(cid)
    assert current['state']['nat20']['definition']['encounters']=={}
    assert current['state']['nat20']['combat'] is None
    assert api.capabilities(current)['encounters']==[]
    projection=Nat20Screen().project(current)
    assert not any(c['parameters']['kind']=='start_combat' for c in projection['commands'])
    result=run(api,cid,'check',kind='check',check_id='athletics')
    assert result['result']['kind']=='skill'
    assert result['event'].payload['command']=='check'
    checkpoint=api.store.save(cid,'Peaceful save')
    saved=deepcopy(api.store.campaign(cid)['state']['nat20'])
    before=deepcopy(api.store.campaign(cid));history=api.store.history(cid)
    with pytest.raises(ValueError,match='Encounter is not in the registered campaign'):
        run(api,cid,'missing',kind='start_combat',encounter_id='missing')
    assert api.store.campaign(cid)==before and api.store.history(cid)==history
    assert api.store.request(cid,'missing') is None
    program='''
import json,sys
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
api=Nat20GameEngine(Store(sys.argv[1]));cid=sys.argv[2]
api.store.restore(cid,sys.argv[3])
restored=api.restore(cid)
result=api.execute(cid,GameCommand(request_id='continued',expected_version=restored['version'],action={'kind':'check','check_id':'perception'}))
print(json.dumps({'before':restored['runtime'],'after':api.store.campaign(cid)['state']['nat20'],'result':result['result']}))
'''
    process=subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(api.store.path),cid,checkpoint],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True,timeout=50,
        env=dict(os.environ,SOLO_TEST='1',SOLO_DATA_DIR=str(tmp_path),PYTHONDONTWRITEBYTECODE='1'))
    continued=json.loads(process.stdout)
    assert continued['before']==saved and continued['after']==saved
    assert continued['result']['kind']=='skill'
    assert continued['after']['definition']['build']['class_slug']=='rogue'
    assert api.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])


def test_nonempty_profile_remains_strict_and_executes(tmp_path):
    profile=encounter()
    for mutation in ('empty-actors','outside-grid','missing-source'):
        invalid=deepcopy(profile)
        if mutation=='empty-actors':invalid['actors']=[]
        elif mutation=='outside-grid':invalid['actors'][0]['position']['x']=79
        else:invalid['actors'][0].pop('attack_bonus_source')
        with pytest.raises(ValidationError):PrototypeEncounter.model_validate(invalid)
    api=Nat20GameEngine(Store(tmp_path/'nonempty.sqlite3'))
    cid=api.create_experimental_campaign('Rogue encounter',build=build('rogue'),encounters={'forest':profile},seed=5)['id']
    run(api,cid,'start',kind='start_combat',encounter_id='forest')
    run(api,cid,'dash',kind='native_action',operation='dash')
    saved=api.store.campaign(cid)['state']['nat20']
    assert set(saved['combat']['view']['encounter_ids'])=={'rat-a','rat-b'}
    assert api.restore(cid)['runtime']==saved

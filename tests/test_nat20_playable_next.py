"""Public Native projection -> ordinary encounter, ability cost, restart."""
from copy import deepcopy
import json
import os
import random
import subprocess
import sys

import pytest

from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import ROOT
from solo.storage import Store
from test_nat20_prototype import build, offline, run


def scene(monster='wolf'):
    return dict(grid=dict(width=10,height=8),hero_position=dict(x=1,y=1,z=0),actors=[
        dict(id='foe',monster=monster,position=dict(x=3,y=1,z=0))])


def test_public_stat_block_projection_and_narrow_unrepresentable_profile():
    state=random.getstate()
    for slug,bonus in [('wolf',4),('rat',2),('goblin-warrior',4)]:
        profile=Nat20GameEngine.monster_attack_profile(slug)
        assert profile['status']=='native_derived' and profile['attack_bonus']==bonus
        assert profile['method']=='native-stat-block-v1'
        assert profile['activities'] and all(a['modifier']==bonus for a in profile['activities'])
    knight=Nat20GameEngine.monster_attack_profile('knight')
    assert knight['status']=='unbound' and 'differing action modifiers' in knight['reason']
    assert random.getstate()==state


def test_catalogue_does_not_project_all_monsters(monkeypatch):
    from solo import nat20_monsters
    def forbidden(*args,**kwargs):raise AssertionError('Catalogue must not calculate creature bindings')
    with monkeypatch.context() as context:
        context.setattr(nat20_monsters,'attack_profile',forbidden)
        assert Nat20GameEngine.catalog('monsters',limit=2)['total']==341


@pytest.mark.parametrize('cls',['monk','wizard'])
def test_no_manual_numbers_ability_cost_save_new_process_and_replay(tmp_path,cls):
    api=Nat20GameEngine(Store(tmp_path/(cls+'.sqlite3')))
    spells=['magic-missile'] if cls=='wizard' else []
    chosen=build(cls,2 if cls=='monk' else 1)
    cid=api.create_experimental_campaign(cls,build=chosen,encounters={'trail':scene()},prepared_spells=spells,seed=5)['id']
    assert api.store.history(cid)==[]  # projection did not create any game event
    actor=api.store.campaign(cid)['state']['nat20']['definition']['encounters']['trail']['actors'][0]
    assert actor['attack_bonus']==4 and actor['attack_bonus_source']['kind']=='native_derived'
    run(api,cid,'start',kind='start_combat',encounter_id='trail')
    view=api.store.campaign(cid)['state']['nat20']['combat']['view']
    if view['initiative'][view['current_turn_index']]['entity_id']!='hero':
        run(api,cid,'initiative',kind='end_turn')
    if cls=='monk':
        result=run(api,cid,'ability',kind='native_action',operation='use_feature',feature_id='monks-focus',activity_id='7xj7b6e8tDznDSrE')
        assert api.store.campaign(cid)['state']['nat20']['hero']['custom_counters']['feature_use:monks-focus']['spent']==1
        state=api.store.campaign(cid)['state']
        assert 'dodging' not in state['hero']['conditions']
        assert any('dodging' in e['statuses'] for e in state['nat20']['active_effects'])
    else:
        result=run(api,cid,'ability',kind='native_action',operation='cast_spell',spell_id='magic-missile',target_id='foe',slot_level=1)
        assert any(e['type']=='spell_cast' for e in result['native_events'])
        assert api.store.campaign(cid)['state']['nat20']['hero']['spell_slots']['1']==1
    checkpoint=api.store.save(cid,'Real native ability')
    saved=deepcopy(api.store.campaign(cid)['state']['nat20'])
    baseline=run(api,cid,'baseline',kind='end_turn')
    after=deepcopy(api.store.campaign(cid)['state']['nat20'])
    if cls=='monk':
        assert any(e['type']=='attack_rolled' and e['attacker_id']=='foe' and e['modifier']==4 for e in baseline['native_events'])
    else:
        assert after['combat']['view']['tracked_hp']['foe']==0
    api.store.restore(cid,checkpoint)
    program='''
import sys,json
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
api=Nat20GameEngine(Store(sys.argv[1]));cid=sys.argv[2]
before=api.restore(cid)
result=api.execute(cid,GameCommand(request_id='continued',expected_version=before['version'],action={'kind':'end_turn'}))
print(json.dumps({'before':before['runtime'],'after':api.store.campaign(cid)['state']['nat20'],'events':result['native_events']}))
'''
    process=subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(api.store.path),cid],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',check=True,timeout=60,
        env=dict(os.environ,SOLO_TEST='1',SOLO_DATA_DIR=str(tmp_path),PYTHONDONTWRITEBYTECODE='1'))
    assert json.loads(process.stdout)==dict(before=saved,after=after,events=baseline['native_events'])
    before=deepcopy(api.store.campaign(cid))
    with pytest.raises(ValueError):
        run(api,cid,'unsupported',kind='native_action',operation='use_feature',feature_id='invented-feature')
    assert api.store.campaign(cid)==before
    assert api.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])


def test_only_unrepresentable_monster_attack_refuses_and_rolls_back(tmp_path):
    api=Nat20GameEngine(Store(tmp_path/'unbound.sqlite3'))
    cid=api.create_experimental_campaign('Rogue',build=build('rogue'),encounters={'trail':scene('knight')},seed=5)['id']
    actor=api.store.campaign(cid)['state']['nat20']['definition']['encounters']['trail']['actors'][0]
    assert 'attack_bonus' not in actor
    run(api,cid,'start',kind='start_combat',encounter_id='trail')
    before=deepcopy(api.store.campaign(cid));history=api.store.history(cid)
    with pytest.raises(ValueError,match='bonus is unbound'):
        run(api,cid,'npc-turn',kind='end_turn')
    assert api.store.campaign(cid)==before and api.store.history(cid)==history


def test_rage_disabled_effect_refuses_only_that_feature(tmp_path):
    api=Nat20GameEngine(Store(tmp_path/'rage.sqlite3'))
    cid=api.create_experimental_campaign('Barbarian',build=build('barbarian'),encounters={'trail':scene()},seed=5)['id']
    run(api,cid,'start',kind='start_combat',encounter_id='trail')
    before=deepcopy(api.store.campaign(cid));history=api.store.history(cid)
    with pytest.raises(ValueError,match='disabled effect'):
        run(api,cid,'rage',kind='native_action',operation='use_feature',feature_id='rage')
    assert api.store.campaign(cid)==before and api.store.history(cid)==history
    run(api,cid,'still-playable',kind='native_action',operation='dash')

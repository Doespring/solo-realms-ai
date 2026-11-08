"""Small source-only prototype-1 smoke; real pinned Nat20, no AI/network."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from pydantic import ValidationError

from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import ROOT
from solo.storage import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):raise AssertionError('No external network in prototype-1')
    monkeypatch.setattr(socket.socket,'connect',forbidden)


def build(cls,level=1):
    # A legal build: Standard Array, the Soldier background and the +2/+1 it
    # grants, the class skills and each feature pool the class owes. The level
    # must reach the classes map too, otherwise the spec is contradictory.
    return legal(class_slug=cls,species_slug='human',level=level,
        equipment=['dagger','potion-of-healing'])


def encounter():
    source=dict(kind='authored_scenario',id='prototype-1-smoke',version='1',note='Trusted authored to-hit input for native EncounterMemberSpec, not computed from a book or supplied by an AI action')
    return dict(grid=dict(width=9,height=6),hero_position=dict(x=1,y=1,z=0),actors=[
        dict(id='rat-a',monster='rat',position=dict(x=2,y=1,z=0),attack_bonus=2,attack_bonus_source=source),
        dict(id='rat-b',monster='rat',position=dict(x=5,y=1,z=0),attack_bonus=2,attack_bonus_source=source)])


def run(api,cid,identity,**action):
    return api.execute(cid,GameCommand(request_id=identity,expected_version=api.store.campaign(cid)['version'],action=action))


def test_full_catalogue_is_searchable_but_not_all_mechanics_claimed():
    api=Nat20GameEngine
    assert api.catalog('classes')['total']==12
    # Counted from the pinned corpus, not frozen: the catalogue total must equal
    # the documents actually shipped, so adding a document cannot leave a stale
    # number behind, and removing one still fails here.
    from solo.nat20_prototype import DATA
    assert api.catalog('spells')['total']==len(list((DATA/'spells').glob('*.json')))
    assert api.catalog('monsters')['total']==341
    # Search runs over slug and display name; the display names are translated, so
    # the stable identity is the slug. Searching by the translated name is covered
    # by the same assertion below, which must find the same document.
    assert api.catalog('spells','magic-missile')['entries'][0]['id']=='spells:magic-missile'
    translated=api.catalog_detail('spells:magic-missile')['name']
    assert api.catalog('spells',translated)['entries'][0]['id']=='spells:magic-missile'
    detail=api.catalog_detail('spells:magic-missile')
    assert detail['rules_version']=='dnd2024' and detail['source']['license_tag']=='CC-BY-4.0'
    assert detail['data']['level']==1 and detail['execution_status']=='native_candidate'
    assert api.catalog_detail('spells:bless')['execution_status']=='blocked'
    assert next(e for e in api.catalog('actions')['entries'] if e['slug']=='hide')['execution_status']=='blocked'
    with pytest.raises(ValueError):api.catalog_detail('spells:../../keys')
    with pytest.raises(ValidationError):GameCommand(request_id='inject',expected_version=0,
        action=dict(kind='native_action',operation='attack',damage=999))


@pytest.mark.parametrize('cls',['fighter','rogue','wizard'])
def test_diverse_native_build_encounter_action_restart(tmp_path,cls):
    api=Nat20GameEngine(Store(tmp_path/(cls+'.sqlite3')))
    prepared=['magic-missile','bless'] if cls=='wizard' else []
    preview=api.preview_build(build(cls),prepared)
    assert preview['build']['classes']=={cls:1} and preview['warnings']
    cid=api.create_experimental_campaign(cls,build=build(cls),prepared_spells=prepared,seed=5,
        encounters={'forest':encounter(),'cellar':encounter()})['id']
    check=run(api,cid,'save',kind='check',check_id='save-dex')
    assert check['result']['kind']=='saving_throw' and check['result']['success'] is None
    assert check['event'].payload['effects'][0]['payload']['kind']=='save'
    run(api,cid,'start',kind='start_combat',encounter_id='forest')
    current=api.store.campaign(cid);caps=api.capabilities(current)
    assert set(caps['targets'])=={'hero','rat-a','rat-b'} and 'cast_spell' in caps['native_operations']
    action=(dict(operation='cast_spell',spell_id='magic-missile',target_ids=['rat-a','rat-b','rat-b'],slot_level=1)
        if cls=='wizard' else dict(operation='attack',weapon_id='dagger',target_id='rat-a')
        if cls=='fighter' else dict(operation='dodge'))
    command=GameCommand(request_id='act',expected_version=current['version'],action=dict(kind='native_action',**action))
    outcome=api.execute(cid,command)
    assert api.execute(cid,command)['journal']==outcome['journal']
    visible=api.visible(outcome['snapshot'])
    assert all(a['resources']=={} for a in visible['actors'] if a['id']!='hero')
    if cls=='wizard':
        events=outcome['native_events']
        assert len([e for e in events if e['type']=='damage_applied'])==3
        assert any(e['type']=='spell_cast' for e in events)
        assert api.store.campaign(cid)['state']['nat20']['hero']['spell_slots']['1']==1
    checkpoint=api.store.save(cid,'prototype-1 active combat')
    before=deepcopy(api.store.campaign(cid)['state']['nat20'])
    program='''
import json,sys
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
api=Nat20GameEngine(Store(sys.argv[1]));cid=sys.argv[2]
before=api.restore(cid)['runtime']
result=api.execute(cid,GameCommand(request_id='continued',expected_version=api.store.campaign(cid)['version'],action={'kind':'end_turn'}))
print(json.dumps({'before':before,'after':api.store.campaign(cid)['state']['nat20'],'events':result['native_events']}))
'''
    baseline=run(api,cid,'baseline',kind='end_turn')
    expected=deepcopy(api.store.campaign(cid)['state']['nat20'])
    api.store.restore(cid,checkpoint)
    process=subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(api.store.path),cid],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',check=True,timeout=60,
        env=dict(os.environ,SOLO_TEST='1',SOLO_DATA_DIR=str(tmp_path),PYTHONDONTWRITEBYTECODE='1'))
    assert json.loads(process.stdout)==dict(before=before,after=expected,events=baseline['native_events'])
    if cls=='wizard':
        run(api,cid,'end',kind='end_combat')
        run(api,cid,'rest',kind='long_rest',food_water=True)
        assert api.store.campaign(cid)['state']['nat20']['hero']['spell_slots']['1']==2
        run(api,cid,'new-scene',kind='start_combat',encounter_id='cellar')
        state=deepcopy(api.store.campaign(cid))
        for identity,values in [('bless',dict(operation='cast_spell',spell_id='bless',target_id='hero',slot_level=1)),
                                ('unowned',dict(operation='attack',weapon_id='greatsword',target_id='rat-a')),
                                ('hide',dict(operation='hide'))]:
            with pytest.raises(ValueError):run(api,cid,identity,kind='native_action',**values)
            assert api.store.campaign(cid)==state
    assert api.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])


def test_atomicity_and_old_fighter_release_survive(tmp_path,monkeypatch):
    from solo.contracts import Hero
    api=Nat20GameEngine(Store(tmp_path/'atomic.sqlite3'))
    cid=api.create_experimental_campaign('Rogue',build=build('rogue'),seed=5,encounters={'forest':encounter()})['id']
    run(api,cid,'start',kind='start_combat',encounter_id='forest')
    before=deepcopy(api.store.campaign(cid));history=api.store.history(cid)
    def fail(*args,**kwargs):raise RuntimeError('prototype-1 memory fault')
    with monkeypatch.context() as fault:
        fault.setattr(api.store.memory,'ingest',fail)
        with pytest.raises(RuntimeError,match='prototype-1'):
            run(api,cid,'dash',kind='native_action',operation='dash')
    assert api.store.campaign(cid)==before and api.store.history(cid)==history
    run(api,cid,'dash',kind='native_action',operation='dash')
    with pytest.raises(ValueError,match='Stale'):
        api.execute(cid,GameCommand(request_id='stale',expected_version=0,action=dict(kind='native_action',operation='dash')))
    fixture=json.loads((Path(__file__).parent/'fixtures/nat20-preview7-fighter-combat.json').read_text(encoding='utf-8'))['campaign']
    old=api.store.create('Old fighter',Hero.model_validate(fixture['state']['hero']),initial_state=fixture['state'])['id']
    # Preview 7's release is not this tree's release: the digest names exact bytes.
    # The old campaign therefore keeps its own digest and is refused rather than
    # silently replayed on the current corpus (docs/RELEASE_IDENTITY_DECISION.md).
    with pytest.raises(ValueError,match='release changed'):
        api.restore(old)
    with pytest.raises(ValueError,match='release changed'):
        run(api,old,'old-turn',kind='end_turn')
    stored=api.store.campaign(old)
    assert stored['state']['nat20']['release_digest']=='374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75'
    # The refusal changed nothing: the campaign is still the untouched copy.
    assert stored['version']==0 and stored['state']['nat20']['combat']==fixture['state']['nat20']['combat']


def test_equipped_consumable_and_wolf_policy_use_native_resources(tmp_path):
    api=Nat20GameEngine(Store(tmp_path/'item.sqlite3'))
    scene=encounter();scene['actors'][1].update(monster='wolf',attack_bonus=4)
    cid=api.create_experimental_campaign('Supplies',build=build('fighter'),seed=5,encounters={'wilds':scene})['id']
    run(api,cid,'start',kind='start_combat',encounter_id='wilds')
    view=api.store.campaign(cid)['state']['nat20']['combat']['view']
    if view['initiative'][view['current_turn_index']]['entity_id']!='hero':run(api,cid,'initiative',kind='end_turn')
    result=run(api,cid,'drink',kind='native_action',operation='use_item',item_id='potion-of-healing',target_id='hero')
    assert any(e['type']=='healing_applied' for e in result['native_events'])
    counters=api.store.campaign(cid)['state']['nat20']['hero']['custom_counters']
    assert any('potion-of-healing' in k and v['spent']==1 for k,v in counters.items())
    before=deepcopy(api.store.campaign(cid))
    with pytest.raises(ValueError):run(api,cid,'drink-again',kind='native_action',operation='use_item',item_id='potion-of-healing',target_id='hero')
    assert api.store.campaign(cid)==before
    result=run(api,cid,'npc-policy',kind='end_turn')
    assert any(e['type']=='attack_rolled' and e['attacker_id']=='rat-b' and e['modifier']==4 for e in result['native_events'])
    assert api.restore(cid)['runtime']==api.store.campaign(cid)['state']['nat20']
    unbound=encounter()
    for actor in unbound['actors']:
        actor.pop('attack_bonus');actor.pop('attack_bonus_source')
        actor['monster']='knight'  # differing action modifiers cannot fit Native's scalar
    other=api.create_experimental_campaign('Unbound',build=build('fighter'),seed=5,encounters={'wilds':unbound})['id']
    run(api,other,'start',kind='start_combat',encounter_id='wilds')
    before=deepcopy(api.store.campaign(other))
    with pytest.raises(ValueError,match='bonus is unbound'):
        run(api,other,'refused-npc',kind='end_turn')
    assert api.store.campaign(other)==before

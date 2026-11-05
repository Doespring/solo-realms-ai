"""Stage 2 targeted source-only mechanics; all campaigns disposable."""
import asyncio
from copy import deepcopy
import json
import os
import random
import socket
import subprocess
import sys

import pytest
from dnd5e_engine import (ActiveEffect,CheckSpec,EncounterMemberSpec,GridScene,PartyMemberSpec,
    PlayerIntent,configure_lib_loader,drain_pending_events,get_live,resolve_check,start_combat,submit_player_intent)

from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import ROOT,release
from solo.nat20_worker import execute,plain
from solo.storage import Store
from test_nat20_prototype import build,run


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect=socket.socket.connect
    def only_loopback(sock,address):
        if address[0] in ('127.0.0.1','::1'):return connect(sock,address)
        raise AssertionError('No external APIs in stage2')
    monkeypatch.setattr(socket.socket,'connect',only_loopback)


def test_standalone_poisoned_advantage_cancels_effect_bonus_and_exhaustion(tmp_path):
    api=Nat20GameEngine(Store(tmp_path/'checks.sqlite3'))
    cid=api.create_experimental_campaign('Checks',build=build('rogue'),encounters={},seed=5)['id']
    state=api.store.campaign(cid)['state']['nat20']
    state['active_effects']=[ActiveEffect(id='poison',name='Poison',origin='trusted:test',target_id='hero',statuses={'poisoned'}).model_dump(mode='json'),
        ActiveEffect(id='bonus',name='Check bonus',origin='trusted:test',target_id='hero',changes=[dict(key='check.bonus',mode='add',value='1d4')]).model_dump(mode='json'),
        ActiveEffect(id='disabled',name='Disabled',origin='trusted:test',target_id='hero',disabled=True,
            changes=[dict(key='check.bonus',mode='add',value=100)]).model_dump(mode='json')]
    state['exhaustion']=2
    projected=api.store.campaign(cid);projected['state']['nat20']=state
    assert api.snapshot(projected).actors[0].resources['exhaustion'].current==2
    from solo.nat20_checks import resolve_state_check
    args=dict(kind='skill',skill='athletics',ability=None,dc=15,advantage=True,disadvantage=False)
    result=plain(resolve_state_check(state,**args,rng=random.Random(7)))
    # Independent call of the existing Native public primitives, no Solo math.
    from dnd5e_engine.rules.conditions import d20_test_penalty
    from dnd5e_engine.types.conditions import ActiveCondition
    penalty=d20_test_penalty([ActiveCondition(condition='exhaustion',source_entity_id='implied:exhaustion',scope='session',exhaustion_level=2)])
    effects=[ActiveEffect.model_validate(state['active_effects'][1]),ActiveEffect(id='expected',name='Penalty',origin='test',target_id='hero',changes=[dict(key='check.bonus',mode='add',value=penalty)])]
    sheet=state['sheet']
    expected=plain(resolve_check(CheckSpec(kind='skill',skill='athletics',dc=15,ability_scores=sheet['ability_scores'],
        proficient_skills=tuple(sheet['skill_proficiencies']),proficient_saves=tuple(sheet['save_proficiencies']),
        proficiency_bonus=sheet['proficiency_bonus'],expertise_skills=tuple(sheet['skill_expertise']),
        advantage=True,disadvantage=True,active_effects=tuple(effects),rng=random.Random(7))))
    assert result==expected and result['effect_breakdown']
    command=GameCommand(request_id='effects',expected_version=0,action=dict(kind='check',check_id='athletics'))
    committed=asyncio.run(execute(dict(mode='execute',state=state,command=command.model_dump(mode='json'),session_id='checks')))
    assert committed['state']==state and committed['result']['effect_breakdown']


def test_native_automatic_failure_does_not_draw_or_fabricate_die(tmp_path):
    from solo.nat20_checks import resolve_state_check
    api=Nat20GameEngine(Store(tmp_path/'auto.sqlite3'))
    cid=api.create_experimental_campaign('Auto',build=build('rogue'),encounters={},seed=5)['id']
    state=api.store.campaign(cid)['state']['nat20']
    state['active_effects']=[ActiveEffect(id='paralyzed',name='Paralyzed',origin='trusted:test',target_id='hero',statuses={'paralyzed'}).model_dump(mode='json')]
    rng=random.Random(11);before=rng.getstate()
    result=resolve_state_check(state,kind='saving_throw',skill=None,ability='dex',dc=12,advantage=False,disadvantage=False,rng=rng)
    assert result['success'] is False and result['natural_roll'] is None and rng.getstate()==before


def test_native_hide_defect_proof_and_adapter_refusal(tmp_path):
    api=Nat20GameEngine(Store(tmp_path/'hide.sqlite3'))
    cid=api.create_experimental_campaign('Rogue',build=build('rogue'),encounters={},seed=5)['id']
    state=api.store.campaign(cid)['state']['nat20']
    async def probe():
        from solo.nat20_prototype import RELEASE
        configure_lib_loader(release(RELEASE)[0])
        party=PartyMemberSpec.model_validate(dict(state['hero'],initiative=20,zone_id='1,1'))
        opened=await start_combat(session_id='hide-proof',rng_seed=5,party=[party],encounter=[EncounterMemberSpec(
            entity_id='foe',entity_type='Monster',name='Foe',initiative=1,hp_current=10,hp_max=10,zone_id='4,1')],
            grid_scene=GridScene(width=6,height=5,cover_cells={'1,1':'total'}))
        handle=opened.handle;drain_pending_events(handle)
        await submit_player_intent(handle,'hero',PlayerIntent(intent_type='hide'))
        view=get_live(handle);hero=next(a for a in view.initiative if a.entity_id=='hero')
        assert hero.action_available and view.initiative[view.current_turn_index].entity_id=='hero'
        assert hero.hide_attempted_this_turn
        return plain(drain_pending_events(handle))
    events=asyncio.run(probe())
    assert events  # proof of the broken free attempt, NOT certification
    assert Nat20GameEngine.catalog_detail('actions:hide')['execution_status']=='blocked'


def test_zero_hp_native_death_saves_memory_undo_save_new_process(tmp_path,monkeypatch):
    api=Nat20GameEngine(Store(tmp_path/'death.sqlite3'))
    cid=api.create_campaign('Fighter',seed=5,hp_current=1)['id']
    run(api,cid,'start',kind='start_combat',encounter_id='basic')
    for i in range(6):
        reply=run(api,cid,'turn-'+str(i),kind='end_turn')
        if api.store.campaign(cid)['state']['hero']['hp']==0:break
    else:raise AssertionError('Native fixture did not reach zero HP')
    assert any(e['type']=='death_save_rolled' for e in reply['native_events'])
    actor=next(a for a in reply['snapshot'].actors if a.id=='hero')
    assert actor.resources['death_save:successes'].current==1
    from solo.nat20_screen import Nat20Screen
    screen=Nat20Screen().project(api.store.campaign(cid))
    assert screen['terminal'] is None
    assert {c['parameters']['kind'] for c in screen['commands']}=={'end_turn'}
    before=deepcopy(api.store.campaign(cid));history=api.store.history(cid)
    with pytest.raises(ValueError,match='0 HP'):
        run(api,cid,'no-attack',kind='attack',target='goblin')
    assert api.store.campaign(cid)==before
    def fail(*args,**kwargs):raise RuntimeError('death memory rollback')
    with monkeypatch.context() as fault:
        fault.setattr(api.store.memory,'ingest',fail)
        with pytest.raises(RuntimeError,match='rollback'):run(api,cid,'next',kind='end_turn')
    assert api.store.campaign(cid)==before and api.store.history(cid)==history
    checkpoint=api.store.save(cid,'Dying')
    saved=deepcopy(api.restore(cid)['runtime'])
    body=GameCommand(request_id='next',expected_version=api.store.campaign(cid)['version'],action=dict(kind='end_turn'))
    baseline=api.execute(cid,body)
    assert api.execute(cid,body)['journal']==baseline['journal']
    after=deepcopy(api.store.campaign(cid)['state']['nat20'])
    api.store.undo(cid);assert api.restore(cid)['runtime']==saved
    api.store.restore(cid,checkpoint)
    program='''
import json,sys
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
api=Nat20GameEngine(Store(sys.argv[1]));cid=sys.argv[2]
r=api.restore(cid)
result=api.execute(cid,GameCommand(request_id='continued',expected_version=r['version'],action={'kind':'end_turn'}))
print(json.dumps({'before':r['runtime'],'after':api.store.campaign(cid)['state']['nat20'],'events':result['native_events']}))
'''
    process=subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(api.store.path),cid],cwd=ROOT,
        text=True,encoding='utf-8',capture_output=True,check=True,timeout=50,
        env=dict(os.environ,SOLO_TEST='1',SOLO_DATA_DIR=str(tmp_path),PYTHONDONTWRITEBYTECODE='1'))
    assert json.loads(process.stdout)==dict(before=saved,after=after,events=baseline['native_events'])
    for i in range(12):
        view=api.store.campaign(cid)['state']['nat20']['combat']['view']
        if 'hero' in view['dead_ids']:break
        run(api,cid,'dying-'+str(i),kind='end_turn')
    else:raise AssertionError('Bounded Native dying sequence did not end')
    closed=run(api,cid,'close-defeat',kind='end_combat')
    assert closed['result']['outcome']['ended_reason']=='defeat_tpk'
    assert api.store.campaign(cid)['state']['nat20']['experience']==0
    assert api.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])


def test_native_death_save_critical_recovery_stability_and_death():
    from dnd5e_engine.death_saves import roll_death_save
    from dnd5e_engine.types.combat import Combatant
    from dnd5e_engine.types.conditions import ActiveCondition
    actor=Combatant(entity_id='hero',entity_type='Character',name='Dying',initiative=1,hp_current=0,hp_max=12,
        conditions=[ActiveCondition(condition='unconscious',source_entity_id='implied:unconscious',scope='combat')])
    recovered=roll_death_save(actor,random.Random(5))
    assert recovered.outcome=='critical_success' and recovered.combatant.hp_current==1
    assert all(c.condition!='unconscious' for c in recovered.combatant.conditions)
    stable=roll_death_save(actor.model_copy(update={'death_saves':{'successes':2,'failures':0,'is_stable':False}}),random.Random(0))
    assert stable.outcome=='stabilized' and stable.combatant.death_saves['is_stable']
    dead=roll_death_save(actor.model_copy(update={'death_saves':{'successes':0,'failures':2,'is_stable':False}}),random.Random(2))
    assert dead.outcome=='dead' and not dead.combatant.is_alive


def test_spell_catalogue_combat_payload_status_is_data_driven(tmp_path):
    from solo.nat20_prototype import DATA,spell_execution_status
    counts={}
    for path in (DATA/'spells').glob('*.json'):
        status=spell_execution_status(json.loads(path.read_text(encoding='utf-8')))
        counts[status]=counts.get(status,0)+1
    # Every spell document on disk gets exactly one status; the count must match
    # the catalogue rather than a frozen number that has to be edited every time
    # the corpus gains a document.
    assert sum(counts.values())==len(list((DATA/'spells').glob('*.json')))
    assert sum(counts.values())==Nat20GameEngine.catalog('spells',limit=1)['total']
    assert counts['narrative_or_unsupported_in_combat']>0
    assert Nat20GameEngine.catalog_detail('spells:magic-missile')['execution_status']=='native_candidate'
    rows=Nat20GameEngine.catalog('spells',limit=200)['entries']
    unsupported=next(r['slug'] for r in rows if r['execution_status']=='narrative_or_unsupported_in_combat')
    api=Nat20GameEngine(Store(tmp_path/'spells.sqlite3'))
    scene=dict(grid=dict(width=6,height=5),hero_position=dict(x=1,y=1,z=0),actors=[dict(id='rat',monster='rat',position=dict(x=4,y=1,z=0))])
    cid=api.create_experimental_campaign('Wizard',build=build('wizard'),prepared_spells=[unsupported,'magic-missile','bless'],encounters={'scene':scene},seed=5)['id']
    run(api,cid,'start',kind='start_combat',encounter_id='scene');before=deepcopy(api.store.campaign(cid))
    for id,spell in [('unsupported',unsupported),('bless','bless')]:
        with pytest.raises(ValueError):run(api,cid,id,kind='native_action',operation='cast_spell',spell_id=spell,target_id='hero',slot_level=1)
        assert api.store.campaign(cid)==before
    caps=api.capabilities(before)
    assert caps['spell_execution'][unsupported]=='narrative_or_unsupported_in_combat'


def test_goblin_advantage_is_resolved_by_native_not_adapter_crash(tmp_path):
    api=Nat20GameEngine(Store(tmp_path/'advantage.sqlite3'))
    cid=api.create_campaign('Prone fixture',seed=5)['id']
    state=api.store.campaign(cid)['state']['nat20']
    state['active_effects']=[ActiveEffect(id='prone',name='Prone',origin='trusted:test',target_id='hero',statuses={'prone'}).model_dump(mode='json')]
    def command(identity,**action):
        return GameCommand(request_id=identity,expected_version=0,action=action).model_dump(mode='json')
    response=asyncio.run(execute(dict(mode='execute',state=state,session_id='goblin-adv',command=command('start',kind='start_combat',encounter_id='basic'))))
    for n in range(5):
        response=asyncio.run(execute(dict(mode='execute',state=response['state'],session_id='goblin-adv',command=command('next-'+str(n),kind='end_turn'))))
        attacks=[e for e in response['events'] if e['type']=='attack_rolled' and e['attacker_id']=='goblin']
        if any(e['advantage']=='advantage' for e in attacks):break
    else:raise AssertionError('Native Goblin did not attack at advantage')
    assert response['state']['combat']['commands']

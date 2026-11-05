"""Pending stand: successful seams and explicit upstream gap reproductions."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from dnd5e_engine import (ActiveEffect, CheckSpec, EncounterMemberSpec, GridScene, HitDicePool,
    PartyMemberSpec, PlayerIntent, advance_monster_turn, configure_lib_loader, drain_pending_events,
    get_actor_active_effects, get_live, resolve_check, resolve_long_rest, resolve_short_rest,
    start_combat, submit_player_intent)

from solo.app import create_app
from solo.command_engine import CommandEngine
from solo.contracts import Hero
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_bless import ROOT, PROFILE, RELEASE, BONUS_KEYS, normalize_bless
from solo.nat20_campaign import load_definition
from solo.nat20_content import release
from solo.storage import Store

PROJECT = ROOT.parents[3]
CAST = dict(kind='cast_spell', spell_id='bless', target='hero')


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    monkeypatch.setenv('SOLO_NAT20_BLESS_STAND', '1')
    connect = socket.socket.connect
    def local(sock, address):
        if address[0] in ('127.0.0.1', '::1'): return connect(sock, address)
        raise AssertionError('External calls forbidden')
    monkeypatch.setattr(socket.socket, 'connect', local)


@pytest.fixture
def game(tmp_path):
    api = Nat20GameEngine(Store(tmp_path/'bless.sqlite3'))
    return api, api.create_campaign('Pending caster', definition_id=PROFILE, seed=5)['id']


def run(api, cid, identity, **action):
    return api.execute(cid, GameCommand(request_id=identity, expected_version=api.store.campaign(cid)['version'], action=action))


def test_pending_release_isolated_from_preview7_and_production(game, monkeypatch):
    api, cid = game
    fixture = json.loads((PROJECT/'tests/fixtures/nat20-preview7-fighter-combat.json').read_text(encoding='utf-8'))
    # Preview 7's own digest stays untouched, as a record of the bytes that
    # campaign was played on. The live corpus has since been re-released, so the
    # current base digest is a different, well-formed value -- not the old one.
    assert fixture['release_digest'] == '374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75'
    live = release()[1]
    assert live != fixture['release_digest'] and len(live) == 64
    assert int(live, 16) is not None  # hexadecimal
    assert release()[0].get_spell('bless') is None
    assert release(RELEASE)[1] != release()[1]
    old = fixture['campaign']
    saved = api.store.create('Old fighter', Hero.model_validate(old['state']['hero']), initial_state=old['state'])['id']
    assert api.restore(saved)['runtime'] == old['state']['nat20']
    run(api, saved, 'continue-preview7', kind='end_turn')
    assert api.store.campaign(saved)['state']['nat20']['release_digest'] == fixture['release_digest']
    assert 'effects' not in old['state']['engine_snapshot']
    from solo.engine_memory_contracts import GameSnapshot
    assert GameSnapshot.model_validate(old['state']['engine_snapshot']).model_dump(mode='json') == old['state']['engine_snapshot']
    with pytest.raises(ValueError, match='not registered'): run(api, saved, 'no-magic', **CAST)
    before = api.store.campaign(cid)
    monkeypatch.delenv('SOLO_NAT20_BLESS_STAND')
    with pytest.raises(ValueError, match='activation pending'): load_definition(PROFILE)
    with pytest.raises(ValueError, match='activation pending'): api.restore(cid)
    assert api.store.campaign(cid) == before


def test_normalization_and_closed_command_fail_closed(game):
    api, cid = game
    raw = json.loads((ROOT/'raw/_source/spells24/bless.yml').read_text(encoding='utf-8'))
    projected = normalize_bless(raw)
    assert {c['key'] for c in projected['effects'][0]['changes']} == BONUS_KEYS
    assert 'changes' not in raw['effects'][0]
    for mutation in ('empty', 'duplicate', 'formula', 'mode', 'edition'):
        bad = deepcopy(raw)
        if mutation == 'empty': bad['effects'][0]['system']['changes'] = []
        elif mutation == 'duplicate': bad['effects'][0]['system']['changes'][0] = bad['effects'][0]['system']['changes'][1]
        elif mutation == 'formula': bad['effects'][0]['system']['changes'][0]['value'] = 'eval(1)'
        elif mutation == 'mode': bad['effects'][0]['system']['changes'][0]['type'] = 'custom'
        else: bad['system']['source']['rules'] = '2014'
        with pytest.raises(ValueError): normalize_bless(bad)
    for action in [dict(CAST, target='road-raider'), dict(CAST, slot_level=2), dict(CAST, amount=999),
                   dict(CAST, components_confirmed=True), dict(CAST, spell_id='fireball')]:
        with pytest.raises(ValueError): GameCommand(request_id='no', expected_version=0, action=action)
    state = api.restore(cid)['runtime']
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    bad = deepcopy(api.restore(cid)['runtime']); bad['hero']['equipment'] = ['mace']
    with pytest.raises(ValueError, match='components/loadout'):
        api._worker(dict(mode='execute', state=bad, session_id=cid,
            command=dict(request_id='missing-component', expected_version=1, action=CAST)))
    before = deepcopy(api.store.campaign(cid))
    with pytest.raises(ValueError, match='No active concentration'):
        run(api, cid, 'empty-drop', kind='drop_concentration')
    assert api.store.campaign(cid) == before
    assert state['hero']['hp_current'] == state['hero']['hp_max'] == 10
    assert 'feature_use:second-wind' not in state['hero']['custom_counters']
    from rules_data.testing.schema_validation import _validate
    schema = PROJECT/'integration/nat20/playable/campaign.schema.json'
    assert _validate(json.dumps(load_definition(PROFILE).model_dump(mode='json')),str(schema),schema.read_bytes())


def test_http_cast_slot_memory_acl_new_process_replay_undo_and_atomicity(game, monkeypatch):
    api, cid = game
    llm = CommandMockLLM({'bless': dict(status='command', action=CAST, message='')})
    app = create_app(api.store.path, llm)
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    before = deepcopy(api.store.campaign(cid))
    with TestClient(app, raise_server_exceptions=False) as client:
        body = dict(request_id='cast', text='bless', expected_version=before['version'])
        with monkeypatch.context() as fault:
            def fail(*args, **kwargs): raise RuntimeError('Injected effect memory failure')
            fault.setattr(app.state.store.memory, 'put', fail)
            assert client.post(f'/api/campaigns/{cid}/command-turn', json=body).status_code == 500
        assert api.store.campaign(cid) == before
        result = client.post(f'/api/campaigns/{cid}/command-turn', json=body).json()
        assert result['status'] == 'resolved', result
        snapshot = result['snapshot']
        hero = next(a for a in snapshot['actors'] if a['id'] == 'hero')
        assert hero['resources']['spell_slot:1'] == hero['resources']['concentration'] == 1
        assert snapshot['effects'][0]['name'] == 'Blessed' and hero['conditions'] == []
        assert client.post(f'/api/campaigns/{cid}/command-turn', json=body).json()['snapshot'] == snapshot
        stale = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(body, request_id='stale')).json()
        assert stale['status'] == 'state_version_error'
    context = app.state.game.compiler.engine_context(cid, limit=30, budget=15000)
    assert context['mechanics']['effects']
    assert any(f['kind'] == 'effect' for f in context['memories'])
    other = app.state.game.compiler.engine_context(cid, npc_id='road-raider', limit=30, budget=15000)
    assert other['mechanics']['effects'] == [] and not any(f['kind'] == 'effect' for f in other['memories'])
    checkpoint = api.store.save(cid, 'Active Bless')
    before_restart = api.restore(cid)['runtime']
    program = '''import json,sys
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store
from solo.game_commands import GameCommand
api=Nat20GameEngine(Store(sys.argv[1]));cid=sys.argv[2]
before=api.restore(cid)
r=api.execute(cid,GameCommand(request_id='restart-turn',expected_version=before['version'],action={'kind':'end_turn'}))
print(json.dumps({'before':before['runtime'],'after':api.restore(cid)['runtime']}))
'''
    process = subprocess.run([sys.executable,'-X','utf8','-B','-c',program,str(api.store.path),cid],
        cwd=PROJECT,capture_output=True,text=True,encoding='utf-8',timeout=60,env=dict(os.environ))
    assert process.returncode == 0, process.stderr
    resumed = json.loads(process.stdout)
    assert resumed['before'] == before_restart
    assert resumed['after']['hero']['spell_slots'] == {'1': 1}
    api.store.undo(cid)
    assert api.restore(cid)['runtime'] == before_restart
    api.store.restore(cid, checkpoint)
    run(api, cid, 'next-turn', kind='end_turn')
    prior = deepcopy(api.store.campaign(cid))
    with pytest.raises(ValueError, match='recast disabled'): run(api,cid,'unsafe-recast',**CAST)
    assert api.store.campaign(cid) == prior
    first_drop = run(api, cid, 'first-drop', kind='drop_concentration')
    assert first_drop['snapshot'].effects == []
    replacement = run(api, cid, 'cast-after-drop', **CAST)
    assert len(api.restore(cid)['runtime']['combat']['view']['concentration_chain']['hero']) == 1
    run(api, cid, 'next-turn-2', kind='end_turn')
    drop = run(api, cid, 'drop', kind='drop_concentration')
    assert drop['snapshot'].effects == []
    actor = next(a for a in drop['snapshot'].actors if a.id=='hero')
    assert actor.resources['spell_slot:1'].current == actor.resources['concentration'].current == 0
    assert actor.resources['action_available'].current == 1
    prior = deepcopy(api.store.campaign(cid))
    with pytest.raises(ValueError, match='no_slot'): run(api, cid, 'exhausted', **CAST)
    assert api.store.campaign(cid) == prior
    api.store.undo(cid)
    assert api.restore(cid)['runtime']['active_effects']
    assert api.store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])


async def arena(state, *, bless=True, frozen=True, seed=7):
    loader, _, _ = release(RELEASE)
    configure_lib_loader(loader)
    party = PartyMemberSpec.model_validate(dict(state['hero'], initiative=20, zone_id='1,1'))
    foe = EncounterMemberSpec(entity_id='foe', entity_type='Monster', name='Fixed mechanical fixture',
        initiative=1, hp_current=10, hp_max=10, ac=15, attack_bonus=4,
        damage_dice='1d6',damage_type='slashing', zone_id='2,1', base_speed=0)
    effects = [ActiveEffect(id='fixture',name='Incapacitated fixture',origin='trusted:test',target_id='foe',statuses={'incapacitated'})] if frozen else []
    started = await start_combat(session_id=f'bless-{bless}-{frozen}-{seed}',party=[party],encounter=[foe],grid_scene=GridScene(width=5,height=5),rng_seed=seed,active_effects=effects)
    handle = started.handle
    drain_pending_events(handle)
    await submit_player_intent(handle,'hero',PlayerIntent(intent_type='cast_spell',spell_id='bless',target_id='hero',slot_level=1) if bless else PlayerIntent(intent_type='pass'))
    drain_pending_events(handle)
    return handle


def test_live_bless_attack_bonus_is_one_d4_not_four_copies(game):
    api,cid=game; state=api.restore(cid)['runtime']
    async def attack(bless):
        handle=await arena(state,bless=bless)
        await advance_monster_turn(handle)
        drain_pending_events(handle)
        await submit_player_intent(handle,'hero',PlayerIntent(intent_type='attack',weapon_id='mace',target_id='foe'))
        return next(e for e in drain_pending_events(handle) if e.type=='attack_rolled')
    normal=asyncio.run(attack(False)); blessed=asyncio.run(attack(True))
    assert normal.natural == blessed.natural and normal.modifier == blessed.modifier
    assert normal.roll_total == normal.natural + normal.modifier
    assert 1 <= blessed.roll_total - blessed.natural - blessed.modifier <= 4


def test_native_duration_drop_and_slot_rest_paths(game):
    api,cid=game;state=api.restore(cid)['runtime']
    async def expire():
        handle=await arena(state)
        assert get_live(handle).concentration_chain
        for _ in range(9):
            await advance_monster_turn(handle)
            await submit_player_intent(handle,'hero',PlayerIntent(intent_type='pass'))
        events=drain_pending_events(handle)
        return handle,events
    handle,events=asyncio.run(expire())
    assert not get_live(handle).concentration_chain
    assert any(e.type=='effect_expired' and e.reason=='duration' for e in events)
    pool=HitDicePool(**state['hit_dice'])
    assert resolve_short_rest(pool,0,2,rng=random.Random(1)).spell_slots is None
    long=resolve_long_rest(pool,10,10,spell_slots={1:0},spell_slot_max={1:2})
    assert long.spell_slots == {1:2}


def test_gap_native_concentration_save_omits_bless_bonus(game):
    """Passing this reproducer is a blocker, NOT successful rule certification."""
    api,cid=game; state=api.restore(cid)['runtime']
    async def checks():
        for seed in range(8):
            handle=await arena(state,frozen=False,seed=seed)
            await advance_monster_turn(handle)
            found=[e for e in drain_pending_events(handle) if e.type=='concentration_check']
            if found: return found[0]
        raise AssertionError('No concentration damage check in bounded fixture')
    check=asyncio.run(checks())
    # Correct Bless requires a nonzero extra d4 on this saving throw. Upstream
    # bypasses its effect-aware save primitive; no host re-roll/fallback is added.
    assert check.roll_total == check.natural + check.modifier


def test_gap_standalone_save_does_not_alias_foundry_bless_keys(game):
    api,cid=game; state=api.restore(cid)['runtime']
    handle=asyncio.run(arena(state))
    effects=tuple(get_actor_active_effects(handle,'hero'))
    args=dict(kind='saving_throw', ability='con', ability_scores=state['sheet']['ability_scores'],
        proficient_skills=(),proficient_saves=(),proficiency_bonus=2,dc=10)
    base=resolve_check(CheckSpec(**args,rng=random.Random(7)))
    blessed=resolve_check(CheckSpec(**args,rng=random.Random(7),active_effects=effects))
    assert effects and base.roll_total == blessed.roll_total


def test_gap_same_identity_recast_retains_a_duplicate_after_drop(game):
    api,cid=game; state=api.restore(cid)['runtime']
    async def duplicate():
        handle=await arena(state)
        await advance_monster_turn(handle)
        await submit_player_intent(handle,'hero',PlayerIntent(intent_type='cast_spell',spell_id='bless',target_id='hero',slot_level=1))
        assert len(get_actor_active_effects(handle,'hero')) == 2
        await advance_monster_turn(handle)
        await submit_player_intent(handle,'hero',PlayerIntent(intent_type='drop_concentration'))
        return handle
    handle=asyncio.run(duplicate())
    assert get_live(handle).concentration_chain == {}
    assert len(get_actor_active_effects(handle,'hero')) == 1  # reproduced upstream blocker


def test_time_boundary_cannot_silently_discard_surviving_bless(game):
    api,cid=game
    run(api,cid,'start',kind='start_combat',encounter_id='basic');run(api,cid,'cast',**CAST)
    before=deepcopy(api.store.campaign(cid))
    with pytest.raises(ValueError,match='clock is pending'):run(api,cid,'close',kind='end_combat')
    assert api.store.campaign(cid)==before

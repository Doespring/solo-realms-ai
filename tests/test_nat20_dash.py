"""Dash on the unchanged pinned runtime; fresh campaigns, no external AI."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from solo.app import create_app
from solo.command_engine import CommandEngine, StateVersionError
from solo.contracts import Hero
from solo.game_commands import GameCommand, tool_schemas
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import ROOT, release
from solo.storage import Store
from test_nat20_command_cycle import offline


# The corpus release identity. The digest is over the engine's .py runtime and
# the content manifest -- NOT over the document bodies, so a translated .json
# does not move it. It moved here because engine patches landed in
# activities/{context,damage,dice,formula}.py and loader.py. The value is
# stably computed (checked below), so a mismatch means the corpus moved, not
# that release() lies.
FINGERPRINT = '33359fe3538b96bdb1bbde77b5d1d6aa8769d2fe2a2c65f66eb282de5a4bf394'


def test_the_pinned_fingerprint_is_the_one_the_corpus_really_produces():
    """The constant above must be the value release() computes, not a guess.

    Without this, the pin could silently describe no corpus at all and the
    assertions that compare against it would keep passing.
    """
    from solo.nat20_content import release
    assert release()[1] == FINGERPRINT


@pytest.fixture
def fighter(tmp_path):
    store = Store(tmp_path / 'dash.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_campaign('Ordinary fighter', definition_id='fighter-standard-array-2024-v1', seed=5)['id']
    return api, cid


def command(api, cid, identity, **action):
    return GameCommand(request_id=identity, expected_version=api.store.campaign(cid)['version'], action=action)


def run(api, cid, identity, **action):
    return api.execute(cid, command(api, cid, identity, **action))


def hero(api, cid):
    view = api.store.campaign(cid)['state']['nat20']['combat']['view']
    return next(a for a in view['initiative'] if a['entity_id'] == 'hero')


def test_public_upstream_dash_without_solo_arithmetic(fighter):
    from dnd5e_engine import IntentRejectedError, PlayerIntent, drain_pending_events, submit_player_intent
    from solo.nat20_worker import plain, read_combat, replay
    api, cid = fighter
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    state = api.store.campaign(cid)['state']['nat20']
    async def probe():
        from dnd5e_engine import configure_lib_loader
        loader, fingerprint, _ = release()
        assert fingerprint == FINGERPRINT
        configure_lib_loader(loader)
        handle = await replay(state['combat'])
        await submit_player_intent(handle, 'hero', PlayerIntent(intent_type='dash'))
        before_repeat = read_combat(handle)
        actor = next(a for a in before_repeat['view']['initiative'] if a['entity_id'] == 'hero')
        assert actor['movement_remaining'] == 60 and not actor['action_available']
        assert plain(drain_pending_events(handle)) == [dict(type='dash_taken', actor_id='hero',
            doubled_movement_remaining=60, budget_consumed='action')]
        with pytest.raises(IntentRejectedError):
            await submit_player_intent(handle, 'hero', PlayerIntent(intent_type='dash'))
        assert read_combat(handle) == before_repeat and not drain_pending_events(handle)
    asyncio.run(probe())


def test_movement_repeat_stale_save_new_process_continue(fighter):
    api, cid = fighter
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    run(api, cid, 'before-dash', kind='move', position=dict(x=1, y=4, z=0))
    assert hero(api, cid)['movement_remaining'] == 15
    body = command(api, cid, 'dash', kind='dash')
    dashed = api.execute(cid, body)
    assert hero(api, cid)['movement_remaining'] == 45 and not hero(api, cid)['action_available']
    assert dashed['event'].payload['command'] == 'dash'
    assert {e['payload']['resource'] for e in dashed['event'].payload['effects'] if e['type'] == 'resource'} >= {'movement_remaining', 'action_available'}
    unchanged = deepcopy(api.store.campaign(cid))
    assert api.execute(cid, body)['journal'] == dashed['journal']
    with pytest.raises(ValueError):
        run(api, cid, 'dash-again', kind='dash')
    with pytest.raises(ValueError):
        run(api, cid, 'attack-after-dash', kind='attack', target='road-raider')
    with pytest.raises(StateVersionError):
        api.execute(cid, GameCommand(request_id='stale', expected_version=0, action=dict(kind='dash')))
    with pytest.raises(ValueError, match='request_id'):
        api.execute(cid, GameCommand(request_id='dash', expected_version=body.expected_version, action=dict(kind='end_turn')))
    assert api.store.campaign(cid) == unchanged
    run(api, cid, 'after-dash', kind='move', position=dict(x=1, y=0, z=0))
    assert hero(api, cid)['movement_remaining'] == 25
    run(api, cid, 'beyond-original-budget', kind='move', position=dict(x=1, y=4, z=0))
    assert hero(api, cid)['movement_remaining'] == 5
    checkpoint = api.store.save(cid, 'Dash active combat')
    saved = deepcopy(api.store.campaign(cid)['state']['nat20'])
    baseline = run(api, cid, 'next', kind='end_turn')
    assert hero(api, cid)['action_available'] and hero(api, cid)['movement_remaining'] == 30
    expected = deepcopy(api.store.campaign(cid)['state']['nat20'])
    api.store.restore(cid, checkpoint)
    program = '''
import json,sys
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
api=Nat20GameEngine(Store(sys.argv[1]));cid=sys.argv[2]
restored=api.restore(cid)['runtime']
result=api.execute(cid,GameCommand(request_id='continued',expected_version=api.store.campaign(cid)['version'],action={'kind':'end_turn'}))
print(json.dumps({'before':restored,'after':api.store.campaign(cid)['state']['nat20'],'events':result['native_events']}))
'''
    process = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', program, str(api.store.path), cid],
        cwd=ROOT, text=True, encoding='utf-8', capture_output=True, check=True, timeout=50,
        env=dict(os.environ, SOLO_TEST='1', PYTHONDONTWRITEBYTECODE='1'))
    continued = json.loads(process.stdout)
    assert continued == dict(before=saved, after=expected, events=baseline['native_events'])
    run(api, cid, 'next-dash', kind='dash')
    assert hero(api, cid)['movement_remaining'] == 60
    assert api.store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])


def test_dash_rollback_has_no_event_memory_or_rng_leak(fighter, monkeypatch):
    api, cid = fighter
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    before = deepcopy(api.store.campaign(cid))
    history = api.store.history(cid)
    body = command(api, cid, 'dash', kind='dash')
    def fail(*args, **kwargs):
        raise RuntimeError('Injected Dash memory failure')
    with monkeypatch.context() as fault:
        fault.setattr(api.store.memory, 'ingest', fail)
        with pytest.raises(RuntimeError, match='Injected Dash'):
            api.execute(cid, body)
    assert api.store.campaign(cid) == before and api.store.history(cid) == history
    assert api.restore(cid)['runtime'] == before['state']['nat20']
    result = api.execute(cid, body)
    assert result['native_events'] == [dict(type='dash_taken', actor_id='hero',
        doubled_movement_remaining=60, budget_consumed='action')]
    assert api.store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])


def test_http_master_and_screen_use_same_native_dash(fighter, monkeypatch):
    api, cid = fighter
    llm = CommandMockLLM({'dash': dict(status='command', action=dict(kind='dash'), message='')})
    def forbidden(*args, **kwargs):
        raise AssertionError('Nat20 cannot use legacy mechanics')
    monkeypatch.setattr(CommandEngine, 'resolve', forbidden)
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    with TestClient(create_app(api.store.path, llm)) as client:
        screen = client.get(f'/api/campaigns/{cid}/screen').json()
        assert any(c['parameters'] == dict(kind='dash') and c['label'] == 'Рывок' for c in screen['commands'])
        body = dict(request_id='master-dash', expected_version=api.store.campaign(cid)['version'], text='dash')
        result = client.post(f'/api/campaigns/{cid}/command-turn', json=body).json()
        assert result['status'] == 'resolved'
        assert result['events'][0]['payload']['command'] == 'dash'
        assert next(a for a in result['snapshot']['actors'] if a['id'] == 'road-raider')['resources'] == {}
        assert client.post(f'/api/campaigns/{cid}/command-turn', json=body).json()['narrative_policy'] == 'replayed'
        screen = client.get(f'/api/campaigns/{cid}/screen').json()
        assert not any(c['parameters']['kind'] in ('dash', 'attack') for c in screen['commands'])
        resources = {r['id']: r['current'] for r in screen['snapshot']['hero']['resources']}
        assert resources['movement_remaining'] == 60 and resources['action_available'] == 0
        body.update(request_id='spent-dash', expected_version=api.store.campaign(cid)['version'])
        assert client.post(f'/api/campaigns/{cid}/command-turn', json=body).json()['status'] == 'rejected'
        response = client.post(f'/api/campaigns/{cid}/commands', json=dict(request_id='button-turn',
            expected_version=api.store.campaign(cid)['version'], action=dict(kind='end_turn')))
        assert response.status_code == 200, response.text
        screen = client.get(f'/api/campaigns/{cid}/screen').json()
        button = next(c for c in screen['commands'] if c['parameters']['kind'] == 'dash')
        body = dict(request_id='button-dash', expected_version=button['expected_version'], action=button['parameters'])
        response = client.post(f'/api/campaigns/{cid}/commands', json=body)
        assert response.status_code == 200, response.text
        assert hero(api, cid)['movement_remaining'] == 60 and not hero(api, cid)['action_available']
        assert client.post(f'/api/campaigns/{cid}/commands', json=body).json() == response.json()


def test_closed_contract_old_fixture_and_legacy_boundary(fighter):
    from rules_data.testing.schema_validation import _validate
    from solo.nat20_contracts import GameCommand as NativeCommand
    api, cid = fighter
    schema_path = ROOT / 'integration/nat20/schemas/game-command-v1.schema.json'
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    assert schema == NativeCommand.model_json_schema()
    instance = NativeCommand(request_id='schema-dash', expected_version=0, action=dict(kind='dash')).model_dump(mode='json')
    assert _validate(json.dumps(instance), str(schema_path), schema_path.read_bytes())
    instance['action']['use_bonus_action'] = True
    assert not _validate(json.dumps(instance), str(schema_path), schema_path.read_bytes())
    # A difficulty may only arrive with the master's mark. The JSON shape cannot
    # express that dependency, so the model is the guard; this pins the rule to
    # the bound model too, so a future schema-only reader knows what it owes.
    difficulty = NativeCommand(request_id='schema-check', expected_version=0,
                               action=dict(kind='check', check_id='look', dc=15, source='master'))
    assert _validate(json.dumps(difficulty.model_dump(mode='json')), str(schema_path), schema_path.read_bytes())
    with pytest.raises(ValueError, match='master ruling'):
        NativeCommand(request_id='schema-check', expected_version=0,
                      action=dict(kind='check', check_id='look', dc=15))
    assert next(t for t in tool_schemas() if t['function']['name'] == 'dash')['function']['parameters']['properties'] == {}
    for forbidden in ('speed', 'movement', 'use_bonus_action', 'state_patch', 'dc'):
        with pytest.raises(ValidationError):
            command(api, cid, 'invalid', kind='dash', **{forbidden: 30})
    with pytest.raises(ValueError, match='No active'):
        run(api, cid, 'outside', kind='dash')
    # The preview.7 save keeps its own digest; the corpus it was made from has
    # since changed (translation plus one declared patch), so the honest answer
    # is a named refusal, not a rewritten digest. See
    # docs/RELEASE_IDENTITY_DECISION.md.
    fixture = json.loads((Path(__file__).parent / 'fixtures/nat20-preview7-fighter-combat.json').read_text(encoding='utf-8'))
    old_state = fixture['campaign']['state']
    assert old_state['nat20']['release_digest'] == fixture['release_digest'], 'the fixture keeps its own release'
    assert fixture['release_digest'] != FINGERPRINT, 'a translated corpus is a different release'
    old = api.store.create('Old fighter', Hero.model_validate(old_state['hero']), initial_state=old_state)['id']
    with pytest.raises(ValueError, match='release changed'):
        api.restore(old)
    # The refusal names the reason and leaves the stored save untouched.
    assert api.store.campaign(old)['state']['nat20']['release_digest'] == fixture['release_digest']
    legacy = dict(state={})
    with pytest.raises(ValueError, match='Nat20'):
        CommandEngine(api.store).resolve(legacy, command(api, cid, 'legacy', kind='dash'))

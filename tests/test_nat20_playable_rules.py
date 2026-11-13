"""Playable scope acceptance: real local Nat20, mock GM, isolated SQLite only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
# `_validate` жил в `rules_data.testing.schema_validation` ХОСТОВОГО проекта
# (соседний `SoloRealmsAI`). В этой папке такого каталога нет и быть не должно:
# это чужой поток. Схема `campaign.schema.json` ссылается ТОЛЬКО на внутренние
# `#/$defs/...` (14 ссылок, ни одной внешней), поэтому та же проверка выполнима
# локально через `jsonschema` без чужих модулей и без сети.
def _validate(serialized: str, schema_path: str, schema_bytes: bytes) -> bool:
    del schema_path  # remote refs are forbidden by construction: every $ref is local
    schema = json.loads(schema_bytes)
    refs = [""]
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == '$ref' and isinstance(value, str):
                    refs.append(value)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    for reference in refs:
        if reference and not reference.startswith('#'):
            raise AssertionError(f'remote $ref is forbidden: {reference}')
    validator = Draft202012Validator(schema)
    return not list(validator.iter_errors(json.loads(serialized)))

from solo.app import create_app
from solo.command_engine import CommandEngine
from solo.contracts import Hero
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_campaign import ROOT, load_definition, validate_definition
from solo.nat20_contracts import DurableState
from solo.rulesets import BUILTINS
from solo.storage import Store

PROJECT = ROOT.parents[2]
ACTIONS = dict(check=dict(kind='check', check_id='athletics'),
    start=dict(kind='start_combat', encounter_id='basic'),
    move=dict(kind='move', position=dict(x=2, y=1, z=0)),
    attack=dict(kind='attack', target='road-raider'), turn=dict(kind='end_turn'),
    wind=dict(kind='use_feature', feature_id='second-wind'), end=dict(kind='end_combat'),
    short=dict(kind='short_rest', hit_dice=1), long=dict(kind='long_rest', food_water=False))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect
    def local_only(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External APIs are forbidden')
    monkeypatch.setattr(socket.socket, 'connect', local_only)


def model():
    return CommandMockLLM({text: dict(status='command', action=action, message='')
                           for text, action in ACTIONS.items()})


def post(client, store, cid, identity, text, version=None):
    response = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
        request_id=identity, text=text,
        expected_version=store.campaign(cid)['version'] if version is None else version))
    assert response.status_code == 200, response.text
    return response.json()


def resolved(client, store, cid, identity, text):
    result = post(client, store, cid, identity, text)
    assert result['status'] == 'resolved', result
    return result


def run(api, cid, identity, **action):
    return api.execute(cid, GameCommand(request_id=identity,
        expected_version=api.store.campaign(cid)['version'], action=action))


def restart(database, cid, mode):
    store = Store(database)
    restored = Nat20GameEngine(store).restore(cid)
    result = dict(before=restored['runtime'], snapshot=restored['snapshot'].model_dump(mode='json'))
    with TestClient(create_app(store.path, model())) as client:
        if mode == 'fight':
            for i in range(24):
                view = store.campaign(cid)['state']['nat20']['combat']['view']
                if view['tracked_hp']['road-raider'] <= 0:
                    break
                assert view['tracked_hp']['hero'] > 0, 'Ordinary fighter did not survive'
                hero_turn = view['initiative'][view['current_turn_index']]['entity_id'] == 'hero'
                resolved(client, store, cid, f'continued-{i}', 'attack' if hero_turn else 'turn')
                if hero_turn:
                    # PHB 2024: an Action does not end the turn; without this
                    # the next attack is correctly refused as no_action_economy.
                    resolved(client, store, cid, f'continued-turn-{i}', 'turn')
            else:
                raise AssertionError('Bounded ordinary fight did not finish')
        else:
            result['continued'] = resolved(client, store, cid, 'continued-check', 'check')
    result['after'] = store.campaign(cid)['state']['nat20']
    return result


def fresh_process(database, cid, mode):
    process = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
        str(database), cid, mode], cwd=PROJECT, text=True, encoding='utf-8', capture_output=True,
        timeout=150, env=dict(os.environ, SOLO_TEST='1', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(PROJECT)))
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_ordinary_fighter_http_fight_reward_atomic_memory_rest_and_restart(tmp_path, monkeypatch):
    store = Store(tmp_path / 'ordinary.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_campaign('Ordinary fighter', definition_id='fighter-standard-array-2024-v1', seed=5)['id']
    hero = store.campaign(cid)['state']['hero']
    assert hero['hp'] == hero['max_hp'] == 12 and hero['ac'] == 18
    assert sorted(hero['abilities'].values()) == [8, 10, 12, 13, 14, 15]
    def forbidden(*args, **kwargs):
        raise AssertionError('Legacy engine cannot resolve native campaign')
    monkeypatch.setattr(CommandEngine, 'resolve', forbidden)
    app = create_app(store.path, model())
    with TestClient(app) as client:
        check = resolved(client, store, cid, 'ordinary-check', 'check')
        assert check['events'][0]['payload']['effects'][0]['type'] == 'roll'
        event = store.event(check['journal_id'])['results'][0]
        assert event['native_result']['modifier'] == 4  # different build, native derived modifier
        resolved(client, store, cid, 'ordinary-start', 'start')
        opened = store.campaign(cid)['state']['nat20']['combat']['initial']
        assert opened['encounter'][0]['entity_id'] == 'road-raider'
        assert opened['encounter'][0]['zone_id'] == '3,1'
        resolved(client, store, cid, 'ordinary-move', 'move')
        resolved(client, store, cid, 'ordinary-attack', 'attack')
        # PHB 2024: the attack spends the Action but does not end the turn.
        # End it here, so the restored fight resumes on a clean turn instead
        # of inheriting a spent Action that the loop would re-spend.
        resolved(client, store, cid, 'ordinary-turn', 'turn')
        saved = store.save(cid, 'Active ordinary combat')
    before = deepcopy(store.campaign(cid)['state']['nat20'])
    continued = fresh_process(store.path, cid, 'fight')
    assert continued['before'] == before
    assert continued['after']['combat']['view']['tracked_hp']['road-raider'] == 0
    assert continued['after']['hero']['hp_current'] > 0
    # The existing memory transaction also protects the new reward resource.
    app = create_app(store.path, model())
    before_award = deepcopy(store.campaign(cid))
    with TestClient(app, raise_server_exceptions=False) as client:
        def failed(*args, **kwargs):
            raise RuntimeError('Injected reward memory failure')
        with monkeypatch.context() as fault:
            fault.setattr(app.state.store.memory, 'put', failed)
            response = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
                request_id='award', text='end', expected_version=before_award['version']))
            assert response.status_code == 500
        assert store.campaign(cid) == before_award and store.request(cid, 'award') is None
        award = resolved(client, store, cid, 'award', 'end')
        runtime = store.campaign(cid)['state']['nat20']
        receipt = runtime['rewards'][0]
        native_result = store.event(award['journal_id'])['results'][0]['native_result']
        assert runtime['experience'] == receipt['experience'] == native_result['outcome']['xp_awarded']['hero'] == 50
        assert receipt['source']['kind'] == 'authored_scenario'
        assert store.campaign(cid)['state']['hero']['experience'] == 50
        assert any(e['type'] == 'resource' and e['payload']['resource'] == 'experience'
                   for e in award['events'][0]['payload']['effects'])
        replay = post(client, store, cid, 'award', 'end', before_award['version'])
        assert replay['snapshot'] == award['snapshot'] and replay['narrative_policy'] == 'replayed'
        for identity, text in [('farm', 'start'), ('no-combat', 'end')]:
            prior = deepcopy(store.campaign(cid))
            assert post(client, store, cid, identity, text)['status'] == 'rejected'
            assert store.campaign(cid) == prior
        resolved(client, store, cid, 'short', 'short')
        resolved(client, store, cid, 'long', 'long')
        assert store.campaign(cid)['state']['hero']['hp'] == 12
    final = deepcopy(store.campaign(cid)['state']['nat20'])
    resumed = fresh_process(store.path, cid, 'check')
    assert resumed['before'] == final and resumed['after']['experience'] == 50
    assert store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])
    facts = app.state.game.compiler.engine_context(cid, limit=80, budget=30000)['memories']
    assert any(f['kind'] == 'resource' and 'experience' in json.dumps(f) for f in facts)
    # Existing checkpoint branching withdraws reward and its memory together.
    store.restore(cid, saved)
    assert api.restore(cid)['runtime']['experience'] == 0
    assert api.restore(cid)['runtime']['rewards'] == []
    facts = create_app(store.path, model()).state.game.compiler.engine_context(cid, limit=80, budget=30000)['memories']
    assert not any(f.get('source_event') == 'nat20:award' for f in facts)


def test_second_wind_uses_native_feature_economy_replay_and_rest(tmp_path):
    store = Store(tmp_path / 'feature.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_campaign('Injured normal fighter', seed=5, hp_current=7)['id']
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    app = create_app(store.path, model())
    with TestClient(app) as client:
        before = deepcopy(store.campaign(cid))
        wind = resolved(client, store, cid, 'wind', 'wind')
        runtime = store.campaign(cid)['state']['nat20']
        assert 7 < runtime['hero']['hp_current'] <= 12
        assert runtime['hero']['custom_counters']['feature_use:second-wind']['spent'] == 1
        native = store.event(wind['journal_id'])['results'][0]['native_events']
        assert any(e['type'] == 'healing_applied' for e in native)
        assert any(e['type'] == 'healing' for e in wind['events'][0]['payload']['effects'])
        assert api.restore(cid)['runtime'] == runtime  # full replay includes native feature RNG and counters
        assert post(client, store, cid, 'wind', 'wind', before['version'])['snapshot'] == wind['snapshot']
        # The button/common command endpoint uses exactly the same native economy.
        repeated = client.post(f'/api/campaigns/{cid}/commands', json=dict(
            request_id='bonus-used', expected_version=store.campaign(cid)['version'], action=ACTIONS['wind']))
        assert repeated.status_code == 409 and 'no_action_economy' in repeated.text
        assert store.campaign(cid)['state']['nat20'] == runtime
        assert post(client, store, cid, 'stale-feature', 'wind', 0)['status'] == 'state_version_error'
    store.undo(cid)
    assert api.restore(cid)['runtime']['hero']['hp_current'] == 7
    assert api.restore(cid)['runtime']['hero']['custom_counters']['feature_use:second-wind']['spent'] == 0
    # Native rest recharge is checked separately at a trusted exhausted fixture.
    other = api.create_campaign('Spent normal fighter', seed=5, second_wind_spent=2)['id']
    run(api, other, 'start', kind='start_combat', encounter_id='basic')
    exhausted = deepcopy(store.campaign(other))
    with pytest.raises(ValueError, match='refused'):
        run(api, other, 'empty', **ACTIONS['wind'])
    assert store.campaign(other) == exhausted
    store.undo(other)
    run(api, other, 'short', **ACTIONS['short'])
    assert store.campaign(other)['state']['nat20']['hero']['custom_counters']['feature_use:second-wind']['spent'] == 1
    run(api, other, 'long', **ACTIONS['long'])
    assert store.campaign(other)['state']['nat20']['hero']['custom_counters']['feature_use:second-wind']['spent'] == 0


def test_registered_data_schema_and_ai_cannot_supply_mechanics(tmp_path):
    schema_path = ROOT / 'campaign.schema.json'
    registry = json.loads((ROOT / 'registry.json').read_text(encoding='utf-8'))
    for identity in registry['definitions']:
        definition = load_definition(identity).model_dump(mode='json')
        assert _validate(json.dumps(definition), str(schema_path), schema_path.read_bytes())
        assert validate_definition(definition).id == identity
        for mutation in ('dc', 'abilities', 'reward', 'edition'):
            raw = deepcopy(definition)
            if mutation == 'dc': raw['checks']['athletics']['dc'] = 1
            elif mutation == 'abilities': raw['build']['ability_scores']['strength'] = 20
            elif mutation == 'reward': raw['encounters']['basic']['actors'][0]['xp_value'] = 999
            else: raw['rules_version'] = 'dnd2014'
            with pytest.raises(ValueError): validate_definition(raw)
    for action in [dict(kind='check', check_id='athletics', dc=1),
                   dict(kind='start_combat', encounter_id='basic', actors=[]),
                   dict(kind='use_feature', feature_id='action-surge'),
                   dict(kind='use_feature', feature_id='second-wind', amount=999),
                   dict(kind='grant_reward', experience=999), dict(kind='exec', code='print(1)')]:
        with pytest.raises(ValueError):
            GameCommand(request_id='inject', expected_version=0, action=action)
    api = Nat20GameEngine(Store(tmp_path / 'guards.sqlite3'))
    cid = api.create_campaign('Scope guards', seed=5)['id']
    before = deepcopy(api.store.campaign(cid))
    for identity, action in [('unknown-check', dict(kind='check', check_id='unreviewed')),
                             ('unknown-encounter', dict(kind='start_combat', encounter_id='invented')),
                             ('out-of-combat-feature', ACTIONS['wind'])]:
        with pytest.raises(ValueError): run(api, cid, identity, **action)
        assert api.store.campaign(cid) == before
    corrupt = deepcopy(before['state']['nat20']); corrupt['experience'] = 999
    with pytest.raises(ValueError, match='reward ledger'): DurableState.model_validate(corrupt)
    with TestClient(create_app(api.store.path, model())) as client:
        for suffix in ('play', 'actions', 'combat/start', 'combat/command',
                       'rest', 'ruleset/apply', 'inventory/command', 'trade/apply',
                       'hybrid-rules/checks'):
            response = client.post(f'/api/campaigns/{cid}/{suffix}', json={})
            assert response.status_code == 409 and response.json()['detail']['code'] == 'command_backend_required'
        # The world layer is not a mechanics backend: a Nat20 campaign walks its
        # prepared places and accepts or declines an offered encounter through it,
        # while every attack, check and rest stays on the Nat20 screen. Its refusal
        # is therefore a validation error about the empty command, not a backend
        # conflict, and the campaign state is still untouched.
        world = client.post(f'/api/campaigns/{cid}/world/command', json={})
        assert world.status_code == 422
        assert api.store.campaign(cid) == before


def test_old_native_recipe_and_ordinary_solo_keep_their_backend(tmp_path):
    store = Store(tmp_path / 'compat.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_campaign('Old native', seed=5)['id']
    # The pre-definition native state is still supported, never silently upgraded.
    with store.connection(True) as db:
        state = store.campaign(cid, db)['state']
        for key in ('definition', 'experience', 'rewards'): state['nat20'].pop(key)
        db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state), cid))
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    assert api.restore(cid)['runtime']['definition'] is None
    assert 'allowed_features' not in api.restore(cid)['runtime']['combat']['initial']
    before = store.campaign(cid)
    # A02: отказ движка читается по-русски и называет путь восстановления:
    # английское «is not registered» до игрока больше не доходит.
    with pytest.raises(ValueError, match='не зарегистрировано в текущей сцене'):
        run(api, cid, 'no-upgrade', **ACTIONS['wind'])
    assert store.campaign(cid) == before
    ordinary = store.create('Solo', Hero(name='Solo'), BUILTINS['dnd2024'])['id']
    legacy = CommandEngine(store)
    for action in [ACTIONS['wind'], dict(kind='start_combat', encounter_id='unknown')]:
        with pytest.raises(ValueError):
            legacy.execute(ordinary, GameCommand(request_id='legacy', expected_version=0, action=action))
    assert 'nat20' not in store.campaign(ordinary)['state']


def test_search_catalogue_is_not_a_mechanical_permission():
    catalog = json.loads((ROOT / 'search-catalog.json').read_text(encoding='utf-8'))
    capabilities = json.loads((ROOT / 'capabilities.json').read_text(encoding='utf-8'))
    assert len(catalog['packs']) == 9 and len(catalog['documents']) > 2000
    assert all(not d['executable'] and d['import_status'] == 'metadata_only' for d in catalog['documents'])
    assert all(p['provenance'] and p['license'] and p['rules_version'] == 'dnd2024' for p in catalog['packs'])
    assert capabilities['character']['active_features'] == ['second-wind']
    assert len(capabilities['sources']) == 12
    assert 'cast_spell' not in capabilities['actions']


def test_pending_campaign_cannot_activate_outside_isolated_stand(tmp_path, monkeypatch):
    from solo import nat20_campaign
    registry = json.loads((ROOT / 'registry.json').read_text(encoding='utf-8'))
    identity = 'fighter-road-2024-v1'
    entry = registry['definitions'][identity]
    entry['verification'] = 'behavioral-tests-required'
    (tmp_path / entry['file']).write_bytes((ROOT / entry['file']).read_bytes())
    (tmp_path / 'registry.json').write_text(json.dumps(registry), encoding='utf-8')
    monkeypatch.setattr(nat20_campaign, 'ROOT', tmp_path)
    monkeypatch.delenv('SOLO_TEST', raising=False)
    with pytest.raises(ValueError, match='поведенческую приёмку'):
        nat20_campaign.load_definition(identity)
    monkeypatch.setenv('SOLO_TEST', '1')
    assert nat20_campaign.load_definition(identity).id == identity


if __name__ == '__main__':
    print(json.dumps(restart(sys.argv[1], sys.argv[2], sys.argv[3]), ensure_ascii=False))

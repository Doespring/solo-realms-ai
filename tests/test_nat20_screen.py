"""Screen projection/buttons use real Nat20 and disposable databases only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.command_engine import CommandEngine
from solo.nat20_api import Nat20GameEngine
from solo.rulesets import RulesetEngine
from test_nat20_command_cycle import ACTIONS, model, offline, native


def get_screen(client, cid):
    response = client.get(f'/api/campaigns/{cid}/screen')
    assert response.status_code == 200, response.text
    return response.json()


def body(packet, kind, request_id, **criteria):
    command = next(c for c in packet['commands'] if c['parameters']['kind'] == kind
                   and all(c['parameters'].get(k) == v for k, v in criteria.items()))
    return {k: command[k] for k in ('kind', 'parameters', 'expected_version')} | dict(request_id=request_id)


def click(client, cid, command):
    if command['kind'] == 'engine':
        return client.post(f'/api/campaigns/{cid}/commands', json=dict(request_id=command['request_id'],
                           expected_version=command['expected_version'], action=command['parameters']))
    if command['kind'] == 'restore':
        return client.post(f"/api/campaigns/{cid}/restore/{command['parameters']['checkpoint']}", json={})
    return client.post(f'/api/campaigns/{cid}/screen/commands', json=command)


def test_native_screen_is_read_only_cached_and_uses_native_numbers(native, monkeypatch):
    store, cid = native
    def legacy_forbidden(*args, **kwargs):
        raise AssertionError('Legacy mathematics must not project/resolve Nat20')
    monkeypatch.setattr(RulesetEngine, 'sheet', legacy_forbidden)
    monkeypatch.setattr(CommandEngine, 'resolve', legacy_forbidden)
    worker = Nat20GameEngine._worker
    calls = []
    def traced(payload):
        calls.append(payload['mode'])
        return worker(payload)
    monkeypatch.setattr(Nat20GameEngine, '_worker', staticmethod(traced))
    before = deepcopy(store.campaign(cid))
    with TestClient(create_app(store.path, model())) as client:
        packet = get_screen(client, cid)
        count = len(calls)
        assert count == 1 and get_screen(client, cid) == packet and len(calls) == count
        assert packet['snapshot']['backend'] == 'nat20' and packet['snapshot']['command_turn']
        assert packet['snapshot']['hero']['hp'] == 12 and packet['snapshot']['hero']['hit_dice'] == 1
        assert len([c for c in packet['commands'] if c['parameters']['kind'] == 'check']) == 3
        assert all(c['kind'] == 'engine' and c['expected_version'] == 0 for c in packet['commands'])
        assert all(k not in json.dumps(packet) for k in ('release_digest', 'native_events', 'rng_seed', 'engine_snapshot'))
    assert store.campaign(cid) == before and store.history(cid) == []


def test_native_screen_buttons_double_submit_stale_refusal_and_restore(native):
    store, cid = native
    app = create_app(store.path, model())
    with TestClient(app) as client:
        initial = get_screen(client, cid)
        command = body(initial, 'check', 'screen-check', check_id='athletics')
        # Two concurrent HTTP sends still commit a single event and memory projection.
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: click(client, cid, command), range(2)))
        assert all(r.status_code == 200 for r in responses)
        assert responses[0].json() == responses[1].json()
        assert store.campaign(cid)['version'] == 1 and len(store.history(cid)) == 1
        stale = click(client, cid, body(initial, 'start_combat', 'stale-start'))
        assert stale.status_code == 409 and stale.json()['detail']['code'] == 'version_conflict'
        checked = get_screen(client, cid)
        assert checked['snapshot']['last_roll']['label'] == 'Проверка'
        assert checked['events'][-1]['roll'] == checked['snapshot']['last_roll']
        assert click(client, cid, body(checked, 'start_combat', 'screen-start')).status_code == 200
        started = get_screen(client, cid)
        assert started['snapshot']['combat']['current_actor'] == 'hero'
        assert not any(c['parameters']['kind'] == 'attack' for c in started['commands'])  # Out of native reach.
        assert not any(c['parameters']['kind'] in ('end_combat', 'short_rest') for c in started['commands'])
        before = store.campaign(cid)
        refused = click(client, cid, dict(request_id='illegal-rest', expected_version=before['version'],
                                         kind='rest', parameters=dict(kind='long')))
        assert refused.status_code == 409 and store.campaign(cid) == before
        invalid_attack = click(client, cid, dict(request_id='far', expected_version=before['version'],
                                                kind='engine', parameters=ACTIONS['attack']))
        assert invalid_attack.status_code == 409 and store.campaign(cid) == before
        destination = dict(x=3, y=1, z=0)
        assert click(client, cid, body(started, 'move', 'screen-move', position=destination)).status_code == 200
        moved = get_screen(client, cid)
        assert click(client, cid, body(moved, 'attack', 'screen-attack')).status_code == 200
        # The engine keeps the turn open while movement or a Bonus Action remains
        # (orchestrator.py:13300-13307): SRD 5.2 lets you break up movement
        # around an action, so the HOST hands the turn on with an explicit pass.
        spent = get_screen(client, cid)
        assert spent['snapshot']['combat']['current_actor'] == 'hero'
        assert click(client, cid, body(spent, 'end_turn', 'screen-pass')).status_code == 200
        active = get_screen(client, cid)
        # end_turn hands the turn on AND runs the monster policy to convergence
        # (solo/nat20_worker.py:122-130), so by the time the screen comes back the
        # goblin has already acted and the round has advanced.
        assert active['snapshot']['combat']['round'] == 2
        assert active['snapshot']['combat']['current_actor'] == 'hero'
        assert active['snapshot']['hero']['hp'] < 12  # the goblin attacked
        # The goblin's swing is the roll the screen now reports, and the fresh
        # hero turn re-offers an attack: a spent Action was what made it
        # unavailable mid-turn, not the turn handover.
        assert active['snapshot']['last_roll']['label'] == 'Атака'
        assert any(c['parameters']['kind'] == 'attack' for c in active['commands'])
        assert any(c['parameters']['kind'] == 'end_turn' for c in active['commands'])
        saved = client.post(f'/api/campaigns/{cid}/saves', json=dict(name='NPC turn')).json()['id']
        assert click(client, cid, body(active, 'end_turn', 'screen-turn')).status_code == 200
        changed = get_screen(client, cid)
        restore = click(client, cid, dict(request_id='restore', expected_version=changed['snapshot']['version'],
                                         kind='restore', parameters=dict(checkpoint=saved)))
        assert restore.status_code == 200
    # Fresh app/cache verifies and projects the restored active battle.
    with TestClient(create_app(store.path, model())) as client:
        restarted = get_screen(client, cid)
        assert restarted['snapshot']['combat'] == active['snapshot']['combat']
        assert restarted['snapshot']['hero'] == active['snapshot']['hero']
        assert restarted['snapshot']['last_roll'] == active['snapshot']['last_roll']
        assert restarted['snapshot']['version'] > changed['snapshot']['version']
        assert click(client, cid, body(restarted, 'end_turn', 'screen-continued')).status_code == 200
    assert store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])


def test_native_free_text_and_buttons_share_backend_and_resources(tmp_path):
    from solo.storage import Store
    store = Store(tmp_path / 'screen-rest.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Screen resources', seed=5, hp_current=7,
                                               second_wind_spent=2, conditions=('poisoned',))['id']
    with TestClient(create_app(store.path, model())) as client:
        initial = get_screen(client, cid)
        assert initial['snapshot']['hero']['conditions'] == ['poisoned']
        assert initial['snapshot']['hero']['resources'][0]['current'] == 2
        assert 'потрачено' in initial['snapshot']['hero']['resources'][0]['label']
        response = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
            request_id='text-short', text='short', expected_version=0))
        assert response.json()['status'] == 'resolved'
        rested = get_screen(client, cid)
        assert rested['snapshot']['hero']['hp'] > 7 and rested['snapshot']['hero']['hit_dice'] == 0
        assert rested['snapshot']['hero']['resources'][0]['current'] == 1
        assert not any(c['parameters'].get('hit_dice') == 1 for c in rested['commands'])
        assert click(client, cid, body(rested, 'long_rest', 'button-long')).status_code == 200
        final = get_screen(client, cid)
        assert final['snapshot']['hero']['hp'] == 12 and final['snapshot']['hero']['hit_dice'] == 1
        assert final['snapshot']['hero']['resources'][0]['current'] == 0
        assert not any(c['parameters']['kind'] == 'long_rest' for c in final['commands'])
    assert all(r['results'][0].get('kind') == 'nat20_command' for r in store.history(cid))

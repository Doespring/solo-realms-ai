"""HTTP/master acceptance using real Nat20, disposable databases and mock AI only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.command_backend import command_backend
from solo.command_engine import CommandEngine
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store


ACTIONS = dict(
    check=dict(kind='check', check_id='athletics'),
    start=dict(kind='start_combat', encounter_id='basic'),
    move=dict(kind='move', position=dict(x=3, y=1, z=0)),
    attack=dict(kind='attack', target='goblin'),
    turn=dict(kind='end_turn'), end=dict(kind='end_combat'),
    short=dict(kind='short_rest', hit_dice=1), long=dict(kind='long_rest', food_water=False))


def model(**options):
    return CommandMockLLM({key: dict(status='command', action=value, message='')
                           for key, value in ACTIONS.items()}, **options)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect
    def denied(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)  # Windows asyncio wakeup socket.
        raise AssertionError('Paid/external APIs are forbidden in this test')
    monkeypatch.setattr(socket.socket, 'connect', denied)


@pytest.fixture
def native(tmp_path):
    store = Store(tmp_path / 'native-http.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('HTTP fighter', seed=5)['id']
    return store, cid


def post(client, store, cid, request_id, text, version=None):
    version = store.campaign(cid)['version'] if version is None else version
    response = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
        request_id=request_id, text=text, expected_version=version))
    assert response.status_code == 200, response.text
    return response.json()


def resolved(client, store, cid, request_id, text):
    result = post(client, store, cid, request_id, text)
    assert result['status'] == 'resolved', result
    assert result['events'][0]['id'] == 'nat20:' + request_id
    GameCommand.model_validate(result['command'])  # The shared command, not a parallel GM DTO.
    return result


def restart_http(database, cid):
    """Executed below in a real new interpreter; imports/commands are fixed test code."""
    store = Store(database)
    restored = Nat20GameEngine(store).restore(cid)
    before = restored['runtime']
    replies = []
    with TestClient(create_app(store.path, model())) as client:
        replies.append(resolved(client, store, cid, 'continued-turn', 'turn'))
        for i in range(16):
            view = store.campaign(cid)['state']['nat20']['combat']['view']
            if view['tracked_hp']['goblin'] <= 0:
                break
            hero_turn = view['initiative'][view['current_turn_index']]['entity_id'] == 'hero'
            replies.append(resolved(client, store, cid, 'continued-' + str(i),
                                      'attack' if hero_turn else 'turn'))
            if hero_turn:
                # PHB 2024: the Action does not end the turn; a second attack
                # without end_turn is correctly refused as no_action_economy.
                replies.append(resolved(client, store, cid, 'continued-turn-' + str(i), 'turn'))
        else:
            raise AssertionError('Bounded combat did not finish')
        for text in ('end', 'short', 'long'):
            replies.append(resolved(client, store, cid, 'continued-' + text, text))
    return dict(before=before, snapshot=restored['snapshot'].model_dump(mode='json'),
                version=restored['version'], replies=replies)


def test_http_native_cycle_save_new_process_continue_and_memory(native, monkeypatch):
    store, cid = native
    llm = model()
    app = create_app(store.path, llm)
    def legacy_forbidden(*args, **kwargs):
        raise AssertionError('Nat20 must not execute legacy mechanics/context')
    monkeypatch.setattr(CommandEngine, 'resolve', legacy_forbidden)
    monkeypatch.setattr(app.state.game.compiler, 'scene', legacy_forbidden)
    with TestClient(app) as client:
        check = resolved(client, store, cid, 'check', 'check')
        assert check['snapshot']['available_checks'] == ['athletics', 'perception', 'strength']
        assert check['events'][0]['payload']['effects'][0]['type'] == 'roll'
        resolved(client, store, cid, 'start', 'start')
        resolved(client, store, cid, 'move', 'move')
        attack = resolved(client, store, cid, 'attack', 'attack')
        # PHB 2024 §Combat: an Action does not end your turn — the actor ends it.
        # The engine keeps the hero on turn after the attack (see
        # tests/test_nat20_full_play_path.py, whose driver steps turns with
        # end_turn); asserting 'goblin' here encoded the opposite rule.
        assert attack['snapshot']['combat']['turn'] == 'hero'
        assert next(a for a in attack['snapshot']['actors'] if a['id'] == 'goblin')['resources'] == {}
        packets_before = len(llm.packets)
        repeated = post(client, store, cid, 'attack', 'attack', 3)
        assert repeated['events'] == attack['events'] and repeated['snapshot'] == attack['snapshot']
        assert repeated['narrative_policy'] == 'replayed' and len(llm.packets) == packets_before
        assert post(client, store, cid, 'attack', 'check')['status'] == 'rejected'
        assert post(client, store, cid, 'stale', 'turn', 0)['status'] == 'state_version_error'
        saved = client.post(f'/api/campaigns/{cid}/saves', json=dict(name='Active native combat'))
        assert saved.status_code == 200
        public = client.get(f'/api/campaigns/{cid}').json()
        assert 'nat20' not in public['state'] and 'engine_snapshot' not in public['state']
        assert all(key not in json.dumps(public) for key in ('native_events', 'native_result', 'release_digest', 'rng_seed'))
    current = store.campaign(cid)
    process = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
                              str(store.path), cid], cwd=Path(__file__).resolve().parents[1],
        text=True, encoding='utf-8', capture_output=True, timeout=150,
        env=dict(os.environ, SOLO_TEST='1', PYTHONDONTWRITEBYTECODE='1',
                 PYTHONPATH=str(Path(__file__).resolve().parents[1])))
    assert process.returncode == 0, process.stderr
    continued = json.loads(process.stdout)
    assert continued['before'] == current['state']['nat20']
    assert continued['version'] == current['version']
    assert continued['snapshot'] == current['state']['engine_snapshot']
    final = store.campaign(cid)
    assert final['state']['nat20']['combat'] is None
    assert final['state']['hero']['hp'] == 12
    assert continued['replies'][-1]['snapshot']['combat']['active'] is False
    # Replaying an old result after later commands returns its original native snapshot.
    replay_model = model()
    with TestClient(create_app(store.path, replay_model)) as client:
        replay = post(client, store, cid, 'attack', 'attack', 3)
        assert replay['snapshot'] == attack['snapshot'] and replay_model.packets == []
    history = store.history(cid)
    assert len(history) == final['version']
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM memory_engine_events WHERE campaign=?', (cid,)).fetchone()[0] == len(history)
    facts = app.state.game.compiler.engine_context(cid, limit=40, budget=20000)['memories']
    # The window is the last 40 memories, so a longer fight can push the
    # opening check's 'roll' out of it. Ask whether the kinds EXIST in the
    # campaign's memory instead of requiring all of them inside one window.
    assert {'damage', 'combat_ended', 'resource'} <= {f['kind'] for f in facts}
    with store.connection() as db:
        persisted = {r[0] for r in db.execute(
            'SELECT kind FROM memory_records WHERE campaign=?', (cid,))}
    # engine_context returns the most recent window; the campaign's memory is
    # the whole table. A longer fight must not hide the opening check's 'roll'.
    assert {'roll', 'damage', 'combat_ended', 'resource'} <= persisted
    with store.connection() as db:
        actual_effects = [effect for row in db.execute('SELECT results FROM events WHERE campaign=? AND undone=0', (cid,))
                          for result in json.loads(row[0]) for effect in result['game_event']['payload']['effects']]
        projected = db.execute("SELECT COUNT(*) FROM memory_records WHERE campaign=? AND source_ref LIKE 'Nat20 0.7.0%'", (cid,)).fetchone()[0]
        assert projected == len(actual_effects)
    # A rest at full HP does not invent a healing fact.
    assert ('healing' in {f['kind'] for f in facts}) == any(e['type'] == 'healing' for e in actual_effects)
    assert all(f['provenance'] == 'confirmed_event' and f['source_ref'].startswith('Nat20 0.7.0') for f in facts)
    assert store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])
    for packet in llm.packets:
        raw = json.dumps(packet['packet'])
        assert all(secret not in raw for secret in ('release_digest', 'rng_seed', 'native_events', 'custom_counters_by_entity'))
        context = packet['packet'].get('context', packet['packet'])
        if context['game_snapshot']['combat']['active']:
            assert next(a for a in context['game_snapshot']['actors'] if a['id'] == 'goblin')['resources'] == {}


def test_native_clarification_refusals_and_verified_narrator(native):
    store, cid = native
    llm = model(narrative=dict(description='Герой нашёл 100 золотых.', dialogue=[], proposals=[]))
    llm.intents.update({
        'unclear': dict(status='clarification', action=None, message='Какую проверку?'),
        'unclear\nУточнение игрока: athletics': dict(status='command', action=ACTIONS['check'], message=''),
        'unsupported': dict(status='unsupported', action=None, message='Заклинания не подключены.'),
        'injection': dict(status='command', action=dict(kind='attack', target='goblin', damage=999), message='')})
    with TestClient(create_app(store.path, llm)) as client:
        assert post(client, store, cid, 'question', 'unclear')['status'] == 'clarification'
        assert post(client, store, cid, 'question', 'unclear')['narrative'] == 'Какую проверку?'
    # Pending clarification survives a new Game/Store instance.
    with TestClient(create_app(store.path, llm)) as client:
        check = resolved(client, store, cid, 'answer', 'athletics')
        assert store.open_question(cid) is None
        # r254: выдумка модели называется своим именем — её отбросил валидатор,
        # а не провайдер; текст игроку — движковый.
        assert check['narrative_policy'] == 'engine_rejected' and 'золотых' not in check['narrative']
        before = store.campaign(cid)
        for text, status in (('unsupported', 'unsupported'), ('injection', 'clarification')):
            assert post(client, store, cid, text, text)['status'] == status
            assert store.campaign(cid) == before
        # Close the injection question by explicitly starting a separate unambiguous command.
        store.finish_question(store.open_question(cid)['id'], 'superseded')
        resolved(client, store, cid, 'start', 'start')
        before = store.campaign(cid)
        for text in ('end', 'short', 'check'):
            assert post(client, store, cid, 'illegal-' + text, text)['status'] == 'rejected'
            assert store.campaign(cid) == before
    assert '100 золотых' not in json.dumps(store.history(cid), ensure_ascii=False)


def test_native_racing_version_and_memory_failure_are_atomic(native, monkeypatch):
    store, cid = native
    llm = model()
    app = create_app(store.path, llm)
    original = llm.interpret_command
    async def racing(text, context):
        proposal = await original(text, context)
        store.record_scenario(cid, 'external', 'external', lambda state: [dict(type='scripted')])
        return proposal
    monkeypatch.setattr(llm, 'interpret_command', racing)
    with TestClient(app, raise_server_exceptions=False) as client:
        result = post(client, store, cid, 'race', 'check')
        assert result['status'] == 'state_version_error' and store.request(cid, 'race') is None
        monkeypatch.setattr(llm, 'interpret_command', original)
        before = store.campaign(cid)
        def failed_memory(*args, **kwargs):
            raise RuntimeError('Injected projection failure')
        with monkeypatch.context() as fault:
            fault.setattr(app.state.store.memory, 'put', failed_memory)
            response = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
                request_id='retry', text='start', expected_version=before['version']))
            assert response.status_code == 500
        assert store.campaign(cid) == before and store.request(cid, 'retry') is None
        with store.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM memory_engine_events WHERE campaign=?', (cid,)).fetchone()[0] == 0
        resolved(client, store, cid, 'retry', 'start')


def test_native_undo_abandoned_replay_and_backend_guard(native):
    store, cid = native
    with TestClient(create_app(store.path, model())) as client:
        first = resolved(client, store, cid, 'first', 'check')
        # Existing native API journals used the already-defined versioned envelope.
        from solo.nat20_contracts import GameCommand as NativeCommand
        journal = store.event(first['journal_id'])
        with store.connection(True) as db:
            results = journal['results']
            results[0]['game_command'] = NativeCommand.model_validate(results[0]['game_command']).model_dump(mode='json')
            db.execute('UPDATE events SET results=? WHERE id=?', (json.dumps(results), journal['id']))
        replay = post(client, store, cid, 'first', 'check', 0)
        assert replay['command'] == first['command'] and replay['narrative_policy'] == 'replayed'
        store.undo(cid)
        assert post(client, store, cid, 'first', 'check')['status'] == 'rejected'
        second = resolved(client, store, cid, 'second', 'check')
        assert second['snapshot']['branch_id'] != first['snapshot']['branch_id']
    context = command_backend(store, store.campaign(cid)).snapshot(store.campaign(cid))
    assert context.event_id == 'nat20:second'
    routed=CommandEngine(store).execute(cid, GameCommand(request_id='common', expected_version=store.campaign(cid)['version'], action=ACTIONS['check']))
    assert routed['event'].id=='nat20:common'
    facts = create_app(store.path, model()).state.game.compiler.engine_context(cid)['memories']
    assert all(f['source_event'] != 'nat20:first' for f in facts)


@pytest.mark.parametrize('guard', ['edition', 'adventure', 'world'])
def test_native_http_keeps_edition_and_scenario_guards(native, guard):
    store, cid = native
    from solo.rulesets import BUILTINS
    with store.connection(True) as db:
        state = store.campaign(cid, db)['state']
        if guard == 'edition':
            state['ruleset'] = BUILTINS['dnd2014'].model_dump(mode='json')
        elif guard == 'adventure':
            state['adventure'] = dict(definition=dict(entry_stage=dict(policy='unverified')))
        else:
            state['world'] = dict(events=[dict(id='unbound')])
        db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state), cid))
    before = store.campaign(cid)
    with TestClient(create_app(store.path, model())) as client:
        if guard == 'edition':
            # Sheet/context projection now rejects the corrupt edition before interpretation.
            response = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
                request_id='blocked', text='check', expected_version=before['version']))
            assert response.status_code == 409
            assert 'закреплена за правилами D&D 2024' in response.json()['detail']
        else:
            result = post(client, store, cid, 'blocked', 'check')
            assert result['status'] == 'rejected' and result['events'] == []
    assert store.campaign(cid) == before and store.request(cid, 'blocked') is None


if __name__ == '__main__':
    # The subprocess uses the same mock transports and network guard, never a provider.
    original_connect = socket.socket.connect
    def local_only(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return original_connect(sock, address)
        raise AssertionError('External network forbidden')
    socket.socket.connect = local_only
    print(json.dumps(restart_http(sys.argv[1], sys.argv[2]), ensure_ascii=False))

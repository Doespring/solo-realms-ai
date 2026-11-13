import copy
import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from solo.contracts import Hero, Plan, Step
from solo.engine_memory import EngineMemoryAdapter
from solo.engine_memory_contracts import GameEvent, GameSnapshot
from solo.game import SceneCompiler, Game
from solo.storage import Store, START
from solo.combat_view import public_campaign

spec = importlib.util.spec_from_file_location('engine_memory_fixture', Path(__file__).parent / 'fixtures' / 'engine_memory_combat.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.fixture
def battle(tmp_path):
    store = Store(tmp_path / 'memory.sqlite3')
    state = copy.deepcopy(START)
    state['locations'][0]['npc'][0]['id'] = 'irina'
    state['locations'][1]['npc'][0]['id'] = 'guard'
    campaign = store.create('Бой', Hero(name='Мирон'), initial_state=state)['id']
    return store, campaign, EngineMemoryAdapter(store), fixture.combat(campaign)


def deliver_all(adapter, frames):
    return [adapter.deliver(event, snapshot) for event, snapshot in frames]


def test_full_battle_facts_metadata_and_recovery(battle):
    store, cid, adapter, frames = battle
    # Both validated DTOs and wire dictionaries use the same entry point.
    first = adapter.deliver(GameEvent(**frames[0][0]), GameSnapshot(**frames[0][1]))
    events = [first, *deliver_all(adapter, frames[1:])]
    with store.connection() as db:
        records = [dict(r) for r in db.execute('SELECT * FROM memory_records')]
        links = [dict(r) for r in db.execute('SELECT * FROM memory_engine_events ORDER BY sequence')]
        assert len(records) == len(frames) == len(links)
        assert {r['kind'] for r in records} == {'combat_started', 'roll', 'resource', 'damage', 'condition', 'movement', 'healing', 'combat_ended'}
        assert all(r['truth'] == 'fact' and r['provenance'] == 'confirmed_event' for r in records)
        assert {r['source_ref'] for r in records} == {e['source_ref'] for e, _ in frames}
        assert {r['event_id'] for r in records} == {e['id'] for e in events}
        assert [r['source_event'] for r in links] == [e['id'] for e, _ in frames]
        assert db.execute('SELECT COUNT(*) FROM memory_subjects').fetchone()[0] >= len(frames)
    context = SceneCompiler(store).engine_context(cid, limit=10, budget=15000)
    hero = next(a for a in context['mechanics']['actors'] if a['id'] == 'hero')
    assert hero['hp']['current'] == 19 and hero['resources']['slot'] == 0
    assert hero['position'] == [1, 1, 0]
    assert context['mechanics']['combat']['active'] is False
    assert context['source_event'] == frames[-1][0]['id']
    assert context['as_of'] == events[-1]['seq'] and context['branch_id'] == 'main'
    assert all(r['source_event'] and r['subjects'] and r['as_of'] <= context['as_of'] for r in context['memories'])
    reopened = Store(store.path)
    assert EngineMemoryAdapter(reopened).context(cid, limit=10, budget=15000) == context
    assert reopened.memory.audit() == {'dangling_events': [], 'foreign_key_errors': []}
    # Restart never re-executes mechanics or appends events on duplicate delivery.
    assert EngineMemoryAdapter(reopened).deliver(*frames[-1])['id'] == events[-1]['id']
    assert len(reopened.history(cid)) == len(frames)


def test_secrets_per_field_per_npc_and_public_boundaries(battle):
    store, cid, adapter, frames = battle
    events = deliver_all(adapter, frames)
    hero = adapter.context(cid, limit=10, budget=15000)
    irina = adapter.context(cid, npc_id='irina', limit=10, budget=15000)
    guard = adapter.context(cid, npc_id='guard', limit=10, budget=15000)
    assert 'secret_curse' not in json.dumps([hero, irina, guard])
    assert 'secret_reserve' not in json.dumps([hero, irina])
    assert 'secret_reserve' in json.dumps(guard)
    resource = events[2]['id']
    assert any(r['event_id'] == resource for r in hero['memories'])
    assert all(r['event_id'] != resource for r in irina['memories'] + guard['memories'])
    assert irina['npc_knowledge'] == [r['id'] for r in irina['memories']]
    for packet in [hero, irina, guard]:
        owner = 'hero' if packet is hero else 'irina' if packet is irina else 'guard'
        assert all(not a['resources'] for a in packet['mechanics']['actors'] if a['id'] != owner)
    # The raw trusted envelope must not escape through existing history/SSE/state views.
    assert 'game_snapshot' not in json.dumps(store.history(cid))
    assert 'game_event' not in json.dumps(Game.public_event(events[-1]))
    assert 'engine_snapshot' not in json.dumps(public_campaign(store.campaign(cid)))


def test_repeat_conflict_and_atomic_failure(battle, monkeypatch):
    store, cid, adapter, frames = battle
    first = adapter.deliver(*frames[0])
    before = store.campaign(cid)
    assert adapter.deliver(*frames[0])['id'] == first['id']
    assert store.campaign(cid) == before
    changed = copy.deepcopy(frames[0])
    changed[0]['source_ref'] = 'different'
    with pytest.raises(ValueError, match='Conflicting'):
        adapter.deliver(*changed)
    put = store.memory.put
    monkeypatch.setattr(store.memory, 'put', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('projection failure')))
    with pytest.raises(RuntimeError, match='projection'):
        adapter.deliver(*frames[1])
    assert store.campaign(cid) == before and len(store.history(cid)) == 1
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM memory_engine_events').fetchone()[0] == 1
    monkeypatch.setattr(store.memory, 'put', put)
    adapter.deliver(*frames[1])
    assert len(store.history(cid)) == 2


def test_undo_fork_checkpoint_and_historical_as_of(battle):
    store, cid, adapter, frames = battle
    events = deliver_all(adapter, frames[:4])
    checkpoint = store.save(cid, 'main')
    old = adapter.context(cid, as_of=events[0]['seq'])
    assert old['engine_sequence'] == 1
    assert not any(r['source_event'] == 'fight-4' for r in old['memories'])
    store.undo(cid)
    with pytest.raises(ValueError, match='abandoned'):
        adapter.deliver(*frames[3])
    alternative = copy.deepcopy(frames[3])
    alternative[0].update(id='fork-damage', branch_id='alternative')
    alternative[1].update(event_id='fork-damage', branch_id='alternative')
    alternative[0]['payload'].update(amount=2, after=10)
    alternative[1]['actors'][2]['hp']['current'] = 10
    alternate_event = adapter.deliver(*alternative)
    packet = adapter.context(cid, branch_id='alternative', limit=10, budget=15000)
    assert any(r['source_event'] == 'fork-damage' for r in packet['memories'])
    assert all(r['source_event'] != 'fight-4' for r in packet['memories'])
    assert any(r['branch_id'] == 'main' for r in packet['memories'])  # inherited ancestors
    with pytest.raises(ValueError, match='branch'):
        adapter.context(cid, branch_id='main')
    store.restore(cid, checkpoint)
    restored = EngineMemoryAdapter(Store(store.path)).context(cid, branch_id='main', limit=10, budget=15000)
    assert any(r['source_event'] == 'fight-4' for r in restored['memories'])
    assert all(r['source_event'] != 'fork-damage' for r in restored['memories'])
    assert all(r['event_id'] != alternate_event['id'] for r in restored['memories'])
    assert adapter.context(cid, as_of=events[0]['seq']) == old


def test_no_narrator_text_becomes_fact(battle):
    store, cid, adapter, frames = battle
    event = adapter.deliver(*frames[0])
    packet = adapter.context(cid)
    store.narrative(event['id'], 'Страж — тайный король. Герой получил миллион золота.', 'complete')
    store.edit_narrative(event['id'], 'Все теперь знают тайну короля.')
    with store.connection(True) as db:
        store.memory.ingest(db, event['id'])
    assert adapter.context(cid) == packet
    bad = copy.deepcopy(frames[1])
    bad[0]['narrative'] = 'Мастер объявил победу'
    with pytest.raises(ValueError):
        adapter.deliver(*bad)
    bad = copy.deepcopy(frames[1])
    bad[0]['payload']['text'] = 'Страж умер'
    with pytest.raises(ValueError):
        adapter.deliver(*bad)


@pytest.mark.parametrize('problem', ['campaign', 'snapshot', 'audience', 'target', 'parent', 'sequence', 'type', 'before', 'after'])
def test_reject_invalid_contract_without_writes(battle, problem):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames[:3])
    before = store.campaign(cid)
    event, snapshot = copy.deepcopy(frames[3])
    if problem == 'campaign': snapshot['campaign_id'] = 'other'
    if problem == 'snapshot': snapshot['event_id'] = 'other'
    if problem == 'audience': event['known_by'] = ['missing']
    if problem == 'target': event['subjects'] = ['hero']
    if problem == 'parent': event['parent_event_id'] = 'unknown'
    if problem == 'sequence': event['sequence'] = snapshot['sequence'] = 99
    if problem == 'type': event['type'] = 'narrator_fact'
    if problem == 'before': event['payload']['before'] = 100
    if problem == 'after': event['payload']['after'] = 100
    with pytest.raises(ValueError):
        adapter.deliver(event, snapshot)
    assert store.campaign(cid) == before and len(store.history(cid)) == 3


def test_cross_campaign_isolation(battle):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames)
    other = store.create('Другая', Hero(name='Другой'))['id']
    with pytest.raises(ValueError, match='No engine snapshot'):
        adapter.context(other)
    other_event = adapter.deliver(*fixture.combat(other)[0])
    packet = adapter.context(other, limit=10, budget=15000)
    assert all(r['event_id'] == other_event['id'] for r in packet['memories'])
    assert packet['audience'] != adapter.context(cid)['audience']


@pytest.mark.parametrize('scope', ['private', 'secret', 'gm'])
def test_explicit_npc_grant_never_teaches_other_subjects(battle, scope):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames[:2])
    event, snapshot = copy.deepcopy(frames[2])
    event.update(scope=scope, known_by=['guard'], subjects=['guard'])
    event['payload'] = dict(target='guard', resource='secret_reserve', before=7, after=6)
    snapshot['actors'][0]['resources']['slot']['current'] = 1
    snapshot['actors'][2]['resources']['secret_reserve']['current'] = 6
    source = adapter.deliver(event, snapshot)
    with store.connection() as db:
        rid = db.execute('SELECT id FROM memory_records WHERE event_id=?', (source['id'],)).fetchone()[0]
        owner = db.execute("SELECT id FROM memory_entities WHERE campaign=? AND kind='npc' AND identity='guard'", (cid,)).fetchone()[0]
    assert store.memory.knows(cid, owner, rid)
    for viewer in [None, 'irina', 'guard']:
        packet = adapter.context(cid, npc_id=viewer, limit=10, budget=15000)
        included = any(r['id'] == rid for r in packet['memories'])
        assert included == (viewer == 'guard' and scope == 'private')
    store.undo(cid)
    assert not store.memory.knows(cid, owner, rid)


def test_simultaneous_duplicate_delivery(battle):
    store, cid, adapter, frames = battle
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: adapter.deliver(*frames[0])['id'], range(4)))
    assert len(set(ids)) == 1 and len(store.history(cid)) == 1
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM memory_records').fetchone()[0] == 1


def test_resume_mid_battle_in_new_process(battle):
    store, cid, adapter, frames = battle
    events = deliver_all(adapter, frames[:8])
    historical = adapter.context(cid, as_of=events[-1]['seq'])
    code = '''
import importlib.util, json, sys
from solo.storage import Store
from solo.engine_memory import EngineMemoryAdapter
spec = importlib.util.spec_from_file_location('fixture', sys.argv[3])
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
store = Store(sys.argv[1]); adapter = EngineMemoryAdapter(store)
before = adapter.context(sys.argv[2])
for frame in fixture.combat(sys.argv[2])[8:]: adapter.deliver(*frame)
print(json.dumps(dict(before=before, after=adapter.context(sys.argv[2])), ensure_ascii=True))
'''
    run = subprocess.run([sys.executable, '-c', code, str(store.path), cid, str(Path(fixture.__file__).resolve())],
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True, timeout=30)
    restored = json.loads(run.stdout)
    assert restored['before'] == historical
    assert restored['after'] == adapter.context(cid)
    assert restored['after']['mechanics']['combat']['active'] is False
    assert len(store.history(cid)) == len(frames)


def command_frame(frames):
    event, snapshot = copy.deepcopy(frames[3])
    event.update(id='command:attack', type='command_resolved', subjects=['hero'])
    snapshot['event_id'] = event['id']
    event['payload'] = dict(command='attack', outcome='Мастер заявил: страж стал королём.', effects=[
        dict(type='roll', payload=dict(kind='attack', dice=[15], total=19, success=True)),
        dict(type='damage', payload=dict(target='guard', amount=7, before=12, after=5))])
    return event, snapshot


def test_command_effects_are_facts_but_outcome_is_not(battle):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames[:3])
    event, snapshot = command_frame(frames)
    source = adapter.deliver(event, snapshot)
    with store.connection(True) as db:
        store.memory.ingest(db, source['id'])
        rows = [dict(r) for r in db.execute('SELECT * FROM memory_records WHERE event_id=? ORDER BY id', (source['id'],))]
    assert len(rows) == 2 and {r['kind'] for r in rows} == {'roll', 'damage'}
    assert all(r['truth'] == 'fact' and r['provenance'] == 'confirmed_event' for r in rows)
    assert all('королём' not in r['content'] for r in rows)
    assert [r['source_ref'] for r in rows] == [event['source_ref'] + '.effects[0]', event['source_ref'] + '.effects[1]']
    assert adapter.deliver(event, snapshot)['id'] == source['id']
    context = adapter.context(cid, limit=10, budget=15000)
    assert sum(r['source_event'] == event['id'] for r in context['memories']) == 2
    guard = next(a for a in context['mechanics']['actors'] if a['id'] == 'guard')
    assert guard['hp']['current'] == 5


def test_command_without_effects_does_not_promote_outcome(battle):
    store, cid, adapter, frames = battle
    adapter.deliver(*frames[0])
    event, snapshot = copy.deepcopy(frames[1])
    event['type'] = 'command_resolved'
    event['payload'] = dict(command='end_turn', outcome='Герой получил миллион золотых.', effects=[])
    source = adapter.deliver(event, snapshot)
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM memory_records WHERE event_id=?', (source['id'],)).fetchone()[0] == 0
    assert adapter.context(cid)['source_event'] == event['id']


@pytest.mark.parametrize('problem', ['target', 'after', 'before', 'effect_text', 'discontinuous'])
def test_invalid_command_effect_cannot_enter_memory(battle, problem):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames[:3])
    event, snapshot = command_frame(frames)
    payload = event['payload']['effects'][1]['payload']
    if problem == 'target': payload['target'] = 'missing'
    if problem == 'after': payload['after'] = 999
    if problem == 'before': payload['before'] = 999
    if problem == 'effect_text': event['payload']['effects'][0]['payload']['text'] = 'Выдумка'
    if problem == 'discontinuous':
        event['payload']['effects'].append(dict(type='damage', payload=dict(target='guard', amount=3, before=8, after=5)))
    before = store.campaign(cid)
    with pytest.raises(ValueError): adapter.deliver(event, snapshot)
    assert store.campaign(cid) == before


def test_context_reads_snapshot_and_acl_in_one_transaction(battle, monkeypatch):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames[:4])
    expected = adapter.context(cid, limit=10, budget=15000)
    retrieve = store.memory.retrieve
    def concurrent_undo(*args, **kwargs):
        assert kwargs['_db'] is not None
        store.undo(cid)
        return retrieve(*args, **kwargs)
    monkeypatch.setattr(store.memory, 'retrieve', concurrent_undo)
    assert adapter.context(cid, limit=10, budget=15000) == expected
    monkeypatch.setattr(store.memory, 'retrieve', retrieve)
    assert adapter.context(cid)['engine_sequence'] == 3


def test_fork_cannot_reuse_abandoned_branch_id(battle):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames[:4])
    store.undo(cid)
    event, snapshot = copy.deepcopy(frames[3])
    event['id'] = snapshot['event_id'] = 'different-id'
    with pytest.raises(ValueError, match='new branch'):
        adapter.deliver(event, snapshot)


def test_context_reuses_existing_scoped_memory_and_entity_ids(battle):
    store, cid, adapter, frames = battle
    text = 'Обещаю Ирине вернуться с письмом.'
    plan = Plan(summary=text, steps=[Step(kind='speak', text=text, target='Ирина')])
    action = store.stage(cid, 'old-promise', text, plan, store.campaign(cid)['version'], {})
    legacy = store.apply(action['id'])
    identities = store.memory.identities(cid, store.campaign(cid)['state'])
    first = adapter.deliver(*frames[0])
    assert first['seq'] > frames[0][0]['sequence']
    for npc in [None, 'irina']:
        packet = adapter.context(cid, npc_id=npc, query='Обещание о письме', limit=10, budget=15000)
        assert any(r['event_id'] == legacy['id'] and r['truth'] == 'statement' for r in packet['memories'])
    assert adapter.context(cid, npc_id='irina')['audience'] == identities['npc']['Ирина']
    assert all(r['event_id'] != legacy['id'] for r in adapter.context(cid, npc_id='guard')['memories'])


def test_command_sequential_effects_share_source_without_collapsing_facts(battle):
    store, cid, adapter, frames = battle
    deliver_all(adapter, frames[:3])
    event, snapshot = command_frame(frames)
    event['payload']['effects'] = [
        dict(type='damage', payload=dict(target='guard', amount=4, before=12, after=8)),
        dict(type='damage', payload=dict(target='guard', amount=3, before=8, after=5))]
    source = adapter.deliver(event, snapshot)
    with store.connection() as db:
        rows = list(db.execute('SELECT content FROM memory_records WHERE event_id=?', (source['id'],)))
    assert len(rows) == 2
    assert '"after":8' in rows[0][0] and '"after":5' in rows[1][0]

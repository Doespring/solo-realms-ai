"""Scene acceptance: real pinned Nat20 workers, no contract/combat/model fixtures."""
import copy
import json
import socket
import sqlite3

import pytest

import playable_scene_run as scene
from solo.command_engine import CommandEngine
from solo.engine_memory import EngineMemoryAdapter
from solo.game_commands import GameCommand
from solo.inventory import Item
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **kw: pytest.fail('External network forbidden'))
    monkeypatch.setattr(CommandEngine, 'resolve', lambda *a, **kw: pytest.fail('Legacy mechanics forbidden'))
    monkeypatch.setattr(EngineMemoryAdapter, 'deliver', lambda *a, **kw: pytest.fail('Contract fixture delivery forbidden'))


@pytest.fixture(scope='module')
def completed_scene(tmp_path_factory):
    # One real run shared as an immutable DB seed; individual tests get SQLite backups.
    store, cid = scene.create(tmp_path_factory.mktemp('native-scene') / 'scene.sqlite3')
    checked, started, attacked = scene.pause_in_combat(store, cid)
    paused = copy.deepcopy(store.campaign(cid))
    paused_memory = EngineMemoryAdapter(store).context(cid, limit=10, budget=20000)
    checkpoint = store.save(cid, 'Active Nat20 battle')
    continued = scene.child(store.path, cid)
    return dict(store=store, cid=cid, checked=checked, started=started, attacked=attacked,
                paused=paused, paused_memory=paused_memory, checkpoint=checkpoint, continued=continued)


@pytest.fixture
def story(completed_scene, tmp_path):
    source = completed_scene['store']
    path = tmp_path / 'scene-copy.sqlite3'
    with source.connection() as db, sqlite3.connect(path) as target:
        db.backup(target)
    return dict(completed_scene, store=Store(path))


def records(store, cid):
    with store.connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM memory_records WHERE campaign=? ORDER BY id', (cid,))]


def hero_letters(store, cid):
    return [i for i in store.campaign(cid)['state']['economy']['items'] if i['owner'] == 'hero' and i['quantity']]


def test_scene_formats_and_real_native_events_after_process_restart(story):
    store, cid = story['store'], story['cid']
    package = scene.definition()
    assert len(package.locations) == 2 and sum(len(p.npc) for p in package.locations) == 1
    assert package.events == []  # No unbound legacy encounter/clock hidden in the scene.
    Item.model_validate_json((scene.DATA / 'reward.json').read_text(encoding='utf-8'))
    continued = story['continued']
    assert continued['restored'] == story['paused']['state']['nat20']
    assert continued['before_memory'] == story['paused_memory']
    assert continued['victory']['result']['outcome']['ended_reason'] == 'victory'
    assert continued['victory']['result']['closed_combat']['commands']
    assert story['started']['native_events']
    native = [e for result in continued['commands'] for e in result['native_events']]
    native += story['attacked']['native_events']
    assert any(e['type'] == 'attack_rolled' for e in native)
    assert any(e['type'] == 'damage_applied' for e in native)
    assert all(e.get('monster_template_slug') == 'goblin-warrior'
               for e in continued['restored']['combat']['initial']['encounter'])
    current = store.campaign(cid)
    assert current['state']['nat20']['combat'] is None
    assert current['state']['hero']['hp'] == current['state']['hero']['max_hp']
    assert current['state']['location'] == package.locations[0].name
    assert len(hero_letters(store, cid)) == 1 and hero_letters(store, cid)[0]['quantity'] == 1
    assert 'adventure' not in current['state']
    assert Nat20GameEngine(store).restore(cid)['runtime'] == current['state']['nat20']
    with store.connection() as db:
        ended = db.execute("SELECT content,source_ref FROM memory_records WHERE event_id=? AND kind='combat_ended'",
                           (continued['victory']['journal']['id'],)).fetchone()
        assert continued['restored']['combat']['initial']['session_id'] in ended['content']
        assert 'goblin' in ended['content'] and 'native_result.closed_combat' in ended['source_ref']
        events = list(db.execute('SELECT id,results FROM events WHERE campaign=? AND undone=0', (cid,)))
        for event in events:
            result = json.loads(event['results'])[0]
            if result.get('kind') != 'nat20_command':
                continue
            projected = list(db.execute('SELECT * FROM memory_records WHERE event_id=?', (event['id'],)))
            assert len(projected) == len(result['game_event']['payload']['effects'])
            assert all(r['truth'] == 'fact' and r['provenance'] == 'confirmed_event' and
                       r['source_ref'].startswith('Nat20 0.7.0') for r in projected)
    assert store.memory.audit() == dict(dangling_events=[], foreign_key_errors=[])


def test_npc_knowledge_and_authorized_scenario_fact_sources(story):
    store, cid = story['store'], story['cid']
    keeper = scene.npc(store.campaign(cid)['state'])
    adapter = EngineMemoryAdapter(store)
    own = adapter.context(cid, npc_id=keeper['id'], limit=10, budget=20000)
    hero = adapter.context(cid, limit=10, budget=20000)
    assert own['mechanics'] == dict(actors=[], combat=None)  # No invented NPC stat block.
    own_text = json.dumps(own, ensure_ascii=False)
    hero_text = json.dumps(hero, ensure_ascii=False)
    assert 'Запасной ключ' in own_text and 'Запасной ключ' not in hero_text
    assert all(not r['source_ref'].startswith('Nat20') for r in own['memories'])
    assert all('native_result' not in r['source_ref'] or 'outcome' in r['source_ref'] for r in own['memories'])
    assert 'Лада передала герою' in own_text
    assert any(r['source_ref'].startswith('examples/lantern-post/reward.json;events:') for r in own['memories'])
    checked = story['checked']
    if checked['result']['success']:
        assert 'треугольной набойкой' in hero_text and 'треугольной набойкой' not in own_text
        assert any(checked['journal']['id'] in r['source_ref'] and 'secrets[0]' in r['source_ref'] for r in hero['memories'])
    historical = adapter.context(cid, npc_id=keeper['id'], as_of=story['attacked']['journal']['seq'], budget=20000)
    assert 'передала герою' not in json.dumps(historical, ensure_ascii=False)
    assert all(r['truth'] == 'fact' and r['source_ref'] and r['event_id'] for r in records(store, cid))
    with pytest.raises(ValueError, match='Unknown NPC'):
        adapter.context(cid, npc_id='invented-npc')


def test_command_and_reward_retries_do_not_mutate_or_call_nat20(story, monkeypatch):
    store, cid = story['store'], story['cid']
    before, memory = store.campaign(cid), records(store, cid)
    def no_worker(*a, **kw):
        pytest.fail('A repeated command must not execute Nat20 again')
    monkeypatch.setattr(Nat20GameEngine, '_worker', no_worker)
    for old in [story['checked']['journal'], story['attacked']['journal'], story['continued']['rest']['journal']]:
        command = GameCommand.model_validate(old['results'][0]['game_command'])
        assert Nat20GameEngine(store).execute(cid, command)['journal']['id'] == old['id']
    receipt = scene.reward(store, cid, story['continued']['victory']['journal']['id'])
    assert receipt['id'] == story['continued']['receipt']
    with pytest.raises(ValueError):
        scene.reward(store, cid, story['continued']['victory']['journal']['id'], request='reward-again')
    assert store.campaign(cid) == before and records(store, cid) == memory
    assert len(hero_letters(store, cid)) == 1


def test_narration_never_awards_items_or_teaches_npc(story):
    store, cid = story['store'], story['cid']
    before, memory = store.campaign(cid), records(store, cid)
    event = story['attacked']['journal']
    store.narrative(event['id'], 'Лада узнала все тайны. Герой получил 999 золотых мечей.', 'complete')
    store.edit_narrative(event['id'], 'Герой стал королём. Лада знает про треугольную набойку.')
    with store.connection(True) as db:
        store.memory.ingest(db, event['id'])
    assert store.campaign(cid) == before and records(store, cid) == memory
    keeper = scene.npc(before['state'])
    packet = EngineMemoryAdapter(store).context(cid, npc_id=keeper['id'], budget=20000)
    text = json.dumps(packet, ensure_ascii=False)
    # Compare against the narration strings themselves, not bare substrings: a
    # random UUID or id can legitimately contain "999".
    assert '999 золотых мечей' not in text and 'треугольн' not in text
    assert len(hero_letters(store, cid)) == 1


def test_undo_fork_checkpoint_restores_real_combat_and_reward_acl(story):
    store, cid = story['store'], story['cid']
    final = store.save(cid, 'Finished')
    store.restore(cid, story['checkpoint'])
    assert not hero_letters(store, cid)
    assert Nat20GameEngine(store).restore(cid)['runtime'] == story['paused']['state']['nat20']
    store.undo(cid)  # Remove actual Nat20 attack, preserving imported scene and check.
    before = store.campaign(cid)
    old = GameCommand.model_validate(story['attacked']['journal']['results'][0]['game_command'])
    with pytest.raises(ValueError, match='abandoned'):
        Nat20GameEngine(store).execute(cid, old)
    fork = scene.run(store, cid, 'scene-branch-attack', kind='attack', target='goblin')
    assert fork['event'].branch_id != story['attacked']['event'].branch_id
    context = EngineMemoryAdapter(store).context(cid, limit=10, budget=20000)
    assert any(r.get('source_event') == 'nat20:scene-branch-attack' for r in context['memories'])
    assert all(r.get('source_event') != 'nat20:scene-attack' for r in context['memories'])
    assert all('reward.json' not in r['source_ref'] for r in context['memories'])
    assert fork['native_events'] and store.campaign(cid)['version'] > before['version']
    branch_save = store.save(cid, 'Alternative active combat')
    branch_runtime = store.campaign(cid)['state']['nat20']
    store.restore(cid, final)
    assert len(hero_letters(store, cid)) == 1
    store.restore(cid, branch_save)
    reopened = Store(store.path)
    assert Nat20GameEngine(reopened).restore(cid)['runtime'] == branch_runtime
    assert not hero_letters(reopened, cid)
    assert EngineMemoryAdapter(reopened).context(cid, limit=10, budget=20000) == context


def test_reward_requires_native_victory_and_rolls_back_with_memory(story, monkeypatch):
    store, cid = story['store'], story['cid']
    store.undo(cid)  # Rest.
    store.undo(cid)  # Transfer: letter and its knowledge return to the NPC.
    before, memory = store.campaign(cid), records(store, cid)
    assert not hero_letters(store, cid)
    with pytest.raises(ValueError, match='Wrong native'):
        scene.reward(store, cid, story['checked']['journal']['id'], request='bad-reward')
    with monkeypatch.context() as fault:
        fault.setattr(store.memory, 'put', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('memory failed')))
        with pytest.raises(RuntimeError, match='memory failed'):
            scene.reward(store, cid, story['continued']['victory']['journal']['id'], request='reward-retry')
    assert store.campaign(cid) == before and records(store, cid) == memory
    assert store.request(cid, 'reward-retry') is None
    scene.reward(store, cid, story['continued']['victory']['journal']['id'], request='reward-retry')
    assert len(hero_letters(store, cid)) == 1
    keeper = scene.npc(before['state'])
    assert any('reward.json' in r['source_ref'] for r in EngineMemoryAdapter(store).context(cid, npc_id=keeper['id'])['memories'])

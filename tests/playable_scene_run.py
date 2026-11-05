"""Fixed trusted walkthrough for memory acceptance; no new game route or rules.

ScenarioPackage/Item data + existing scenario transaction + real Nat20 commands.
This harness is the explicit missing scene/encounter integration, not a world engine.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solo.engine_memory import EngineMemoryAdapter
from solo.inventory import InventoryEngine, Item
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
from solo.storage import Store
from solo.world import ScenarioPackage, import_package, observe

DATA = ROOT / 'examples' / 'lantern-post'
SOURCE = 'examples/lantern-post/scene.json'


def definition():
    return ScenarioPackage.model_validate_json((DATA / 'scene.json').read_text(encoding='utf-8'))


def evidence(text, known_by, ref, scope='private'):
    return dict(text=text, known_by=known_by, source_ref=ref, scope=scope,
                provenance='scenario', truth='fact', kind='event', importance=9)


def npc(state):
    return next(n for p in state['locations'] for n in p['npc'] if n['name'] == 'Лада')


def create(database):
    store = Store(database)
    cid = Nat20GameEngine(store).create_campaign('Письмо у старого фонаря', seed=5)['id']
    package = definition()
    def install(state):
        import_package(state, package)
        state['location'] = package.locations[0].name
        observe(state)
        keeper = npc(state)
        item = Item.model_validate_json((DATA / 'reward.json').read_text(encoding='utf-8'))
        item.owner = 'npc:' + keeper['id']
        inventory = InventoryEngine(state)
        inventory.data.items.append(item)
        inventory.flush()
        return [dict(type='scripted', kind='scene_install', memory_evidence=[
            evidence(keeper['known'], ['hero', keeper['name']], SOURCE + '#locations[0].npc[0].known'),
            evidence(keeper['private_knowledge'][0], [keeper['name']], SOURCE + '#locations[0].npc[0].private_knowledge[0]')])]
    store.record_scenario(cid, 'scene-install', 'Подтверждённый авторский модуль', install)
    return store, cid


def run(store, cid, request, **action):
    return Nat20GameEngine(store).execute(cid, GameCommand(
        request_id=request, expected_version=store.campaign(cid)['version'], action=action))


def native_source(store, cid, journal_id, kind):
    event = store.event(journal_id)
    if event['campaign'] != cid or event['undone']:
        raise ValueError('Inactive or foreign Nat20 source')
    result = event['results'][0]
    if result.get('kind') != 'nat20_command' or result['game_command']['action']['kind'] != kind:
        raise ValueError('Wrong native command source')
    return result


def enter_yard(store, cid, check_id):
    package = definition()
    def transition(state):
        if state['location'] != package.locations[0].name or state['nat20']['combat']:
            raise ValueError('Enter the yard only from the hut outside combat')
        result = native_source(store, cid, check_id, 'check')
        if result['game_command']['action']['check_id'] != 'perception':
            raise ValueError('The scene requires the registered perception check')
        memories = []
        if result['native_result']['success']:
            memories.append(evidence(package.locations[0].secrets[0], ['hero'],
                SOURCE + '#locations[0].secrets[0];events:' + check_id + '/results[0].native_result'))
        state['location'] = package.locations[1].name
        observe(state)
        memories.append(evidence('Герой вошёл во двор фонаря.', ['hero'], SOURCE + '#locations[1]'))
        return [dict(type='scripted', kind='scene_enter', memory_evidence=memories)]
    return store.record_scenario(cid, 'scene-enter', 'Переход через открытую калитку', transition)


def reward(store, cid, victory_id, request='scene-reward'):
    def transition(state):
        result = native_source(store, cid, victory_id, 'end_combat')
        if state['nat20']['combat'] or result['native_result']['outcome']['ended_reason'] != 'victory':
            raise ValueError('Reward requires a completed real Nat20 victory')
        if result['native_result']['closed_combat']['initial']['session_id'] != cid + ':scene-start':
            raise ValueError('Victory does not belong to this scene encounter')
        if state['location'] != definition().locations[1].name:
            raise ValueError('Return from the registered yard encounter')
        state['location'] = definition().locations[0].name
        observe(state)
        keeper = npc(state)
        inventory = InventoryEngine(state)
        item = inventory.transfer('lantern-thank-you-letter', 1, 'hero', owner='npc:' + keeper['id'],
                                  source='Подтверждённая награда; Nat20 victory event:' + victory_id)
        inventory.flush()
        return [dict(type='scripted', kind='scene_reward', item=item.id, memory_evidence=[
            evidence('Лада передала герою благодарственное письмо.', ['hero', keeper['name']],
                     'examples/lantern-post/reward.json;events:' + victory_id + '/results[0].native_result.outcome.ended_reason')])]
    return store.record_scenario(cid, request, 'Подтверждённая передача награды', transition)


def pause_in_combat(store, cid):
    checked = run(store, cid, 'scene-check', kind='check', check_id='perception')
    enter_yard(store, cid, checked['journal']['id'])
    started = run(store, cid, 'scene-start', kind='start_combat', encounter_id='basic')
    run(store, cid, 'scene-move', kind='move', position=dict(x=3, y=1, z=0))
    attack = run(store, cid, 'scene-attack', kind='attack', target='goblin')
    assert store.campaign(cid)['state']['nat20']['combat'] is not None
    return checked, started, attack


def resume(store, cid):
    restored = Nat20GameEngine(store).restore(cid)  # Actually replay through public Nat20 APIs.
    before_memory = EngineMemoryAdapter(store).context(cid, limit=10, budget=20000)
    commands = []
    for index in range(20):
        view = store.campaign(cid)['state']['nat20']['combat']['view']
        if view['tracked_hp']['goblin'] <= 0:
            break
        kind = 'attack' if view['initiative'][view['current_turn_index']]['entity_id'] == 'hero' else 'end_turn'
        action = dict(kind=kind, **(dict(target='goblin') if kind == 'attack' else {}))
        commands.append(run(store, cid, 'scene-finish-' + str(index), **action))
    else:
        raise AssertionError('Supported encounter did not complete in the bounded walkthrough')
    victory = run(store, cid, 'scene-end', kind='end_combat')
    receipt = reward(store, cid, victory['journal']['id'])
    rest = run(store, cid, 'scene-rest', kind='long_rest', food_water=False)
    return dict(restored=restored['runtime'], before_memory=before_memory,
                victory=wire_result(victory), receipt=receipt['id'], rest=wire_result(rest),
                commands=[wire_result(c) for c in commands])


def wire_result(reply):
    """Keep the actual wire results needed by acceptance assertions."""
    return dict(journal=reply['journal'], native_events=reply['native_events'], result=reply['result'])


def child(database, cid):
    process = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
                              '--resume', str(database), cid], cwd=ROOT,
                             text=True, encoding='utf-8', capture_output=True, timeout=150)
    if process.returncode:
        raise RuntimeError(process.stderr)
    return json.loads(process.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', nargs=2, metavar=('DATABASE', 'CAMPAIGN'))
    args = parser.parse_args()
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('Offline scene: network forbidden'))
    if args.resume:
        print(json.dumps(resume(Store(args.resume[0]), args.resume[1]), ensure_ascii=False))
        return
    with tempfile.TemporaryDirectory(prefix='solo-lantern-') as folder:
        store, cid = create(Path(folder) / 'scene.sqlite3')
        checked, _, _ = pause_in_combat(store, cid)
        completed = child(store.path, cid)
        current = store.campaign(cid)
        with store.connection() as db:
            facts = db.execute('SELECT COUNT(*) FROM memory_records WHERE campaign=?', (cid,)).fetchone()[0]
        print(json.dumps(dict(scene=definition().name, backend='Nat20 0.7.0', seed=5,
            check=checked['result'], victory=completed['victory']['result']['outcome']['ended_reason'],
            active_combat=current['state']['nat20']['combat'] is not None,
            hp=current['state']['hero']['hp'], reward=[dict(name=i['name'], quantity=i['quantity'])
                for i in current['state']['economy']['items'] if i['owner'] == 'hero'],
            facts=facts, restarted=True), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

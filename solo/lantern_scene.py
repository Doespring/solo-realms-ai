"""Existing authored scene attached to confirmed Nat20 events; no rule arithmetic."""
from pathlib import Path
from .inventory import InventoryEngine, Item
from .world import ScenarioPackage, import_package, observe

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'examples/lantern-post/scene.json'


def definition():
    return ScenarioPackage.model_validate_json((ROOT / SOURCE).read_text(encoding='utf-8'))


def evidence(text, known_by, reference):
    return dict(text=text, known_by=known_by, source_ref=reference, scope='private',
                provenance='scenario', truth='fact', kind='event', importance=9)


def keeper(state):
    return next(n for p in state['locations'] for n in p['npc'] if n['name'] == 'Лада')


def install(state):
    package = definition()
    import_package(state, package)
    state['location'] = package.locations[0].name
    observe(state)
    npc = keeper(state)
    item = Item.model_validate_json((ROOT / 'examples/lantern-post/reward.json').read_text(encoding='utf-8'))
    item.owner = 'npc:' + npc['id']
    inventory = InventoryEngine(state)
    inventory.data.items.append(item)
    inventory.flush()
    state['lantern_scene'] = dict(version=1, stage='arrival', letter_received=False)
    return [dict(type='scripted', kind='scene_install', text=package.locations[0].description,
                 memory_evidence=[evidence(npc['known'], ['hero', npc['name']], SOURCE+'#locations[0].npc[0].known'),
                                  evidence(npc['private_knowledge'][0], [npc['name']], SOURCE+'#locations[0].npc[0].private_knowledge[0]')])]


def guard(state, action):
    if state.get('lantern_scene') and action.kind == 'start_combat' and state['lantern_scene']['stage'] != 'checked':
        raise ValueError('Сначала осмотрите следы у калитки: проверка Внимательности. Затем можно войти во двор.')


def confirmed(state, action, response, event_id):
    scene = state.get('lantern_scene')
    if not scene:
        return []
    memories = []
    if action.kind == 'check' and action.check_id == 'perception' and scene['stage'] == 'arrival':
        scene['stage'] = 'checked'
        if response['result']['success']:
            memories.append(evidence(definition().locations[0].secrets[0], ['hero'], SOURCE+'#locations[0].secrets[0];events:'+event_id))
    elif action.kind == 'start_combat':
        scene['stage'] = 'combat'
        state['location'] = definition().locations[1].name
        observe(state)
        memories.append(evidence('Герой вошёл во двор фонаря.', ['hero'], SOURCE+'#locations[1]'))
    elif action.kind == 'end_combat' and response['result'].get('outcome', {}).get('ended_reason') == 'victory':
        if scene['letter_received']:
            raise ValueError('Письмо уже получено.')
        state['location'] = definition().locations[0].name
        observe(state)
        npc = keeper(state)
        inventory = InventoryEngine(state)
        inventory.transfer('lantern-thank-you-letter', 1, 'hero', owner='npc:'+npc['id'],
                           source='Подтверждённая награда; Nat20 victory event:'+event_id)
        inventory.flush()
        scene.update(stage='complete', letter_received=True)
        memories.append(evidence('Лада передала герою благодарственное письмо.', ['hero', npc['name']],
                                 'examples/lantern-post/reward.json;events:'+event_id))
    return memories


def conversation(state, text):
    if not state.get('lantern_scene') or state['lantern_scene']['stage'] not in ('arrival', 'checked', 'complete'):
        return None
    if text.strip().casefold().rstrip('.!?') not in ('поговорю с ладой', 'говорю с ладой', 'расспрашиваю ладу', 'осматриваю домик'):
        return None
    return definition().locations[0].npc[0].known if 'лад' in text.casefold() else definition().locations[0].description


def projection(state):
    if not state.get('lantern_scene'):
        return None
    stage = state['lantern_scene']['stage']
    instructions = {'arrival': 'Расспросите Ладу и осмотрите следы: проверка Внимательности.',
                    'checked': 'Следы осмотрены. Можно войти во двор и начать столкновение.',
                    'combat': 'Победите гоблина. «Второе дыхание» используйте до атаки. Сохраниться можно посреди боя.',
                    'complete': 'Победа подтверждена. Лада передала письмо; опыт начислен один раз. Можно отдохнуть.'}
    return dict(title=definition().name, stage=stage, description=instructions[stage],
                opening=definition().locations[0].description, letter_received=state['lantern_scene']['letter_received'])

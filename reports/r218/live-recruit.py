"""r218 live run: a real NPC of a real module becomes a companion, end to end.

No synthetic person: the scene comes from `module_scenes` of the adventure
library (the same rows the import cut from a PDF) processed by the same
`book_scene.scene_world` the campaign binder uses, then installed into a fresh
isolated campaign by `world.import_package`. From there everything goes through
the running app's own HTTP routes.

The library is copied first: the live profile's database is never written.

    .venv/bin/python reports/r218/live-recruit.py
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.book_scene import scene_world
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_wizard import suggested_build
from solo.storage import Store
from solo.world import import_package

LIBRARY = ROOT / 'runtime' / 'web-local' / 'adventure.sqlite3'


def scene_with(person_name):
    """The first module scene of the library that mentions this person."""
    db = sqlite3.connect(f'file:{LIBRARY}?mode=ro', uri=True)
    for module, body in db.execute('select module, body from module_scenes order by rowid'):
        scene = json.loads(body)
        if any(person_name in str(npc.get('name') or '') for npc in scene.get('npcs') or []):
            return module, scene
    raise SystemExit('нет сцены с ' + person_name)


def main():
    wanted = sys.argv[1] if len(sys.argv) > 1 else 'Капанук'
    module, scene = scene_with(wanted)
    package, rejected = scene_world('Глава 2: Лагерь налётчиков', scene)
    print('модуль:', module, '| страницы:', scene.get('pages'),
          '| мест:', len(package.locations), '| людей в пакете:',
          sum(len(place.npc) for place in package.locations))
    person_place = next((place.name for place in package.locations
                         if any(wanted in npc.name for npc in place.npc)), None)
    print(wanted, 'стоит в месте:', person_place)

    work = Path(tempfile.mkdtemp(prefix='r218-live-'))
    store = Store(work / 'live.sqlite3')
    api = Nat20GameEngine(store)
    hero = suggested_build(class_slug='fighter', level=1)
    cid = api.create_experimental_campaign('Живой найм', build=hero, encounters={
        'yard': dict(grid=dict(width=14, height=10), hero_position=dict(x=1, y=1, z=0),
                     actors=[dict(id='goblin', monster='goblin-warrior',
                                  position=dict(x=5, y=1, z=0))])}, title='Живой найм')['id']

    def install(state):
        state['location'] = person_place
        import_package(state, package)
        return [dict(type='scripted', text='Сцена книги установлена: ' + str(person_place))]

    store.record_scenario(cid, 'install-scene', 'Сцена книги', install)

    with TestClient(create_app(store.path, CommandMockLLM({}), experimental=True)) as client:
        listed = client.get(f'/api/campaigns/{cid}/party/candidates').json()
        mine = [row for row in listed if wanted in row['name']]
        print('кандидаты в месте:', len(listed), '|', wanted + ':',
              json.dumps(mine, ensure_ascii=False))
        joined = client.post(f'/api/campaigns/{cid}/party/npc',
                             json=dict(npc=mine[0]['id'], reason='rescued'))
        assert joined.status_code == 200, joined.text
        member = joined.json()['member']
        party = client.get(f'/api/campaigns/{cid}').json()['party']
        joined_row = next(row for row in party if row['id'] == member)
        print('в партии:', [row['name'] for row in party])
        print('лист спутника:', json.dumps({key: joined_row[key] for key in
                                           ('level', 'classes', 'hp_max', 'armor_class' if 'armor_class' in joined_row else 'ac')},
                                          ensure_ascii=False))
        print('происхождение:', json.dumps(joined_row['origin'], ensure_ascii=False))

    event = [row for row in store.history(cid) if 'вступает в партию' in str(row.get('results'))]
    print('журнал:', json.dumps(event[-1]['results'], ensure_ascii=False)[:700])

    opened = api.execute(cid, GameCommand(request_id='fight', expected_version=store.campaign(cid)['version'],
                                          action=dict(kind='start_combat', encounter_id='yard')))
    view = store.campaign(cid)['state']['nat20']['combat']['view']
    print('инициатива:', json.dumps({row['entity_id']: row['initiative'] for row in view['initiative']},
                                    ensure_ascii=False), '| party_ids:', view['party_ids'])
    turns = []
    for index in range(4):
        if not store.campaign(cid)['state']['nat20'].get('combat'):
            break
        result = api.execute(cid, GameCommand(request_id='turn-' + str(index),
                                              expected_version=store.campaign(cid)['version'],
                                              action=dict(kind='end_turn')))
        turns += (result.get('result') or {}).get('companion_turns') or []
    print('ходов спутника:', len(turns))
    for turn in turns[:2]:
        print('  ход:', turn.get('name'), '| действия:',
              [(action.get('kind'), action.get('id') or action.get('feature'))
               for action in turn.get('actions') or []])

    client_note = [row['text'] for row in
                   [dict(text=r.get('text')) for row in store.history(cid) for r in row.get('results') or []]
                   if row['text'] and 'партию' in row['text']]
    print('лента:', client_note[-2:])


if __name__ == '__main__':
    main()

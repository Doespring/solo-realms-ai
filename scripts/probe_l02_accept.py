"""Живая проба L02: слово «играем так» на сервере с experimental=True (как UI-тест)."""
import os
os.environ['SOLO_TEST'] = '1'
import json, socket, threading, time
from pathlib import Path
import tempfile

import uvicorn
from fastapi.testclient import TestClient

from solo import level_gate
from solo.app import create_app
from solo.nat20_api import Nat20GameEngine
from solo.nat20_wizard import suggested_draft
from solo.preview_mock import PreviewMock

SCORES = dict(strength=15, dexterity=14, constitution=13, intelligence=12, wisdom=10, charisma=8)
folder = Path(tempfile.mkdtemp())
app = create_app(folder / 'probe.sqlite3', PreviewMock(), experimental=True, offline_mock=True)
store = app.state.store
draft, state = suggested_draft(class_slug='fighter', species_slug='human',
                               background_slug='soldier', level=1, ability_scores=SCORES)
assert state['ready'], state['issues']
api = Nat20GameEngine(store)
actors = [dict(id='kobold-%d' % i, monster='kobold-warrior', position=dict(x=3 + i, y=1, z=0)) for i in range(8)]
cid = api.create_experimental_campaign('Тест Тиамат 08.11', build=state['build'],
                                       encounters={'first-encounter': dict(grid=dict(width=14, height=10, blocked_cells=[]),
                                                                           hero_position=dict(x=1, y=1, z=0), actors=actors)}, seed=7)['id']
def install(s):
    level_gate.install_requirement(s, book='Тиамат', minimum=8, maximum=8, party=4)
    return [dict(type='scripted', kind='level_gate', text='Книга «Тиамат» рассчитана на 8-й уровень.')]
store.record_scenario(cid, 'book-requirement', 'Требование книги', install)

client = TestClient(app)
current = store.campaign(cid)
print('gate before:', json.dumps(level_gate.projection(current['state']), ensure_ascii=False)[:300])
r = client.post('/api/campaigns/%s/command-turn' % cid,
                json=dict(request_id='probe-accept', text='играем так', expected_version=current['version']))
print('status', r.status_code)
body = r.json()
print('keys', sorted(body))
print('status field', body.get('status'))
print('narrative', str(body.get('narrative'))[:400])
snap = body.get('snapshot') or {}
print('snapshot level_gate', json.dumps(snap.get('level_gate'), ensure_ascii=False)[:400])
after = store.campaign(cid)['state']
print('balance_override', after.get('balance_override'), 'turn', after.get('turn'))
print('actors', len(after['nat20']['definition']['encounters']['first-encounter']['actors']))
screen = client.get('/api/campaigns/%s/screen' % cid)
print('screen', screen.status_code, json.dumps((screen.json().get('snapshot') or {}).get('level_gate'), ensure_ascii=False)[:300] if screen.status_code == 200 else screen.text[:300])

# Приёмка «бой не смертелен на первом ходу по числам»: открываем первый бой
# после «играем так» и печатаем, кто вышел против героя и сколько у него хитов.
from solo.game_commands import GameCommand
fresh = store.campaign(cid)
fight = api.execute(cid, GameCommand(request_id='probe-first-fight', expected_version=fresh['version'],
                                      action=dict(kind='start_combat', encounter_id='first-encounter')))
runtime = store.campaign(cid)['state']['nat20']
view = (runtime.get('combat') or {}).get('view') or {}
hero = next((row for row in view.get('initiative') or [] if row.get('entity_id') == 'hero'), {})
foes = [row for row in view.get('initiative') or [] if row.get('entity_id') != 'hero']
print('fight active', fight['snapshot'].combat.active if fight['snapshot'].combat else None,
      'hero hp', hero.get('hp_current'), '/', hero.get('hp_max'),
      'foes', [(row.get('name'), row.get('hp_max')) for row in foes])


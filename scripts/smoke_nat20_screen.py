"""Persistent, disposable local UI fixture; restart this process to test resume.

No production routes/credentials are changed. Example:
  .venv/Scripts/python.exe -B scripts/smoke_nat20_screen.py --port 19174
"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['SOLO_TEST'] = '1'

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine


def make_app(port):
    folder = ROOT / '.test-tmp-nat20-screen-smoke'
    folder.mkdir(exist_ok=True)
    actions = {
        'Проверяю атлетику': dict(kind='check', check_id='athletics'),
        'Начинаю бой': dict(kind='start_combat', encounter_id='basic'),
        'Подхожу к гоблину': dict(kind='move', position=dict(x=3, y=1, z=0)),
        'Атакую гоблина': dict(kind='attack', target='goblin'),
        'Заканчиваю ход': dict(kind='end_turn'), 'Завершаю бой': dict(kind='end_combat'),
        'Короткий отдых': dict(kind='short_rest', hit_dice=1),
        'Длительный отдых': dict(kind='long_rest', food_water=True),
        'Осматриваю следы': dict(kind='check', check_id='следы')}
    model = CommandMockLLM({text: dict(status='command', action=action, message='') for text, action in actions.items()})
    app = create_app(folder / 'solo.sqlite3', model, local_port=port)
    store = app.state.store
    if not store.list_campaigns():
        Nat20GameEngine(store).create_campaign('Nat20 · проверка экрана', seed=5, hp_current=7, second_wind_spent=1)
        state = dict(location='Каменный двор', turn=0, facts=[], deferred=[], locations=[dict(
            name='Каменный двор', description='У ворот видны следы.', npc=[], objects=['следы'], facts=[],
            checks={'следы': dict(ability='wisdom', skill='survival', dc=12)})])
        store.create('Solo · проверка совместимости', Hero(name='Мирон'), initial_state=state)
    return app


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=19174)
    args = parser.parse_args()
    uvicorn.run(make_app(args.port), host='127.0.0.1', port=args.port, log_level='warning', access_log=False)

"""A07: свой стенд для приёмки фокуса — обычная и экспериментальная кампании.

Живой профиль и порт 19215 не трогаются: своя база в runtime/a07-*, свободный
порт, mock-модель без сети и без платных вызовов.

    .venv/bin/python scripts/a07_accept_server.py runtime/a07-stand

Печатает JSON с портом и id кампаний, затем работает до остановки процесса.
`experimental` нужен селектору действия: `/campaigns/{id}/prototype` отдаёт
список действий только для экспериментальной сборки (иначе `actions: []`).
"""
import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
work = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'runtime' / 'a07')
work.mkdir(parents=True, exist_ok=True)
log = work / 'actions.jsonl'
log.write_text('', encoding='utf-8')
os.environ['SOLO_ACTIONS_LOG'] = str(log)
os.environ['SOLO_TEST'] = '1'

from solo.app import create_app                    # noqa: E402
from solo.llm import CommandMockLLM                # noqa: E402
from solo.nat20_api import Nat20GameEngine         # noqa: E402
from nat20_builds import legal                     # noqa: E402

llm = CommandMockLLM({})
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
app = create_app(work / 'a07.sqlite3', llm, local_port=port, experimental=True)
store = app.state.store
engine = Nat20GameEngine(store)

plain = engine.create_campaign('Тайна трёх сосен', seed=7)['id']
build = legal(class_slug='fighter', species_slug='human', level=3,
              subclass_slug='champion',
              equipment=['longsword', 'chain-mail', 'shield'])
encounters = {'yard': {
    'grid': dict(width=7, height=5, cell_size_ft=5, cover_cells={}),
    'hero_position': dict(x=1, y=1, z=0),
    'actors': [dict(id='foe', monster='bugbear-stalker', position=dict(x=3, y=2, z=0))]}}
experimental = engine.create_experimental_campaign(
    'Двор у мельницы', build=build, encounters=encounters, seed=11,
    title='Двор', description='Открытый двор у мельницы.')['id']

payload = dict(port=port, plain=plain, experimental=experimental, log=str(log))
(work / 'server.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
print(json.dumps(payload, ensure_ascii=False), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False)).run(sockets=[sock])

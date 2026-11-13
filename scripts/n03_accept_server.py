"""n03: свой инстанс приложения для приёмки экрана игры (лента + одно поле ввода).

Живой профиль и порт 19215 не трогаются: своя база в runtime/n03, свободный порт,
mock-модель без сети и без платных вызовов, свой журнал действий.

Печатает JSON с портом и id кампании, затем работает до остановки процесса.

    .venv/bin/python scripts/n03_accept_server.py runtime/n03
"""
import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
work = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'runtime' / 'n03')
work.mkdir(parents=True, exist_ok=True)
log = work / 'actions.jsonl'
log.write_text('', encoding='utf-8')
os.environ['SOLO_ACTIONS_LOG'] = str(log)
os.environ['SOLO_TEST'] = '1'

from solo.app import create_app          # noqa: E402 — путь журнала читается при сборке app
from solo.llm import CommandMockLLM      # noqa: E402
from solo.nat20_api import Nat20GameEngine  # noqa: E402

llm = CommandMockLLM({'Осматриваю зал и стойку': dict(status='command',
                                                      action=dict(kind='check', check_id='perception'),
                                                      message='')})
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
# Как Web.cmd: experimental=True. Иначе маршрут /api/campaigns/<id>/prototype
# не зарегистрирован, и браузер рисует 404, которого в живом приложении нет.
app = create_app(work / 'n03.sqlite3', llm, local_port=port, experimental=True)
store = app.state.store
campaign = Nat20GameEngine(store).create_campaign('n03 · экран игры: лента и поле', seed=7)

(work / 'server.json').write_text(json.dumps(dict(port=port, campaign=campaign['id'], log=str(log)),
                                             ensure_ascii=False), encoding='utf-8')
print(json.dumps(dict(port=port, campaign=campaign['id'], log=str(log)), ensure_ascii=False), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False)).run(sockets=[sock])

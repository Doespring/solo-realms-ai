"""n01: свой инстанс приложения для приёмки «двойное нажатие и отказ по версии».

Живой профиль и его порт не трогаются: своя база во временном каталоге, mock-модель,
свободный порт и свой журнал действий (`SOLO_ACTIONS_LOG`). Печатает JSON с портом
и id кампании, затем работает до остановки процесса.

    .venv/bin/python scripts/n01_accept_server.py runtime/n01
"""
import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
work = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'runtime' / 'n01')
work.mkdir(parents=True, exist_ok=True)
log = work / 'actions.jsonl'
log.write_text('', encoding='utf-8')
os.environ['SOLO_ACTIONS_LOG'] = str(log)
os.environ['SOLO_TEST'] = '1'

from solo.app import create_app          # noqa: E402 — путь журнала читается при сборке app
from solo.llm import CommandMockLLM      # noqa: E402
from solo.nat20_api import Nat20GameEngine  # noqa: E402

# Модель явная и офлайн: текст хода разбирается эталонным планом, иначе
# MockLLM без плана отвечает ValueError — сервер честно отдаёт 409, и прогон
# «двойное нажатие» считал бы отказы интерпретатора, а не поведение клиента.
llm = CommandMockLLM({'Осматриваю калитку': dict(status='command',
                                                action=dict(kind='check', check_id='athletics'),
                                                message='')})
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
app = create_app(work / 'n01.sqlite3', llm, local_port=port)
store = app.state.store
campaign = Nat20GameEngine(store).create_campaign('n01 · двойное нажатие и версия', seed=7)

(work / 'server.json').write_text(json.dumps(dict(port=port, campaign=campaign['id'], log=str(log)),
                                             ensure_ascii=False), encoding='utf-8')
print(json.dumps(dict(port=port, campaign=campaign['id'], log=str(log)), ensure_ascii=False), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False)).run(sockets=[sock])

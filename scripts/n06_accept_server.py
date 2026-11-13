"""n06: свой инстанс приложения для приёмки меню (полоса ПК и шторка).

Живой профиль и порт 19215 не трогаются: своя база в runtime/n06-*, свободный
порт, mock-модель без сети и без платных вызовов.

    .venv/bin/python scripts/n06_accept_server.py runtime/n06-live

Печатает JSON с портом и работает до остановки процесса.
"""
import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
work = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'runtime' / 'n06')
work.mkdir(parents=True, exist_ok=True)
log = work / 'actions.jsonl'
log.write_text('', encoding='utf-8')
os.environ['SOLO_ACTIONS_LOG'] = str(log)
os.environ['SOLO_TEST'] = '1'

from solo.app import create_app          # noqa: E402 — путь журнала читается при сборке app
from solo.llm import CommandMockLLM      # noqa: E402

llm = CommandMockLLM({})
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
# Как Web.cmd: experimental=True. Иначе маршруты Nat20 не зарегистрированы.
app = create_app(work / 'n06.sqlite3', llm, local_port=port, experimental=True)

(work / 'server.json').write_text(json.dumps(dict(port=port, log=str(log)), ensure_ascii=False),
                                 encoding='utf-8')
print(json.dumps(dict(port=port, log=str(log)), ensure_ascii=False), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False)).run(sockets=[sock])

"""n02: свой инстанс приложения для приёмки «коды отказов на чтении».

Живой профиль и его порт не трогаются: база кампаний копируется в собственный
каталог `runtime/n02/accept` (одна копия на запуск), модель офлайн, порт
свободный, журнал действий свой (`SOLO_ACTIONS_LOG`). В копию добавляется
кампания прежней версии (`legacy-n02`), чтобы отказы 409 и 404 можно было
показать живьём, а не только тестом.

    .venv/bin/python scripts/n02_accept_server.py [каталог]

Печатает JSON с портом, id своих кампаний и путём журнала, затем работает до
остановки процесса.
"""
import json
import os
import shutil
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
work = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'runtime' / 'n02' / 'accept')
work.mkdir(parents=True, exist_ok=True)
# Профиль только читается: кампании копируются, живые файлы остаются на месте.
source = ROOT / 'runtime' / 'web-local' / 'solo.sqlite3'
database = work / 'solo.sqlite3'
if source.is_file():
    shutil.copy2(source, database)
log = work / 'actions.jsonl'
log.write_text('', encoding='utf-8')
os.environ['SOLO_ACTIONS_LOG'] = str(log)
os.environ['SOLO_TEST'] = '1'

from solo.app import create_app          # noqa: E402 — путь журнала читается при сборке app
from solo.llm import CommandMockLLM      # noqa: E402

llm = CommandMockLLM({'Осматриваю калитку': dict(status='command',
                                                action=dict(kind='check', check_id='athletics'),
                                                message='')})
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
app = create_app(database, llm, local_port=port)
store = app.state.store

# Кампания прежней версии: запись есть, признака nat20 в состоянии нет.
with store.connection(True) as db:
    db.execute("DELETE FROM campaigns WHERE id='legacy-n02'")
    db.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?)',
               ('legacy-n02', 'Старая кампания (прежняя версия)',
                json.dumps({'hero': {'name': 'Старый герой'}}, ensure_ascii=False),
                json.dumps({'profile_name': 'ГМ'}, ensure_ascii=False), 0, 0))

native = [c['id'] for c in store.list_campaigns() if c['backend'] == 'nat20']
info = dict(port=port, native=native[:5], legacy='legacy-n02', missing='00000000-0000-0000-0000-000000000000',
            database=str(database), log=str(log))
(work / 'server.json').write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
print(json.dumps(info, ensure_ascii=False), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False)).run(sockets=[sock])

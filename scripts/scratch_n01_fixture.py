"""Отладка n01: тот же инстанс, что в tests/test_conflict_recovery_ui.py, но снаружи.

    PYTHONPATH=. .venv/bin/python scripts/scratch_n01_fixture.py
"""
import json
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM

work = Path(sys.argv[1] if len(sys.argv) > 1 else 'runtime/n01-fixture')
work.mkdir(parents=True, exist_ok=True)
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
app = create_app(work / 'fixture.sqlite3', MockLLM(), local_port=port)
campaign = app.state.store.create('Conflict · UI fixture', Hero(name='Мирон'))
server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
thread.start()
while not server.started and thread.is_alive():
    time.sleep(.05)
print(json.dumps({'port': port, 'campaign': campaign['id']}, ensure_ascii=False), flush=True)
thread.join()

"""A07/F12: фокус в диалогах — страж на живом экране (приёмка Q19).

Стенд свой: временная база, свободный порт, mock-модель без сети. Живой
профиль и порт приложения не трогаются. Кампаний две: обычная (карточка
предмета в инвентаре) и экспериментальная (панель действий на экране).
"""
import os

os.environ.setdefault('SOLO_TEST', '1')

from pathlib import Path
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from nat20_builds import legal


def test_dialog_focus_is_trapped_and_returned(tmp_path, monkeypatch):
    node = os.environ.get('SOLO_UI_NODE') or ''
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or ''
    if not Path(node).is_file() or not Path(playwright).exists():
        pytest.skip('Local browser runtime required')
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    app = create_app(tmp_path / 'a07.sqlite3', CommandMockLLM({}), local_port=port,
                     experimental=True, offline_mock=True)
    engine = Nat20GameEngine(app.state.store)
    plain = engine.create_campaign('Тайна трёх сосен', seed=7)['id']
    build = legal(class_slug='fighter', species_slug='human', level=3,
                  subclass_slug='champion',
                  equipment=['longsword', 'chain-mail', 'shield'])
    scene = {'yard': {
        'grid': dict(width=7, height=5, cell_size_ft=5, cover_cells={}),
        'hero_position': dict(x=1, y=1, z=0),
        'actors': [dict(id='foe', monster='bugbear-stalker', position=dict(x=3, y=2, z=0))]}}
    experimental = engine.create_experimental_campaign(
        'Двор у мельницы', build=build, encounters=scene, seed=11,
        title='Двор', description='Открытый двор у мельницы.')['id']

    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [node, str(Path(__file__).with_suffix('.cjs')), f'http://127.0.0.1:{port}', str(tmp_path)],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': playwright,
                 'SOLO_CAMPAIGN_PLAIN': plain, 'SOLO_CAMPAIGN_EXPERIMENTAL': experimental},
            capture_output=True, text=True, encoding='utf-8', timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True; thread.join(10); sock.close(); assert not thread.is_alive()

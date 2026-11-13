"""N2: шапка игры и док на телефоне и на столе — живой браузер.

Проверяется то, что снял критик со сборки 77b88613:

  • название кампании в шапке больше не обрезается (`scrollWidth ≤ clientWidth`
    для трёх названий из фикстуры) и занимает не меньше 100 px;
  • под названием стоит вторая строка — глава и место («глава II · трактир»
    в макете `game`);
  • рамка поля ввода стоит в 16 px от краёв экрана на 375;
  • с клавиатурой между рамкой и клавиатурой 8 px;
  • на 1440 рамка поля совпадает по ширине и правому краю с колонкой ленты;
  • зазор «текст → поле» не больше 100 px на 375.

Числа до правки — `reports/design-review/n17-game-layout.json` (`n17-before`),
после — тот же файл с тегом `n17`; проба `scripts/probe_n2_layout.py`.

Сервер живой, кампания настоящая (Nat20-движок, mock-Мастер: платных вызовов
нет). Подмен нет: шапка и док считаются тем же кодом, что у игрока.
"""
import os

os.environ['SOLO_TEST'] = '1'

from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine


def _browser_runtime():
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node')
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT')
    if not playwright:
        candidates = sorted(Path.home().glob('.npm/_npx/*/node_modules/playwright'))
        playwright = str(candidates[0]) if candidates else ''
    browsers = sorted(Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux64/chrome'))
    browser = os.environ.get('SOLO_UI_BROWSER') or (str(browsers[-1]) if browsers else '')
    return node, playwright, browser


def _browser_env(playwright: str, browser: str) -> dict:
    """Окружение для node-пробы.

    Headless Chromium этой машины живёт без системных GTK/ATK-пакетов: их
    библиотеки сложены в `~/.hermes/chromium-deps`, и без `LD_LIBRARY_PATH`
    запуск падает `error while loading shared libraries: libatk-1.0.so.0`
    (браузер не стартует вовсе, и тест выглядит как поломка экрана).
    """
    env = {**os.environ, 'SOLO_UI_PLAYWRIGHT': str(playwright), 'SOLO_UI_BROWSER': browser}
    deps = Path.home() / '.hermes/chromium-deps/usr/lib/x86_64-linux-gnu'
    if deps.is_dir():
        env['LD_LIBRARY_PATH'] = str(deps) + (':' + env['LD_LIBRARY_PATH']
                                             if env.get('LD_LIBRARY_PATH') else '')
    return env


def test_n2_game_header_and_composer(tmp_path):
    node, playwright, browser = _browser_runtime()
    if not node or not Path(node).is_file() or not playwright or not Path(playwright).exists():
        pytest.skip('нужен локальный Node/Playwright (SOLO_UI_NODE, SOLO_UI_PLAYWRIGHT)')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'n2-header.sqlite3', CommandMockLLM({}), local_port=port,
                     experimental=True)
    campaign = Nat20GameEngine(app.state.store).create_campaign('Мирон', seed=17)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [str(node), str(Path(__file__).with_suffix('.cjs')),
             f'http://127.0.0.1:{port}', campaign['id'], str(tmp_path)],
            env=_browser_env(playwright, browser),
            capture_output=True, text=True, encoding='utf-8', timeout=300,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()
        assert not thread.is_alive()

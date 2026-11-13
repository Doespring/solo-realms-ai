"""Живой браузер: сверка уровня героя с книгой на старте кампании (L02).

Сценарий владельца: книга рассчитана на 8-й уровень, герой собран на 1-м.
До первого броска Мастер называет оба числа и предлагает пересборку; слово
«играем так» фиксирует решение в кампании и меняет состав первого боя, а
слово «пересобрать героя» открывает редактор на книжном уровне.

Сервер живой, кампания настоящая. Подменены только два ответа, которых в
песочнице нет: контекст книги для редактора (черновика «Тиамат» здесь нет)
и сам ответ на слово «пересобрать» — маршрут пересборки проверен отдельно,
`tests/test_level_gate.py::test_the_rebuild_route_replaces_the_hero_without_starting_the_story`.
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

from solo import level_gate
from solo.app import create_app
from solo.nat20_api import Nat20GameEngine
from solo.nat20_wizard import suggested_draft
from solo.preview_mock import PreviewMock

FIGHTER_SCORES = dict(strength=15, dexterity=14, constitution=13,
                      intelligence=12, wisdom=10, charisma=8)
KOBOLD = 'kobold-warrior'


def _browser_runtime():
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node')
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT')
    if not playwright:
        candidates = sorted(Path.home().glob('.npm/_npx/*/node_modules/playwright'))
        playwright = str(candidates[0]) if candidates else ''
    browsers = sorted(Path.home().glob('.hermes/tools/chromium-*/chrome-linux64/chrome'))
    browser = os.environ.get('SOLO_UI_BROWSER') or (str(browsers[-1]) if browsers else '')
    return node, playwright, browser


def _book_campaign(app):
    """Кампания по книге: герой 1-го, книга просит 8-й, бой на отряд героев."""
    draft, state = suggested_draft(class_slug='fighter', species_slug='human',
                                   background_slug='soldier', level=1,
                                   ability_scores=FIGHTER_SCORES)
    assert state['ready'], state['issues']
    api = Nat20GameEngine(app.state.store)
    actors = [dict(id='kobold-' + str(index), monster=KOBOLD,
                   position=dict(x=3 + index, y=1, z=0)) for index in range(8)]
    campaign = api.create_experimental_campaign(
        'Тест Тиамат 08.11', build=state['build'],
        encounters={'first-encounter': dict(grid=dict(width=14, height=10, blocked_cells=[]),
                                            hero_position=dict(x=1, y=1, z=0),
                                            actors=actors)}, seed=7)['id']

    def install(state):
        level_gate.install_requirement(state, book='Тиамат', minimum=8, maximum=8, party=4)
        return [dict(type='scripted', kind='level_gate',
                     text='Книга «Тиамат» рассчитана на 8-й уровень.')]

    api.store.record_scenario(campaign, 'book-requirement', 'Требование книги', install)
    return campaign


def test_the_gate_names_both_numbers_and_the_word_igraem_tak_changes_the_campaign(tmp_path, monkeypatch):
    node, playwright, browser = _browser_runtime()
    if not (node and Path(playwright).exists() and Path(browser).is_file()):
        pytest.skip('Local browser runtime required')
    original = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in ('127.0.0.1', '::1'), 'External calls forbidden'
        return original(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(tmp_path / 'level-gate-ui.sqlite3', PreviewMock(), local_port=port,
                     experimental=True, offline_mock=True)
    campaign = _book_campaign(app)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        result = subprocess.run(
            [str(node), str(Path(__file__).with_suffix('.cjs')),
             f'http://127.0.0.1:{port}', str(tmp_path), campaign],
            env={**os.environ, 'SOLO_UI_PLAYWRIGHT': playwright,
                 'SOLO_UI_BROWSER': browser},
            capture_output=True, text=True, encoding='utf-8', timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(30)
        sock.close()
        assert not thread.is_alive()
    # Слово «играем так» — факт кампании, а не ответ модели.
    state = app.state.store.campaign(campaign)['state']
    assert state['balance_override'] == 'player_level'
    assert len(state['nat20']['definition']['encounters']['first-encounter']['actors']) <= 1

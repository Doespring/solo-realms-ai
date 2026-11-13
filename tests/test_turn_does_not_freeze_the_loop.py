"""r208: ход с механикой не замораживает остальной сервер.

Замер пробы (`scripts/probe_r208_freeze.py`, 03.11.2026) до правки: пока шёл
ход «проверяю внимательность», посторонний `GET /api/campaigns/<id>` ждал
1,97 с вместо 11 мс — весь сервер стоял на время подпроцесса движка. Причина
в коде: `Game.play_command` — корутина, а вызов движка (`Nat20GameEngine.execute`,
внутри которого `subprocess.run` на 1,5–2,5 с) шёл прямо в событийном цикле.
Экранный путь (`playable_ui.command`) тот же вызов уже уводил в поток.

Вызовов в корутине было ДВА, и они разные: общий (намерение после
интерпретатора) и отдельная ветка печатной проверки книги («Осматриваю
калитку», «проверяю следы»), которая разрешается до модели. Первый критик
r208 нашёл вторую: правка только общего вызова оставляла заморозку в живых на
любой печатной проверке. Тест держит оба пути.

Тест не меряет секунды игрока (это делает проба) и не подменяет движок
заглушкой: настоящий воркер вызывается, только с задержкой впереди, чтобы
блокировка была видна даже на быстрой машине.
"""
import asyncio
import time
from pathlib import Path

import pytest

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine

TEXT = 'Осматриваю калитку'
PRINTED = 'Проверяю внимательность у калитки'


@pytest.fixture
def native(tmp_path):
    from solo.storage import Store
    store = Store(tmp_path / 'loop.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('Цикл · Мирон', seed=7)['id']
    model = CommandMockLLM({TEXT: dict(status='command',
                                       action=dict(kind='check', check_id='perception'),
                                       message='')})
    return store, cid, create_app(store.path, model)


def slow_engine(monkeypatch):
    """Движок, заметно медленный, с флагом «внутри движка»."""
    inside = {'engine': False}
    original = Nat20GameEngine._worker

    def slow_worker(payload):
        inside['engine'] = True
        try:
            time.sleep(0.5)
            return original(payload)
        finally:
            inside['engine'] = False

    monkeypatch.setattr(Nat20GameEngine, '_worker', staticmethod(slow_worker))
    return inside


def ticks_during_turn(game, cid, text, version, inside):
    """Сколько раз цикл успел проснуться, пока работал движок."""
    async def scenario():
        ticks = 0

        async def player():
            return await game.play_command(cid, 'r208-loop-' + str(version), text, version)

        turn = asyncio.create_task(player())
        while not turn.done():
            if inside['engine']:
                ticks += 1
            await asyncio.sleep(0.01)
        return ticks, await turn

    return asyncio.run(scenario())


def test_a_mechanics_turn_keeps_the_event_loop_running(native, monkeypatch):
    """Общий путь: намерение разобрано моделью, команда посчитана движком."""
    store, cid, app = native
    game = app.state.game
    inside = slow_engine(monkeypatch)
    version = store.campaign(cid)['version']

    ticks, packet = ticks_during_turn(game, cid, TEXT, version, inside)
    assert packet['status'] == 'resolved', packet
    # До правки цикл стоял всё время работы движка и счётчик оставался нулём.
    assert inside['engine'] is False
    assert ticks >= 5, 'событийный цикл стоял, пока работал движок: тиков %d' % ticks
    assert store.campaign(cid)['version'] == version + 1


def test_a_printed_check_does_not_freeze_the_event_loop(native, monkeypatch):
    """Отдельная ветка: проверка книги разрешается до модели (game.py, строка 841)."""
    store, cid, app = native
    game = app.state.game
    import solo.source_checks as source_checks
    monkeypatch.setattr(source_checks, 'match', lambda state, text: 'perception')
    inside = slow_engine(monkeypatch)
    version = store.campaign(cid)['version']

    ticks, packet = ticks_during_turn(game, cid, PRINTED, version, inside)
    assert packet['status'] == 'resolved', packet
    assert packet['command']['action']['kind'] == 'check', packet
    assert inside['engine'] is False
    assert ticks >= 5, 'ветка печатной проверки стояла в цикле: тиков %d' % ticks
    assert store.campaign(cid)['version'] == version + 1


def test_both_live_routes_call_the_engine_off_the_loop():
    """Экранные маршруты того же движка тоже уводят вызов в поток."""
    root = Path(__file__).resolve().parents[1] / 'solo'
    assert 'asyncio.to_thread(backend.execute' in (root / 'playable_ui.py').read_text(encoding='utf-8')
    assert 'asyncio.to_thread(command_backend(store,current).execute' in (
        root / 'play_screen.py').read_text(encoding='utf-8')

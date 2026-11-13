"""Дочерний сервер пробы r208: одно приложение на профиле + послойный замер хода.

Запускается только из `scripts/probe_r208_turns.py`. Отдельный процесс нужен
по двум причинам: (1) профиль пробы обнуляется между прогонами без перезапуска
интерпретатора, который уже загрузил движок; (2) модуль `solo` из `.venv`
импортируется один раз, как у живого сервера.

Переменные окружения:
    SOLO_CHILD_DB     путь к sqlite-файлу профиля;
    SOLO_CHILD_PORT   порт на 127.0.0.1;
    SOLO_CHILD_STOP   токен: как только файл появился, сервер закрывается;
    SOLO_R208_TIMES   путь к JSONL, куда после каждого хода пишется замер.

Замер — `time.perf_counter()` вокруг вызовов, без cProfile: обёртка стоит доли
микросекунды и не искажает числа, которые увидит игрок. Границы хода —
`Game.play_command`; слои — те функции, которые ход действительно зовёт.

Вложенность настоящая, и в отчёте она названа:
    engine_execute ⊃ engine_event ⊃ worker   (вызов движка в подпроцессе)
    engine_event ⊃ memory_ingest             (память события)
    context ⊃ memory_context, capabilities   (пакет для интерпретатора)
    turn_total ⊃ всё перечисленное
"""
import functools
import json
import os
import threading
import time
from pathlib import Path

os.environ['SOLO_TEST'] = '1'
import sys  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACC = {}
_LOCK = threading.Lock()
_TIMES_PATH = os.environ.get('SOLO_R208_TIMES', '')
_TURN = {'number': 0, 'kind': ''}
_SIZES = {'worker_in': 0, 'worker_out': 0, 'calls': 0}


def _add(label, seconds):
    ACC[label] = ACC.get(label, 0.0) + seconds


def timed(label, function):
    """Обёртка синхронного вызова: копит время в ACC под именем label."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            _add(label, time.perf_counter() - started)
    return wrapper


def timed_async(label, function):
    """То же для `async def`."""
    @functools.wraps(function)
    async def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return await function(*args, **kwargs)
        finally:
            _add(label, time.perf_counter() - started)
    return wrapper


def timed_async_gen(label, function):
    """Обёртка асинхронного генератора (рассказчик отдаёт текст потоком).

    Время считается по фактическому вычерпыванию генератора: пока ход не
    прочитал последний кусок, рассказчик ещё «работает».
    """
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        generator = function(*args, **kwargs)

        async def counted():
            started = time.perf_counter()
            try:
                async for chunk in generator:
                    yield chunk
            finally:
                _add(label, time.perf_counter() - started)
        return counted()
    return wrapper


def record_turn(request_id, text, total, layers, sizes):
    if not _TIMES_PATH:
        return
    line = json.dumps({'turn': _TURN['number'], 'kind': _TURN['kind'], 'request_id': request_id,
                       'text': text, 'server_ms': round(total * 1000, 2),
                       'layers': {key: round(value * 1000, 2) for key, value in layers.items()},
                       'sizes': sizes}, ensure_ascii=False)
    with _LOCK:
        with open(_TIMES_PATH, 'a', encoding='utf-8') as stream:
            stream.write(line + '\n')
            stream.flush()


def kind_of(text):
    """Вид хода по реплике — чтобы в отчёте сравнивать одинаковые ходы."""
    low = text.casefold()
    if low.startswith(('проверяю', 'ищу', 'проверка', 'проверить')):
        return 'check'
    if low.startswith(('иду', 'перехожу', 'двигаюсь')):
        return 'travel'
    if low.startswith(('говорю', 'поговор', 'расспраш', 'привет')):
        return 'talk'
    if low.startswith(('осматр', 'огляд', 'опиши')):
        return 'observe'
    if low.startswith(('атак', 'бью', 'начина', 'заканчива', 'рывок')):
        return 'combat'
    return 'other'


def instrument(app):
    """Вешает замер на объекты ЭТОГО приложения, не на классы движка целиком."""
    from solo import prototype_gm
    from solo.command_engine import CommandEngine
    from solo.game import Game, SceneCompiler
    from solo.memory import Memory
    from solo.nat20_api import Nat20GameEngine
    from solo.storage import Store

    store = app.state.store
    game = app.state.game
    llm = game.llm

    # Граница хода: начало и конец одного player turn.
    original_play = game.play_command

    @functools.wraps(original_play)
    async def play_command(campaign, request_id, text, expected_version, **kwargs):
        _TURN['number'] += 1
        _TURN['kind'] = kind_of(text)
        with _LOCK:
            ACC.clear()
            _SIZES.update(worker_in=0, worker_out=0, calls=0)
        started = time.perf_counter()
        try:
            return await original_play(campaign, request_id, text, expected_version, **kwargs)
        finally:
            total = time.perf_counter() - started
            record_turn(request_id, text, total, dict(ACC), dict(_SIZES))
    game.play_command = play_command

    # Хранилище: чтение состояния, транзакция хода, память и разговор.
    store.campaign = timed('state_read', store.campaign)
    store.open_question = timed('question', store.open_question)
    store.request = timed('request_lookup', store.request)
    store.converse = timed('converse', store.converse)
    store.record_scenario = timed('scenario_write', store.record_scenario)
    store.engine_event = timed('engine_event', store.engine_event)
    store.narrative = timed('narrative_write', store.narrative)
    if isinstance(getattr(store, 'memory', None), Memory):
        store.memory.ingest = timed('memory_ingest', store.memory.ingest)
        store.memory.retrieve = timed('memory_retrieve', store.memory.retrieve)

    # Пакеты для интерпретатора и рассказчика.
    compiler = game.compiler
    compiler.command_context = timed('context', compiler.command_context)
    compiler.engine_context = timed('memory_context', compiler.engine_context)
    compiler.command_narrator = timed('narrator_packet', compiler.command_narrator)
    compiler.command_lookup = timed('catalog_lookup', compiler.command_lookup)

    # Модель (офлайн-мок): разбор реплики и рассказ.
    if hasattr(llm, 'interpret_command'):
        llm.interpret_command = timed_async('interpret', llm.interpret_command)
    if hasattr(llm, 'narrate_scene'):
        llm.narrate_scene = timed_async_gen('narrate', llm.narrate_scene)

    # Ход без механики: наблюдение и разговор. game.py импортирует функцию
    # внутри вызова, поэтому подмена модуля работает и не трогает классы.
    prototype_gm.present_intent = timed_async('present', prototype_gm.present_intent)

    # Движок: сам вызов, подпроцесс воркера и проекции состояния.
    Nat20GameEngine.execute = timed('engine_execute', Nat20GameEngine.execute)
    Nat20GameEngine.capabilities = staticmethod(timed('capabilities', Nat20GameEngine.capabilities))
    Nat20GameEngine.snapshot = staticmethod(timed('engine_snapshot', Nat20GameEngine.snapshot))
    CommandEngine.visible = staticmethod(timed('projection', CommandEngine.visible))
    if hasattr(CommandEngine, 'snapshot'):
        CommandEngine.snapshot = staticmethod(timed('projection', CommandEngine.snapshot))

    original_worker = Nat20GameEngine._worker

    @functools.wraps(original_worker)
    def worker(payload):
        # Размер входа и выхода — вторая половина ответа «где растёт»: если
        # время воркера растёт, а байты нет, дело в старте процесса, а не в данных.
        together = len(json.dumps(payload, ensure_ascii=False))
        started = time.perf_counter()
        try:
            result = original_worker(payload)
        finally:
            _add('worker', time.perf_counter() - started)
            _SIZES['worker_in'] += together
            _SIZES['calls'] += 1
        _SIZES['worker_out'] += len(json.dumps(result, ensure_ascii=False))
        return result
    Nat20GameEngine._worker = staticmethod(worker)


def main() -> int:
    import uvicorn

    from solo.app import create_app
    from solo.preview_mock import PreviewMock

    port = int(os.environ['SOLO_CHILD_PORT'])
    stop = os.environ['SOLO_CHILD_STOP']
    app = create_app(os.environ['SOLO_CHILD_DB'], PreviewMock(), local_port=port,
                     experimental=True, offline_mock=True)
    instrument(app)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error',
                                           access_log=False, timeout_graceful_shutdown=5))

    def serve():
        try:
            server.run()
        except BaseException as exc:  # noqa: BLE001 - печатаем причину смерти
            print('RUNERR', type(exc).__name__, str(exc)[:160], flush=True)

    threading.Thread(target=serve, daemon=True).start()
    deadline = time.monotonic() + 60
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    print('READY', server.started, os.getpid(), flush=True)
    if not server.started:
        return 2
    try:
        sys.stdin.close()
    except Exception:
        pass
    while not os.path.exists(stop):
        time.sleep(.05)
    server.should_exit = True
    time.sleep(.1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
